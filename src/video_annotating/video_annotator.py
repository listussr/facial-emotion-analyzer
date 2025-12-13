# visualizer.py (обновлённый)

import json
import base64
import cv2
import numpy as np
import logging
from confluent_kafka import Consumer, Producer
import time
from .buffers import SyncBuffer

class Visualizer:
    def __init__(self, kafka_bootstrap: str, raw_topic: str, analytics_topic: str, output_topic: str = None):
        self.raw_topic = raw_topic
        self.analytics_topic = analytics_topic
        self.output_topic = output_topic

        self.sync_buffer = SyncBuffer(ttl_seconds=3.0, max_size=100)

        self.raw_consumer = Consumer({
            'bootstrap.servers': kafka_bootstrap,
            'group.id': 'visualizer-raw',
            'auto.offset.reset': 'latest',
            'fetch.message.max.bytes': 10 * 1024 * 1024
        })
        self.raw_consumer.subscribe([raw_topic])

        self.analytics_consumer = Consumer({
            'bootstrap.servers': kafka_bootstrap,
            'group.id': 'visualizer-analytics',
            'auto.offset.reset': 'latest'
        })
        self.analytics_consumer.subscribe([analytics_topic])

        if output_topic:
            self.producer = Producer({
                'bootstrap.servers': kafka_bootstrap,
                'compression.type': 'lz4'
            })
        else:
            self.producer = None

    def _draw_annotations(self, frame: np.ndarray, analytics: dict) -> np.ndarray:
        annotated = frame.copy()
        cv2.putText(annotated, "ANNOTATED", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        for face in analytics.get("faces", []):
            x1, y1, x2, y2 = map(int, face["bbox"])
            emotion = face["emotion"]
            score = face["detection_score"]
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
            label = f"{emotion}: {score:.2f}"
            cv2.putText(annotated, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
        return annotated

    def _encode_frame_to_jpeg(self, frame: np.ndarray) -> bytes:
        success, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not success:
            raise ValueError("Failed to encode frame")
        return buffer.tobytes()

    def _handle_complete_pair(self, frame: np.ndarray, analytics: dict):
        """Обрабатывает готовую пару (кадр + аналитика)."""
        try:
            annotated = self._draw_annotations(frame, analytics)
            camera_id = analytics["camera_id"]
            timestamp = analytics["timestamp"]

            if self.producer and self.output_topic:
                jpeg_bytes = self._encode_frame_to_jpeg(annotated)
                message = {
                    "camera_id": camera_id,
                    "timestamp": timestamp,
                    "annotated_frame": base64.b64encode(jpeg_bytes).decode('utf-8')
                }
                self.producer.produce(
                    self.output_topic,
                    key=camera_id.encode(),
                    value=json.dumps(message).encode('utf-8')
                )
                self.producer.poll(0)
                logging.info(f"Published annotated frame for {camera_id}")
            else:
                cv2.imshow(f"Annotated - {camera_id}", annotated)
                if cv2.waitKey(1) == ord('q'):
                    raise KeyboardInterrupt

        except Exception as e:
            logging.error(f"Error handling pair: {e}", exc_info=True)

    def _process_raw_frame(self, msg_value: bytes):
        try:
            payload = json.loads(msg_value.decode('utf-8'))
            ts = payload["timestamp"]
            jpeg_data = base64.b64decode(payload["frame_data"])
            frame = cv2.imdecode(np.frombuffer(jpeg_data, np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                result = self.sync_buffer.add_frame(ts, frame)
                if result:
                    self._handle_complete_pair(*result)
        except Exception as e:
            logging.error(f"Error processing raw frame: {e}")

    def _process_analytics(self, msg_value: bytes):
        try:
            analytics = json.loads(msg_value.decode('utf-8'))
            ts = analytics["timestamp"]
            result = self.sync_buffer.add_analytics(ts, analytics)
            if result:
                self._handle_complete_pair(*result)
        except Exception as e:
            logging.error(f"Error processing analytics: {e}")

    def run(self):
        logging.info("Visualizer with SyncBuffer started")
        try:
            while True:
                raw_msg = self.raw_consumer.poll(0.05)
                if raw_msg and not raw_msg.error():
                    self._process_raw_frame(raw_msg.value())

                analytics_msg = self.analytics_consumer.poll(0.05)
                if analytics_msg and not analytics_msg.error():
                    self._process_analytics(analytics_msg.value())

                time.sleep(0.001)

        except KeyboardInterrupt:
            logging.info("Visualizer interrupted")
        finally:
            cv2.destroyAllWindows()
            self.raw_consumer.close()
            self.analytics_consumer.close()
            if self.producer:
                self.producer.flush()
