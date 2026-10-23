"""Серверный аннотатор кадров для записи и экспорта; работает в фоне с пониженным приоритетом."""

import asyncio
import logging
import os

from src.video_annotating.video_annotator import VideoAnnotator
from src.cpu_threads import limit_opencv_threads
from src.cpu_threads import lower_priority
from src.logging_config import setup_logger
from src.service_health import heartbeat_from_env

setup_logger("visualizer")
limit_opencv_threads()
lower_priority()


async def main():
    heartbeat = heartbeat_from_env("visualizer")
    annotator = VideoAnnotator(
        ttl_frames=300,
        ttl_annotations=60,
        cleanup_interval=5,
        kafka_bootstrap=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        frames_topic=os.getenv("TOPIC_RAW", "raw-video-frames"),
        annotations_topic=os.getenv("TOPIC_ANALYTICS", "vision-analytics"),
        output_topic=os.getenv("TOPIC_ANNOTATED", "annotated-video"),
    )

    await annotator.run()
    heartbeat.ready()

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logging.info("Stopping annotator...")
        await annotator.stop()
    finally:
        heartbeat.stop()


if __name__ == "__main__":
    asyncio.run(main())
