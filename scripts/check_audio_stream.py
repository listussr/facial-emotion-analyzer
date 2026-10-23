"""
Проверка медиапотоков продюсера в Kafka для одной сессии.

Читает видео- и аудиотопик с начала, отбирает сообщения по `camera_id` и:
  - проверяет непрерывность `seq` и монотонность `pts_ms` по каждому потоку;
  - склеивает аудиочанки в WAV;
  - с `--sync` (для ролика из make_sync_test.py) находит вспышки на кадрах
    и бипы в аудио и печатает расхождение по `pts_ms`.

Запуск (из корня проекта), после прогона продюсера:
    python scripts/check_audio_stream.py --camera-id cam_01
    python scripts/check_audio_stream.py --camera-id cam_01 --sync --wav out.wav
"""

import argparse
import sys
import time
import uuid
import wave
import cv2
import msgpack
import numpy as np

from pathlib import Path
from typing import Dict
from typing import List

from confluent_kafka import Consumer
from confluent_kafka import KafkaError

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def consume(bootstrap: str, topics: List[str], camera_id: str, idle_s: float) -> Dict[str, List[dict]]:
    """
    Сообщения сессии из топиков с начала; таймер тишины стартует с первого сообщения,
    потому что вход новой группы в Kafka сам занимает несколько секунд.

    Args:
        bootstrap (str)      : Адрес Kafka.
        topics    (List[str]): Топики.
        camera_id (str)      : Идентификатор сессии.
        idle_s    (float)    : Сколько ждать новых сообщений, с.

    Returns:
        Dict[str, List[dict]]: Сообщения по топикам.
    """
    consumer = Consumer({
        "bootstrap.servers": bootstrap,
        "group.id": f"check-audio-{uuid.uuid4()}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        "fetch.message.max.bytes": 10 * 1024 * 1024,
    })
    consumer.subscribe(topics)
    out: Dict[str, List[dict]] = {t: [] for t in topics}
    key      = camera_id.encode("utf-8")
    started  = time.time()
    last_msg = None
    try:
        while True:
            now = time.time()
            if last_msg is None and now - started > max(30.0, idle_s):
                break
            if last_msg is not None and now - last_msg > idle_s:
                break
            msg = consumer.poll(0.5)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    print(f"kafka error: {msg.error()}", file=sys.stderr)
                continue
            last_msg = time.time()
            if msg.key() != key:
                continue
            payload = msgpack.unpackb(msg.value(), raw=False)
            if payload.get("camera_id") == camera_id:
                out[msg.topic()].append(payload)
    finally:
        consumer.close()
    return out


def check_stream(name: str, msgs: List[dict]) -> None:
    if not msgs:
        print(f"[{name}] no messages")
        return
    if "seq" not in msgs[0]:
        print(f"[{name}] {len(msgs)} messages without seq (schema v1?)")
        return
    msgs.sort(key=lambda m: m["seq"])
    seqs     = [m["seq"] for m in msgs]
    pts      = [m["pts_ms"] for m in msgs]
    gaps     = [(a, b) for a, b in zip(seqs, seqs[1:]) if b != a + 1]
    non_mono = sum(1 for a, b in zip(pts, pts[1:]) if b <= a)
    print(f"[{name}] {len(msgs)} msgs, seq {seqs[0]}..{seqs[-1]}, gaps={len(gaps)}, "
          f"non-monotonic pts={non_mono}, pts {pts[0]}..{pts[-1]} ms, "
          f"modalities={msgs[0].get('modalities')}")
    if gaps[:5]:
        print(f"  first gaps: {gaps[:5]}")


def write_wav(path: Path, chunks: List[dict]) -> np.ndarray:
    chunks         = sorted(chunks, key=lambda m: m["seq"])
    rate, channels = chunks[0]["sample_rate"], chunks[0]["channels"]
    pcm            = b"".join(c["audio_data"] for c in chunks)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    samples = sum(c["num_samples"] for c in chunks)
    print(f"[audio] wrote {path}: {samples / rate:.2f}s, {rate} Hz, {channels} ch")
    return np.frombuffer(pcm, np.int16)


def flash_times(frames: List[dict]) -> List[int]:
    times, prev_bright = [], False
    for m in sorted(frames, key=lambda m: m["pts_ms"]):
        img    = cv2.imdecode(np.frombuffer(m["frame_data"], np.uint8), cv2.IMREAD_GRAYSCALE)
        bright = img is not None and img.mean() > 128
        if bright and not prev_bright:
            times.append(m["pts_ms"])
        prev_bright = bright
    return times


def beep_times(chunks: List[dict], win_ms: int = 5) -> List[int]:
    times, prev_loud = [], False
    for c in sorted(chunks, key=lambda m: m["pts_ms"]):
        x = np.frombuffer(c["audio_data"], np.int16).astype(np.float32)
        if c["channels"] > 1:
            x = x.reshape(-1, c["channels"]).mean(axis=1)
        win = max(1, c["sample_rate"] * win_ms // 1000)
        for i in range(0, len(x) - win + 1, win):
            loud = np.sqrt(np.mean(x[i:i + win] ** 2)) > 3000
            if loud and not prev_loud:
                times.append(c["pts_ms"] + i * 1000 // c["sample_rate"])
            prev_loud = loud
    return times


def check_sync(frames: List[dict], chunks: List[dict], frame_rate: float) -> None:
    flashes, beeps = flash_times(frames), beep_times(chunks)
    print(f"[sync] flashes={len(flashes)} beeps={len(beeps)}")
    if not flashes or not beeps:
        return
    deltas = []
    for f in flashes:
        b = min(beeps, key=lambda b: abs(b - f))
        if abs(b - f) < 500:
            deltas.append(b - f)
    if not deltas:
        print("[sync] no matching flash/beep pairs")
        return
    d   = np.array(deltas)
    tol = 1000 / frame_rate + 100
    print(f"[sync] audio - video: mean={d.mean():.1f} ms, median={np.median(d):.1f} ms, "
          f"max|delta|={np.abs(d).max():.1f} ms, pairs={len(d)}; tolerance {tol:.0f} ms -> "
          f"{'OK' if np.abs(d).max() <= tol else 'FAIL'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera-id", required=True)
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--video-topic", default="raw-video-frames")
    ap.add_argument("--audio-topic", default="raw-audio-chunks")
    ap.add_argument("--idle", type=float, default=5.0, help="Остановиться после N секунд без сообщений.")
    ap.add_argument("--wav", default=None, help="Куда сохранить склеенное аудио.")
    ap.add_argument("--sync", action="store_true", help="Проверить синхронизацию по вспышкам и бипам.")
    args = ap.parse_args()

    data           = consume(args.bootstrap, [args.video_topic, args.audio_topic], args.camera_id, args.idle)
    frames, chunks = data[args.video_topic], data[args.audio_topic]

    check_stream("video", frames)
    check_stream("audio", chunks)
    if chunks:
        write_wav(Path(args.wav or f"{args.camera_id}_audio.wav"), chunks)
    if args.sync and frames and chunks:
        check_sync(frames, chunks, float(frames[0].get("frame_rate") or 10))


if __name__ == "__main__":
    main()
