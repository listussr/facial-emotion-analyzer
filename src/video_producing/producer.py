import sys
import time
import cv2
import msgpack
import json
from collections import deque
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
        self.frame_id = 0

        self._fps_window: deque = deque(maxlen=30)
        self.total_frames: Optional[int] = None
        self.duration_sec: Optional[float] = None
        self.native_fps: Optional[float] = None
        
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

        На Windows для целочисленных источников (веб-камера) форсим
        DirectShow — дефолтный MSMF-бэкенд OpenCV на 11-м поколении Intel
        часто долго инициализируется или не открывает камеру вовсе.
        """
        try:
            if isinstance(self.config.source, str) and self.config.source.isdigit():
                self.config.source = int(self.config.source)

            if sys.platform == "win32" and isinstance(self.config.source, int):
                self.cap = cv2.VideoCapture(self.config.source, cv2.CAP_DSHOW)
            else:
                self.cap = cv2.VideoCapture(self.config.source)

            if not self.cap.isOpened():
                raise Exception(f"Failed to open video source: {self.config.source}")

            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)

            try:
                fps_native = float(self.cap.get(cv2.CAP_PROP_FPS) or 0)
                total = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if fps_native > 0:
                    self.native_fps = fps_native
                if total > 0:
                    self.total_frames = total
                    if fps_native > 0:
                        self.duration_sec = total / fps_native
            except Exception:
                pass

            logging.info(
                f"Successfully connected to video source: {self.config.source} "
                f"(native_fps={self.native_fps}, total_frames={self.total_frames})"
            )

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

        window_n = 100
        window_t = time.time()
        window_start_count = self.frame_count

        while self.is_running and self.cap and self.cap.isOpened():
            loop_start = time.time()

            try:
                ret, frame = self.cap.read()
                if not ret:
                    # Конец потока. Для файлов это конец файла — корректно
                    # завершаем продюсер, а не пытаемся «переподключиться»
                    # к тому же файлу (это вызывало бесконечное проигрывание).
                    if getattr(self.config, "stop_on_end", False):
                        logging.info(
                            f"Source exhausted ({self.config.source}); stopping producer"
                        )
                        self.is_running = False
                    break

                self._process_frame(frame)
                self.frame_count += 1
                self.last_frame_time = time.time()
                self._fps_window.append(self.last_frame_time)

                if (self.frame_count - window_start_count) >= window_n:
                    now = time.time()
                    dt = now - window_t
                    fps_eff = window_n / dt if dt > 0 else 0
                    logging.info(
                        f"[{self.config.camera_id}] producer pushed "
                        f"{self.frame_count} frames; effective {fps_eff:.1f} FPS "
                        f"(target {self.config.frame_rate})"
                    )
                    window_t = now
                    window_start_count = self.frame_count

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
            frame_id = self.frame_id
            self.frame_id += 1
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

            message = self._create_message(frame_id, frame, processed_frame, jpeg_data)
            
            self._send_to_kafka(message)
            
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

    def _create_message(self, frame_id: int, original_frame: np.ndarray, 
                       processed_frame: np.ndarray, 
                       jpeg_data: np.ndarray) -> bytes:
        """
        Создание

        Args:
            original_frame (np.ndarray): <i>Исходный кадр.</i>
            processed_frame (np.ndarray): <i>Обработанный кадр (после обрезки).</i>
            jpeg_data (np.ndarray): <i>Кадр в jpeg.</i>

        Returns:
            dict: <i>Словарь для отправки в кафку.</i>
        """
        return msgpack.packb({
            'camera_id': self.config.camera_id,
            'frame_id': str(frame_id),
            'timestamp': time.time(),
            'frame_data': jpeg_data.tobytes(),
            'processed_width': processed_frame.shape[1],
            'processed_height': processed_frame.shape[0],
            'original_width': original_frame.shape[1],
            'original_height': original_frame.shape[0],
            'quality': self.config.quality,
            'frame_rate': self.config.frame_rate,
            'format': 'jpeg'
        })

    def _send_to_kafka(self, message: bytes) -> None:
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
                value=message,
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

    def _rolling_fps(self) -> float:
        """Скользящий FPS по последним 30 отправленным кадрам."""
        if len(self._fps_window) < 2:
            return 0.0
        dt = self._fps_window[-1] - self._fps_window[0]
        return (len(self._fps_window) - 1) / dt if dt > 0 else 0.0

    def get_stats(self) -> Dict:
        """
        Статистика работы продюссера.

        Returns:
            Dict: <i>Словарь с данными работы.</i>
        """
        current_time = time.time()
        duration = current_time - self.start_time if self.start_time else 0

        fps = self._rolling_fps()
        progress: Optional[float] = None
        position_sec: Optional[float] = None
        if self.total_frames and self.total_frames > 0:
            progress = min(1.0, self.frame_count / self.total_frames)
            if self.native_fps:
                position_sec = self.frame_count / self.native_fps
        
        return {
            'camera_id': self.config.camera_id,
            'frames_sent': self.frame_count,
            'errors': self.error_count,
            'duration_seconds': duration,
            'current_fps': fps,
            'is_running': self.is_running,
            'last_frame_time': self.last_frame_time,
            'source': self.config.source,
            'progress': progress,
            'position_sec': position_sec,
            'video_duration_sec': self.duration_sec,
            'total_frames': self.total_frames,
        }

    def __del__(self):
        """
        Деструктор для камеры.
        """
        self.stop()
