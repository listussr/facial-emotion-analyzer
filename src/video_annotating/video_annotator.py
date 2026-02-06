import asyncio
import contextlib
import json
import base64
import cv2
import numpy as np
import logging
from confluent_kafka import Consumer, Producer
from typing import Dict
from .buffers import SyncBuffer

class VideoAnnotator(object):
    def __init__(self, ttl_frames: int, ttl_annotations: int, cleanup_interval: int, kafka_bootstrap: str, frames_topic: str, 
                 annotations_topic: str, output_topic: str):
        """
        #### Аннотатор для соединения кадров с аналитикой.

        Предназначен для сбора кадров и статистики и для последующей их синхронизации.

        Чтение топиков кафки, аннотация и запись в результирующий топик производятся асинхронно.

        :param ttl_frames: Время жизни кадров в буфере синхронизации.
        :type ttl_frames: int
        :param ttl_annotations: Время жизни аннотаций в буфере синхронизации.
        :type ttl_annotations: int
        :param cleanup_interval: Интервал очистки кадров с истёкшим ttl из буферов.
        :type cleanup_interval: int
        :param kafka_bootstrap: Название bootstrap сервера кафки.
        :type kafka_bootstrap: str
        :param frames_topic: Название топика с кадрами видеопотока.
        :type frames_topic: str
        :param annotations_topic: Название топика со аннотациями кадров.
        :type annotations_topic: str
        :param output_topic: Название топика с аннотированными кадрами.
        :type output_topic: str
        """
        self.sync_buffer = SyncBuffer(ttl_frames, ttl_annotations)

        self.cleanup_interval = cleanup_interval

        self._running = False

        self.output_topic = output_topic

        self._initialize_frames(kafka_bootstrap, frames_topic)
        self._initialize_annotations(kafka_bootstrap, annotations_topic)
        self._initialize_producer(kafka_bootstrap)

    def _initialize_frames(self, kafka_bootstrap: str, frames_topic: str):
        """
        #### Подключение к топику с кадрами.
        """
        self.frames_consumer = Consumer({
            'bootstrap.servers': kafka_bootstrap,
            'group.id': 'visualizer-raw',
            'auto.offset.reset': 'latest',
            'fetch.message.max.bytes': 10 * 1024 * 1024
        })
        self.frames_consumer.subscribe([frames_topic])
        logging.info("Listening to frames topic in Annotator")

    def _initialize_annotations(self, kafka_bootstrap: str, annotations_topic: str):
        """
        #### Подключение к топику с аннотациями.
        """
        self.annotation_consumer = Consumer({
            'bootstrap.servers': kafka_bootstrap,
            'group.id': 'visualizer-analytics',
            'auto.offset.reset': 'latest'
        })
        self.annotation_consumer.subscribe([annotations_topic])
        logging.info("Listening to annotation topic in Annotator")

    def _initialize_producer(self, kafka_bootstrap: str):
        """
        #### Подключение к топику с итоговыми кадрами.
        """
        self.producer = Producer({
                'bootstrap.servers': kafka_bootstrap,
                'compression.type': 'lz4'
            })
        logging.info(f"Initialized `{self.output_topic}` topic for annotated frames")


    async def _cleanup_loop(self):
        """
        #### Цикл очистки буферов с кадрами и аннотациями.
        """
        logging.info("Started cleanup")
        while self._running:
            self.sync_buffer.cleanup()
            await asyncio.sleep(self.cleanup_interval)

    def _annotate_and_encode(self, frame, annotation):
        """
        #### Утилитная функция для использования в `run_in_executor()`.
        """
        annotated = self._draw_annotations(frame, annotation)
        jpeg = self._encode_frame_to_jpeg(annotated)
        return jpeg

    def _draw_annotations(self, frame: np.ndarray, annotation: Dict) -> np.ndarray:
        """
        #### Метод отрисовки аннотаций на кадре.
        """
        annotated = frame.copy()
        for face in annotation.get("faces", []):
            x1, y1, x2, y2 = map(int, face["bbox"])
            emotion = face["emotion"]
            score = max(face["emotion_scores"][0])
            face_id = face.get('face_id', 'Unknown')
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
            label = f"{emotion}: {score:.2f}\n User: {str(face_id)}"
            cv2.putText(annotated, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        return annotated

    def _encode_frame_to_jpeg(self, frame: np.ndarray) -> bytes:
        """
        #### Утилитная функция для использования в `run_in_executor()`.
        """
        success, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            raise ValueError("Failed to encode frame")
        return buffer.tobytes()

    async def _handle_pair(self, frame: Dict, annotation: Dict):
        """
        #### Метод для сопоставления кадра и аннотаций с одним идентификатором кадра.
        """
        try:
            loop = asyncio.get_running_loop()

            jpeg_bytes = await loop.run_in_executor(
                None,
                self._annotate_and_encode,
                frame,
                annotation
            )
            camera_id = annotation["camera_id"]
            frame_id = annotation["frame_id"]

            message = {
                "camera_id": camera_id,
                "frame_id": frame_id,
                "annotated_frame": base64.b64encode(jpeg_bytes).decode('utf-8')
            }
            self.producer.produce(
                self.output_topic,
                key=camera_id.encode(),
                value=json.dumps(message).encode('utf-8')
            )
            self.producer.poll(0)
            logging.info(f"Published annotated frame for {camera_id}")

        except Exception as e:
            logging.error(f"Error handling pair: {e}", exc_info=True)

    def _decode_frame(self, payload):
        """
        #### Утилитная функция для использования в `run_in_executor()`.
        """
        jpeg_image = base64.b64decode(payload["frame_data"])
        return cv2.imdecode(np.frombuffer(jpeg_image, np.uint8), cv2.IMREAD_COLOR)

    async def _process_frame(self, msg: bytes):
        """
        #### Обработка одного кадра.
        """
        try:
            loop = asyncio.get_running_loop()
            payload = json.loads(msg.decode("utf-8"))

            frame = await loop.run_in_executor(
                None,
                self._decode_frame,
                payload
            )

            result = self.sync_buffer.add_frame(payload["frame_id"], frame)
            if result:
                await self._handle_pair(*result)
        except Exception as e:
            logging.error(f"Error in processing frame: {e}")

    async def _frames_processing(self):
        """
        #### Цикл работы с кадрами.
        """
        logging.info("Started frames processing")
        while self._running:
            msg = self.frames_consumer.poll(0.05)

            if msg is None:
                await asyncio.sleep(0)
                continue

            if msg.error():
                logging.error(msg.error())
                continue

            await self._process_frame(msg.value())

    async def _process_annotation(self, msg: bytes):
        """
        #### Обработка одной аннотации.
        """
        try:
            analytics = json.loads(msg.decode('utf-8'))
            frame_id = analytics["frame_id"]
            result = self.sync_buffer.add_annotation(frame_id, analytics)
            if result:
                await self._handle_pair(*result)
        except Exception as e:
            logging.error(f"Error in processing annotation: {e}")
                

    async def _annotation_processing(self):
        """
        #### Цикл работы с аннотациями.
        """
        logging.info("Started annotation processing")
        while self._running:
            msg = self.annotation_consumer.poll(0.05)

            if msg is None:
                await asyncio.sleep(0)
                continue

            if msg.error():
                logging.error(msg.error())
                continue

            await self._process_annotation(msg.value())

    async def run(self):
        """
        #### Запуск асинхронного пайплайна аннотации кадров.

        Асинхронно реализуется получение кадров, получение аннотаций и процесс фоновой очистки буфера.
        """
        self._running = True
        self._frames_task = asyncio.create_task(self._frames_processing())
        self._annotation_task = asyncio.create_task(self._annotation_processing())
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self):
        """
        #### Остановка цикла аннотации кадров.
        """
        logging.info("Finishing all annotator's tasks")
        self._running = False

        for task in (self._frames_task, self._annotation_task, self._cleanup_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        self.frames_consumer.close()
        self.annotation_consumer.close()

        if self.producer:
            self.producer.flush(5)
