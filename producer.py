"""
Запуск продюсера медиапотока (видео и/или аудио) в Kafka.

Примеры (из корня проекта):
    python producer.py
    python producer.py --source data/sync_test.mp4 --modalities video,audio --stop-on-end
    python producer.py --source 0 --modalities video,audio --audio-device 1
    python producer.py --source mic --modalities audio
    python producer.py --source rtsp://user:pass@host/stream --modalities video,audio
    python producer.py --list-devices

Остановка — Ctrl+C (или сама, по концу файла с --stop-on-end).
"""

import argparse
import logging
import sys
import time

from typing import List
from typing import Optional
from typing import Union

from src.video_producing import CameraConfig
from src.video_producing import CameraProducer

DEFAULT_SOURCE = r"src\test_videos\film.mp4"


def _source_arg(value: str) -> Union[str, int]:
    return int(value) if value.isdigit() else value


def _device_arg(value: str) -> Union[str, int]:
    return int(value) if value.isdigit() else value


def _modalities_arg(value: str) -> tuple:
    return tuple(m.strip() for m in value.split(",") if m.strip())


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    src = ap.add_argument_group("источник")
    src.add_argument("--source", type=_source_arg, default=DEFAULT_SOURCE,
                     help="Файл, URL (rtsp://, http://), индекс камеры или 'mic'.")
    src.add_argument("--camera-id", default="cam_01")
    src.add_argument("--modalities", type=_modalities_arg, default=("video",),
                     help="Через запятую: video, audio или video,audio.")
    src.add_argument("--strict", action="store_true",
                     help="Не запускаться, если источник не даёт какую-то из модальностей.")
    src.add_argument("--stop-on-end", action="store_true",
                     help="Остановиться в конце файла (иначе — проигрывать по кругу).")
    src.add_argument("--no-pacing", action="store_true",
                     help="Отдавать файл так быстро, как читается (без темпа реального времени).")

    video = ap.add_argument_group("видео")
    video.add_argument("--frame-rate", type=int, default=20)
    video.add_argument("--quality", type=int, default=80)
    video.add_argument("--max-width", type=int, default=640)
    video.add_argument("--max-height", type=int, default=480)

    audio = ap.add_argument_group("аудио")
    audio.add_argument("--audio-device", type=_device_arg, default=None,
                       help="Индекс или имя микрофона (см. --list-devices).")
    audio.add_argument("--audio-rate", type=int, default=16000)
    audio.add_argument("--audio-channels", type=int, default=1)
    audio.add_argument("--audio-chunk-ms", type=int, default=100)
    audio.add_argument("--list-devices", action="store_true",
                       help="Показать микрофоны и выйти.")

    kafka = ap.add_argument_group("kafka")
    kafka.add_argument("--kafka", default="localhost:9092", help="bootstrap.servers")
    kafka.add_argument("--video-topic", default="raw-video-frames")
    kafka.add_argument("--audio-topic", default="raw-audio-chunks")
    kafka.add_argument("--events-topic", default="session-events")
    kafka.add_argument("--partition", type=int, default=None,
                       help="Явная партиция (по умолчанию — по ключу camera_id).")
    kafka.add_argument("--max-message-size", type=int, default=5_000_000)

    ap.add_argument("--log-level", default="INFO")
    return ap


def build_config(args: argparse.Namespace) -> CameraConfig:
    return CameraConfig(
        camera_id=args.camera_id,
        source=args.source,
        kafka_servers=args.kafka,
        partition=args.partition,
        modalities=args.modalities,
        strict_modalities=args.strict,
        stop_on_end=args.stop_on_end,
        realtime_pacing=not args.no_pacing,
        frame_rate=args.frame_rate,
        quality=args.quality,
        max_width=args.max_width,
        max_height=args.max_height,
        max_message_size=args.max_message_size,
        audio_device=args.audio_device,
        audio_sample_rate=args.audio_rate,
        audio_channels=args.audio_channels,
        audio_chunk_ms=args.audio_chunk_ms,
        video_topic=args.video_topic,
        audio_topic=args.audio_topic,
        events_topic=args.events_topic,
        reconnect_timeout=5,
    )


def list_devices() -> None:
    from src.video_producing.sources.mic_source import list_input_devices
    devices = list_input_devices()
    if not devices:
        print("Микрофоны не найдены")
    for d in devices:
        print(f"[{d['index']}] {d['name']}  (channels={d['channels']}, "
              f"default_rate={d['default_samplerate']:.0f})")


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_devices:
        list_devices()
        return 0

    from src.logging_config import setup_logger
    setup_logger("producing")
    logging.getLogger().setLevel(args.log_level.upper())

    try:
        config = build_config(args)
    except ValueError as e:
        print(f"Некорректные параметры: {e}", file=sys.stderr)
        return 2

    producer = CameraProducer(config)
    producer.start()
    try:
        while producer.thread is not None and producer.thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        logging.info("Interrupted by user")
    finally:
        producer.stop()

    return 0 if producer.end_reason in ("eof", "stopped") else 1


if __name__ == "__main__":
    sys.exit(main())
