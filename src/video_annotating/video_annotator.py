"""Серверный аннотатор: рамки и эмоции поверх кадров для записи и экспорта."""

import asyncio
import contextlib
import json
import logging
import cv2
import msgpack
import numpy as np

from concurrent.futures import ThreadPoolExecutor
from typing import Dict
from typing import List
from typing import Tuple

from confluent_kafka import Consumer
from confluent_kafka import Producer

from .buffers import SyncBuffer

COLORS = {
    "anger": (255, 0, 0),
    "contempt": (128, 0, 128),
    "disgust": (0, 128, 0),
    "fear": (128, 128, 0),
    "happy": (255, 255, 0),
    "neutral": (128, 128, 128),
    "sad": (0, 0, 255),
    "surprise": (255, 165, 0)
}


def draw_faces(frame: np.ndarray, faces: List[Dict], details: bool = False) -> np.ndarray:
    """
    Рамки и подписи эмоций; неуверенная оценка рисуется серой рамкой с подписью undetermined.

    Args:
        frame   (np.ndarray)      : Кадр BGR, рисуется на месте.
        faces   (List[Dict])      : Лица аналитики с bbox, emotion и emotion_scores.
        details (bool, optional)  : Подпись оценок лица и голоса под рамкой. Defaults to False.

    Returns:
        np.ndarray: Тот же кадр.
    """
    for face in faces:
        x1, y1, x2, y2 = map(int, face["bbox"])
        emotion        = face.get("emotion")
        label_y        = max(y1 - 10, 16)

        if emotion is None or emotion not in COLORS:
            color = (200, 200, 200)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1)
            if face.get("emotion_scores"):
                cv2.putText(frame, "undetermined", (x1, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        else:
            color = COLORS[emotion]
            score = max(face["emotion_scores"])
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, f"{emotion}: {score:.2f}", (x1, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        if details:
            parts = [f"face: {face.get('face_emotion') or '-'}", f"voice: {face.get('voice_emotion') or '-'}"]
            cv2.putText(frame, "  ".join(parts), (x1, y2 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1)
    return frame


def _serialize_frame_message(camera_id: str, frame_id: str, jpeg_data: bytes) -> bytes:
    return msgpack.packb({
        "camera_id": camera_id,
        "frame_id": frame_id,
        "annotated_frame": jpeg_data,
    })


def _deserialize_frame_message(raw: bytes) -> Tuple[str, str, np.ndarray]:
    payload    = msgpack.unpackb(raw, raw=False)
    jpeg_bytes = payload["frame_data"]
    frame      = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
    return payload["frame_id"], payload.get("camera_id", ""), frame


class VideoAnnotator(object):
    """
    Аннотатор кадров по аналитике.

    Кадры и аналитика сопоставляются по frame_id в буфере с TTL. Чтение Kafka и обработка разнесены:
    reader кладёт сообщения в очередь, worker рисует и кодирует в пуле потоков.
    """

    def __init__(self, ttl_frames: int, ttl_annotations: int, cleanup_interval: int, kafka_bootstrap: str, frames_topic: str,
                 annotations_topic: str, output_topic: str, queue_maxsize: int = 1000):
        cv2.setNumThreads(1)
        self._executor = ThreadPoolExecutor(max_workers=4)

        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)

        self.sync_buffer = SyncBuffer(ttl_frames, ttl_annotations)

        self.cleanup_interval = cleanup_interval

        self._running = False

        self.output_topic      = output_topic
        self._frames_topic     = frames_topic
        self._annotation_topic = annotations_topic

        self._initialize_consumers(kafka_bootstrap, frames_topic, annotations_topic)
        self._initialize_producer(kafka_bootstrap)

    def _initialize_consumers(self, kafka_bootstrap: str, frames_topic: str, annotations_topic: str):
        self.consumer = Consumer({
            "bootstrap.servers": kafka_bootstrap,
            "group.id": "visualizer-raw",
            "auto.offset.reset": "latest",
            "fetch.message.max.bytes": 10 * 1024 * 1024
        })
        self.consumer.subscribe([frames_topic, annotations_topic])
        logging.info("Listening to frames topic in Annotator")

    def _initialize_producer(self, kafka_bootstrap: str):
        self.producer = Producer({
                "bootstrap.servers": kafka_bootstrap,
                "compression.type": "lz4",
                "linger.ms": 15,
                "batch.size": 65536,
                "acks": 0,
            })
        logging.info(f"Initialized `{self.output_topic}` topic for annotated frames")

    def _blocking_consume(self, num_messages: int = 50, timeout: float = 0.05):
        return self.consumer.consume(num_messages=num_messages, timeout=timeout)

    async def _kafka_reader_loop(self):
        """Чтение Kafka в пуле потоков; при переполнении очереди выбрасываются старые сообщения."""
        logging.info("Started kafka reader loop")
        loop = asyncio.get_running_loop()

        while self._running:
            msgs = await loop.run_in_executor(
                self._executor,
                self._blocking_consume,
                50,
                0.05
            )

            for msg in msgs:
                if msg.error():
                    logging.error(f"Kafka consumer error: {msg.error()}")
                    continue
                while self._queue.full():
                    try:
                        self._queue.get_nowait()
                        self._queue.task_done()
                        logging.debug("Queue full - dropped oldest message")
                    except asyncio.QueueEmpty:
                        pass
                self._queue.put_nowait((msg.topic(), msg.value()))

    async def _worker_loop(self):
        logging.info("Started worker loop")

        while self._running:
            try:
                topic, value = await asyncio.wait_for(
                    self._queue.get(), timeout=0.1
                )
            except asyncio.TimeoutError:
                continue

            if topic == self._frames_topic:
                await self._process_frame(value)
            else:
                await self._process_annotation(value)

            self._queue.task_done()

    async def _cleanup_loop(self):
        logging.info("Started cleanup")
        while self._running:
            self.sync_buffer.cleanup()
            await asyncio.sleep(self.cleanup_interval)

    def _annotate_and_encode(self, frame, annotation):
        annotated = self._draw_annotations(frame, annotation)
        return self._encode_frame_to_jpeg(annotated)

    def _draw_annotations(self, frame: np.ndarray, annotation: Dict) -> np.ndarray:
        return draw_faces(frame, annotation.get("faces", []))

    def _encode_frame_to_jpeg(self, frame: np.ndarray) -> bytes:
        success, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            raise ValueError("Failed to encode frame")
        return buffer.tobytes()

    async def _handle_pair(self, frame: np.ndarray, annotation: Dict):
        try:
            loop = asyncio.get_running_loop()

            jpeg_bytes = await loop.run_in_executor(
                self._executor,
                self._annotate_and_encode,
                frame,
                annotation
            )
            camera_id = annotation["camera_id"]
            frame_id  = annotation["frame_id"]

            message = _serialize_frame_message(camera_id, frame_id, jpeg_bytes)

            self.producer.produce(
                self.output_topic,
                key=camera_id.encode(),
                value=message
            )
            self.producer.poll(0)

        except Exception as e:
            logging.error(f"Error handling pair: {e}", exc_info=True)

    def _decode_frame(self, payload: bytes):
        try:
            return _deserialize_frame_message(payload)
        except Exception as e:
            logging.error(f"Failed to deserialize frame: {e}")
            return None, None, None

    async def _process_frame(self, msg: bytes):
        try:
            loop = asyncio.get_running_loop()

            frame_id, camera_id, frame = await loop.run_in_executor(
                self._executor,
                self._decode_frame,
                msg
            )

            if frame is None:
                return

            result = self.sync_buffer.add_frame(frame_id, frame)
            if result:
                await self._handle_pair(*result)
        except Exception as e:
            logging.error(f"Error in processing frame: {e}")

    async def _process_annotation(self, msg: bytes):
        try:
            analytics = json.loads(msg.decode("utf-8"))
            frame_id  = analytics["frame_id"]
            result    = self.sync_buffer.add_annotation(frame_id, analytics)
            if result:
                await self._handle_pair(*result)
        except Exception as e:
            logging.error(f"Error in processing annotation: {e}")

    async def run(self):
        """Запуск чтения, обработки и фоновой очистки буфера."""
        self._running      = True
        self._reader_task  = asyncio.create_task(self._kafka_reader_loop())
        self._worker_task  = asyncio.create_task(self._worker_loop())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self):
        logging.info("Finishing all annotator's tasks")
        self._running = False

        for task in (self._reader_task, self._worker_task, self._cleanup_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        self.consumer.close()

        if self.producer:
            self.producer.flush(5)
