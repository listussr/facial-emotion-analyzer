"""
Генерация тестового ролика для проверки синхронизации аудио и видео.

Каждую секунду — белая вспышка на кадре и одновременно звуковой бип 1 кГц.
По расхождению `pts_ms` вспышек и бипов в Kafka (см. check_audio_stream.py)
проверяется синхронизация продюсера.

Запуск (из корня проекта):
    python scripts/make_sync_test.py
    python scripts/make_sync_test.py --out data/sync_test.mp4 --seconds 30 --fps 30
"""

import argparse
import av
import numpy as np

from fractions import Fraction
from pathlib import Path

FLASH_S = 0.05
BEEP_HZ = 1000.0


def make(out: Path, seconds: int, fps: int, width: int, height: int, audio_rate: int) -> None:
    """
    Ролик с вспышкой и бипом длиной FLASH_S в начале каждой секунды.

    Args:
        out        (Path): Файл mp4.
        seconds    (int) : Длительность, с.
        fps        (int) : Частота кадров.
        width      (int) : Ширина кадра.
        height     (int) : Высота кадра.
        audio_rate (int) : Частота дискретизации звука, Гц.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(out), mode="w") as container:
        vstream                                        = container.add_stream("libx264", rate=fps)
        vstream.width, vstream.height, vstream.pix_fmt = width, height, "yuv420p"
        astream                                        = container.add_stream("aac", rate=audio_rate)
        astream.layout                                 = "mono"

        black = np.zeros((height, width, 3), np.uint8)
        white = np.full((height, width, 3), 255, np.uint8)
        for i in range(seconds * fps):
            t               = i / fps
            img             = white if (t % 1.0) < FLASH_S else black
            frame           = av.VideoFrame.from_ndarray(img, format="rgb24")
            frame.pts       = i
            frame.time_base = Fraction(1, fps)
            for packet in vstream.encode(frame):
                container.mux(packet)

        block = 1024
        total = seconds * audio_rate
        for start in range(0, total, block):
            n                 = min(block, total - start)
            t                 = (start + np.arange(n)) / audio_rate
            gate              = (t % 1.0) < FLASH_S
            samples           = (0.8 * np.sin(2 * np.pi * BEEP_HZ * t) * gate).astype(np.float32)
            frame             = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="fltp", layout="mono")
            frame.sample_rate = audio_rate
            frame.pts         = start
            frame.time_base   = Fraction(1, audio_rate)
            for packet in astream.encode(frame):
                container.mux(packet)

        for stream in (vstream, astream):
            for packet in stream.encode(None):
                container.mux(packet)
    print(f"wrote {out} ({seconds}s, {fps} fps, beep+flash every 1s)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/sync_test.mp4")
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--audio-rate", type=int, default=48000)
    args = ap.parse_args()
    make(Path(args.out), args.seconds, args.fps, args.width, args.height, args.audio_rate)


if __name__ == "__main__":
    main()
