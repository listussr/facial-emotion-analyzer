import time
import cv2
import base64
import json
from confluent_kafka import Producer
import threading
import logging
from typing import Optional, Dict
import numpy as np
from .config import CameraConfig


class CameraProducer:
    def __init__(self, config: CameraConfig):
        """
        Инициализация продюсера для видеопотока
        
        Args:
            config: <i>Класс с конфигурацией видеопотока</i>
        """
        self.config = config
        
        self.producer_conf = {
            'bootstrap.servers': self.config.kafka_servers,
            'message.max.bytes': self.config.max_message_size,
            'batch.size': 32768,
            'linger.ms': 10,
            'compression.type': 'lz4',
            'retries': 5,
            'retry.backoff.ms': 500,
            'queue.buffering.max.messages': 100000,
            'queue.buffering.max.kbytes': 1048576,
            'enable.idempotence': True,
        }
        
        self.producer = Producer(self.producer_conf)
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self.cap: Optional[cv2.VideoCapture] = None
        
        self.frame_count = 0
        self.error_count = 0
        self.start_time: Optional[float] = None
        self.last_frame_time: Optional[float] = None
        
        logging.info(f"Initialized for partition {self.config.partition} "
                        f"(total partitions: {self.config.total_partitions})")

    def start(self) -> None:
        """Запуск передачи кадров"""
        if self.is_running:
            logging.warning("Producer is already running")
            return
            
        self.is_running = True
        self.thread = threading.Thread(target=self._capture_and_send)
        self.thread.daemon = True
        self.thread.start()
        logging.info("Started video streaming")

    def stop(self) -> None:
        """
        Остановка передачи с гарантией доставки сообщений.
        """
        if not self.is_running:
            return
            
        self.is_running = False
        logging.info("Stopping video streaming...")
        
        if self.cap:
            self.cap.release()
            self.cap = None
            
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=10.0)
            if self.thread.is_alive():
                logging.warning("Thread did not stop gracefully")
        
        try:
            messages_remaining = self.producer.flush(timeout=5)
            if messages_remaining > 0:
                logging.warning(f"{messages_remaining} messages were not delivered")
        except Exception as e:
            logging.error(f"Flush failed: {e}")
        
        if self.start_time:
            duration = time.time() - self.start_time
            fps = self.frame_count / duration if duration > 0 else 0
            logging.info(
                f"Streaming stopped. Sent {self.frame_count} frames "
                f"in {duration:.1f}s ({fps:.1f} FPS), errors: {self.error_count}"
            )

    def _capture_and_send(self) -> None:
        """
        Основной цикл захвата и отправки кадров с переподключением.
        """
        self.start_time = time.time()
        consecutive_failures = 0
        max_consecutive_failures = 3
        
        while self.is_running:
            try:
                # проверка записи кадров на камере
                if self.cap is None or not self.cap.isOpened():
                    self._initialize_capture()
                    if self.cap is None or not self.cap.isOpened():
                        consecutive_failures += 1
                        if consecutive_failures >= max_consecutive_failures:
                            logging.error("Max consecutive failures reached, stopping")
                            break
                        time.sleep(self.config.reconnect_timeout)
                        continue
                
                # обработка кадров
                consecutive_failures = 0
                self._process_capture_loop()
                
            except Exception as e:
                logging.error(f"Unexpected error in capture loop: {e}")
                consecutive_failures += 1
                if self.cap:
                    self.cap.release()
                    self.cap = None
                
                if consecutive_failures >= max_consecutive_failures:
                    logging.error("Max consecutive failures reached, stopping")
                    break
                    
                time.sleep(self.config.reconnect_timeout)

    def _initialize_capture(self) -> None:
        """
        Инициализация захвата видео.
        """
        try:
            if isinstance(self.config.source, str) and self.config.source.isdigit():
                self.config.source = int(self.config.source)
            self.cap = cv2.VideoCapture(self.config.source)
            if not self.cap.isOpened():
                raise Exception(f"Failed to open video source: {self.config.source}")
                
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
            logging.info(f"Successfully connected to video source: {self.config.source}")
            
        except Exception as e:
            logging.error(f"Failed to initialize capture: {e}")
            if self.cap:
                self.cap.release()
                self.cap = None

    def _process_capture_loop(self) -> None:
        """
        Обработка цикла захвата видеопотока.
        """
        frame_interval = 1.0 / self.config.frame_rate
        
        while self.is_running and self.cap and self.cap.isOpened():
            loop_start = time.time()
            
            try:
                ret, frame = self.cap.read()
                if not ret:
                    logging.warning("Failed to read frame, reinitializing capture")
                    break
                
                self._process_frame(frame)
                self.frame_count += 1
                self.last_frame_time = time.time()
                
            except Exception as e:
                logging.error(f"Error processing frame: {e}")
                self.error_count += 1
                break
            
            elapsed = time.time() - loop_start
            sleep_time = max(0, frame_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _process_frame(self, frame: np.ndarray) -> None:
        """
        Сжатие и отправка кадра.

        Args:
            frame (np.ndarray): <i>Исходный кадр</i>
        """
        try:
            processed_frame = self._resize_frame(frame)
            
            success, jpeg_data = cv2.imencode(
                '.jpg', 
                processed_frame, 
                [cv2.IMWRITE_JPEG_QUALITY, self.config.quality]
            )
            
            if not success:
                logging.warning("Failed to encode frame as JPEG")
                return
            
            if len(jpeg_data) > self.config.max_message_size:
                logging.warning(
                    f"Frame too large: {len(jpeg_data)} bytes. "
                    f"Max allowed: {self.config.max_message_size}"
                )
                return
            
            message = self._create_message(frame, processed_frame, jpeg_data)
            message_json = json.dumps(message)
            
            self._send_to_kafka(message_json)
            
        except Exception as e:
            logging.error(f"Error in frame processing: {e}")
            self.error_count += 1

    def _resize_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        #### Уменьшение кадра при превышении максимального размера файла.

        Args:
            frame (np.ndarray): <i>Исходный кадр видеопотока.</i>

        Returns:
            np.ndarray: <i>Обработанный кадр.</i>
        """
        height, width = frame.shape[:2]
        
        if width <= self.config.max_width and height <= self.config.max_height:
            return frame
        
        scale = min(self.config.max_width / width, self.config.max_height / height)
        new_width = int(width * scale)
        new_height = int(height * scale)
        
        logging.debug(f"Resizing frame from {width}x{height} to {new_width}x{new_height}")
        return cv2.resize(frame, (new_width, new_height), interpolation=cv2.INTER_AREA)

    def _create_message(self, original_frame: np.ndarray, 
                       processed_frame: np.ndarray, 
                       jpeg_data: np.ndarray) -> dict:
        """
        Создание

        Args:
            original_frame (np.ndarray): <i>Исходный кадр.</i>
            processed_frame (np.ndarray): <i>Обработанный кадр (после обрезки).</i>
            jpeg_data (np.ndarray): <i>Кадр в jpeg.</i>

        Returns:
            dict: <i>Словарь для отправки в кафку.</i>
        """
        return {
            'camera_id': self.config.camera_id,
            'timestamp': time.time_ns(),
            'frame_data': base64.b64encode(jpeg_data).decode('utf-8'),
            'processed_width': processed_frame.shape[1],
            'processed_height': processed_frame.shape[0],
            'original_width': original_frame.shape[1],
            'original_height': original_frame.shape[0],
            'quality': self.config.quality,
            'frame_rate': self.config.frame_rate,
            'format': 'jpeg'
        }

    def _send_to_kafka(self, message_json: str) -> None:
        """
        Отправка сообщений в кафку

        Args:
            message_json (str): <i>Сообщение в JSON-формате.</i>
        """
        try:
            effective_partition = self.config.partition % self.config.total_partitions
            
            self.producer.produce(
                topic=self.config.topic_name,
                key=self.config.camera_id.encode('utf-8'),
                value=message_json,
                partition=effective_partition,
                callback=self._delivery_callback
            )
            
            self.producer.poll(0)
            
        except BufferError:
            logging.warning("Producer queue is full, frame will be dropped")
            self.error_count += 1
        except Exception as e:
            logging.error(f"Failed to send message to Kafka: {e}")
            self.error_count += 1

    def _delivery_callback(self, err: Optional[Exception], msg) -> None:
        """
        Логирование доставки сообщений.

        Args:
            err (Optional[Exception]): <i>Ошибка отправки.</i>
            msg: <i>Отправленное сообщение.</i>
        """
        if err:
            logging.error(f'Message delivery failed: {err}')
            self.error_count += 1
        else:
            logging.debug(
                f'Message delivered to {msg.topic()}[{msg.partition()}] '
                f'at offset {msg.offset()}'
            )

    def get_stats(self) -> Dict:
        """
        Статистика работы продюссера.

        Returns:
            Dict: <i>Словарь с данными работы.</i>
        """
        current_time = time.time()
        duration = current_time - self.start_time if self.start_time else 0
        fps = self.frame_count / duration if duration > 0 else 0
        
        return {
            'camera_id': self.config.camera_id,
            'frames_sent': self.frame_count,
            'errors': self.error_count,
            'duration_seconds': duration,
            'current_fps': fps,
            'is_running': self.is_running,
            'last_frame_time': self.last_frame_time,
            'source': self.config.source
        }

    def __del__(self):
        """
        Деструктор для камеры.
        """
        self.stop()
