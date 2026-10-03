import time
import cv2
from collections import deque
from confluent_kafka import KafkaError, Producer
import threading
import logging
from typing import Any, Dict, Literal, Optional, Tuple
import numpy as np

from .config import CameraConfig
from .contracts import SCHEMA_VERSION, AudioMessage, SessionEvent, VideoMessage
from .pacing import FrameDecimator, RealtimePacer
from .sources import AudioChunk, MediaSource, SourceInfo, VideoFrame, open_source

EndReason = Literal['eof', 'stopped', 'error']

# Партиционер как у Java-клиента Kafka: одинаковый ключ → одинаковый номер
# партиции во всех топиках с одним числом партиций, независимо от языка продюсера.
_PARTITIONER = 'murmur2_random'

_SCHEMA_HEADER = str(SCHEMA_VERSION).encode()

# Пауза между попытками переподключения к брокеру растёт экспоненциально;
# по умолчанию в librdkafka — до 10 с. Тогда после восстановления брокера
# клиент «спит» ещё секунды, и свежие кадры истекают зря.
_RECONNECT_BACKOFF_MAX_MS = 1000


class ModalityError(RuntimeError):
    """Источник не может дать запрошенные модальности — переподключение не поможет."""


class CameraProducer:
    def __init__(self, config: CameraConfig):
        """
        Инициализация продюсера медиапотока (видео и/или аудио).

        В Kafka пишут два клиента с разными гарантиями:
          - медиа (кадры, аудиочанки): свежесть важнее доставки — `acks=1`,
            без идемпотентности, недоставленное за `kafka_media_timeout_ms`
            выбрасывается;
          - события сессии: `acks=all` + идемпотентность, их мало и терять нельзя.

        Args:
            config: <i>Класс с конфигурацией потока</i>
        """
        self.config = config

        self.producer_conf = self._media_producer_conf()
        self.events_producer_conf = self._events_producer_conf()
        self.producer = Producer(self.producer_conf)
        self.events_producer = Producer(self.events_producer_conf)

        self.is_running = False
        self.thread: Optional[threading.Thread] = None

        self.source: Optional[MediaSource] = None
        self.source_info: Optional[SourceInfo] = None
        self.effective_modalities: Tuple[str, ...] = ()
        self.degraded = False
        self.stream_started = False
        self.end_reason: Optional[EndReason] = None

        self._pacer = RealtimePacer(enabled=False)
        self._decimator = FrameDecimator(self.config.frame_rate)
        self._session_t0: Optional[float] = None
        self.session_start_wall_ts: Optional[float] = None
        self._pts_offset_ms = 0
        self._pass_start_pts_ms = 0

        self._stats_lock = threading.Lock()
        self.video_seq = 0
        self.audio_seq = 0
        self.frame_count = 0
        self.audio_chunks_sent = 0
        self.audio_samples_sent = 0
        self.error_count = 0
        self.dropped_local = {'video': 0, 'audio': 0}   # локальная очередь переполнена
        self.expired = 0                                # не доставлено за message.timeout.ms
        self.delivery_failed = 0                        # прочие ошибки доставки
        self.start_time: Optional[float] = None
        self.last_frame_time: Optional[float] = None
        self.last_video_pts_ms: Optional[int] = None
        self.last_audio_pts_ms: Optional[int] = None

        self._fps_window: deque = deque(maxlen=30)
        self.total_frames: Optional[int] = None
        self.duration_sec: Optional[float] = None
        self.native_fps: Optional[float] = None

        partition = self.config.effective_partition
        logging.info(
            f"Initialized producer {self.config.camera_id}: "
            f"partition={'by key' if partition is None else partition}, "
            f"modalities={self.config.modalities}"
        )

    def _media_producer_conf(self) -> Dict[str, Any]:
        return {
            'bootstrap.servers': self.config.kafka_servers,
            'message.max.bytes': self.config.max_message_size,
            'compression.type': 'lz4',
            'partitioner': _PARTITIONER,
            'acks': self.config.kafka_media_acks,
            'enable.idempotence': False,
            'linger.ms': self.config.kafka_media_linger_ms,
            'batch.size': 32768,
            'message.timeout.ms': self.config.kafka_media_timeout_ms,
            'retry.backoff.ms': 100,
            'reconnect.backoff.max.ms': _RECONNECT_BACKOFF_MAX_MS,
            'queue.buffering.max.kbytes': self.config.kafka_media_queue_kbytes,
            'queue.buffering.max.messages': 10000,
        }

    def _events_producer_conf(self) -> Dict[str, Any]:
        return {
            'bootstrap.servers': self.config.kafka_servers,
            'partitioner': _PARTITIONER,
            'acks': 'all',
            'enable.idempotence': True,
            'linger.ms': 0,
            'reconnect.backoff.max.ms': _RECONNECT_BACKOFF_MAX_MS,
        }

    def start(self) -> None:
        """Запуск передачи"""
        if self.is_running:
            logging.warning("Producer is already running")
            return

        self.is_running = True
        self.thread = threading.Thread(target=self._run, name=f"producer-{self.config.camera_id}")
        self.thread.daemon = True
        self.thread.start()
        logging.info("Started media streaming")

    def stop(self) -> None:
        """
        Остановка передачи с гарантией доставки сообщений.

        Источник закрывается и `stream_end` отправляется в потоке захвата
        (в конце `_run`), здесь только флаг и ожидание — иначе чтение и
        закрытие гоняются между потоками.
        """
        if not self.is_running and not (self.thread and self.thread.is_alive()):
            return

        self.is_running = False
        logging.info("Stopping media streaming...")

        if self.thread and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=10.0)
            if self.thread.is_alive():
                logging.warning("Thread did not stop gracefully")

        self._flush(self.producer, 2.0, "media")
        self._flush(self.events_producer, 5.0, "events")

        if self.start_time:
            duration = time.time() - self.start_time
            fps = self.frame_count / duration if duration > 0 else 0
            logging.info(
                f"Streaming stopped. Sent {self.frame_count} frames ({fps:.1f} FPS), "
                f"{self.audio_chunks_sent} audio chunks "
                f"({self.audio_samples_sent / self.config.audio_sample_rate:.1f}s) "
                f"in {duration:.1f}s, errors: {self.error_count}, "
                f"dropped: {self.dropped_local}, expired: {self.expired}"
            )

    @staticmethod
    def _flush(producer: Producer, timeout: float, name: str) -> None:
        try:
            remaining = producer.flush(timeout=timeout)
            if remaining > 0:
                logging.warning(f"{remaining} {name} messages were not delivered")
        except Exception as e:
            logging.error(f"Flush of {name} producer failed: {e}")

    def _stop_requested(self) -> bool:
        return not self.is_running

    def _run(self) -> None:
        """
        Основной цикл: открытие источника, чтение, отправка; переподключение
        при обрыве. Для файлов без `stop_on_end` — проигрывание по кругу.

        `stream_start` отправляется один раз — при первом успешном открытии,
        `stream_end` — один раз в конце, если сессия стартовала.
        """
        self.start_time = time.time()
        consecutive_failures = 0
        max_consecutive_failures = 3
        reason: Optional[EndReason] = None

        while self.is_running:
            try:
                self._open_source()
                consecutive_failures = 0
                if not self.stream_started:
                    self._send_event('stream_start')
                    self.stream_started = True

                self._loop()

                if not self.is_running:
                    reason = 'stopped'
                    break
                if not self.source_info.is_live and self.config.stop_on_end:
                    logging.info(f"Source exhausted ({self.config.source}); stopping producer")
                    reason = 'eof'
                    self.is_running = False
                    break
                if not self.source_info.is_live:
                    logging.info(f"Source exhausted ({self.config.source}); replaying")

            except ModalityError as e:
                logging.error(f"Cannot start stream: {e}")
                self.error_count += 1
                reason = 'error'
                break

            except Exception as e:
                logging.error(f"Unexpected error in capture loop: {e}")
                self.error_count += 1
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    logging.error("Max consecutive failures reached, stopping")
                    reason = 'error'
                    break
                self._sleep_interruptible(self.config.reconnect_timeout)

            finally:
                self._close_source()

        # Цикл вышел по условию while — значит, вызвали stop()
        self.end_reason = reason or 'stopped'

        # Сначала медиа, потом stream_end: событие о конце не должно
        # обгонять последние кадры сильнее, чем это неизбежно между топиками.
        self._flush(self.producer, 2.0, "media")
        if self.stream_started:
            self._send_event('stream_end', reason=self.end_reason)
        self._flush(self.events_producer, 10.0, "events")

    def _sleep_interruptible(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while self.is_running and time.monotonic() < deadline:
            time.sleep(0.1)

    def _open_source(self) -> None:
        """
        Открытие источника и разрешение модальностей.

        При повторном открытии (переподключение / повтор файла) время
        продолжается: к `pts_ms` нового прохода прибавляется смещение.
        """
        self.source = open_source(self.config)
        info = self.source.open(want_video=self.config.has_video, want_audio=self.config.has_audio)
        self.source_info = info
        self.effective_modalities = self._resolve_modalities(info)

        self.native_fps = info.native_fps
        self.total_frames = info.total_frames
        self.duration_sec = info.duration_sec

        if self._session_t0 is None:
            self._session_t0 = time.monotonic()
            self.session_start_wall_ts = time.time()
            self._pts_offset_ms = 0
            self._pacer = RealtimePacer(enabled=self.config.realtime_pacing and not info.is_live)
            self._pacer.start()
        elif info.is_live:
            self._pts_offset_ms = int((time.monotonic() - self._session_t0) * 1000)
        else:
            last = max(self.last_video_pts_ms or 0, self.last_audio_pts_ms or 0)
            self._pts_offset_ms = last + 1
        self._pass_start_pts_ms = self._pts_offset_ms

        logging.info(
            f"[{self.config.camera_id}] source opened: modalities={self.effective_modalities} "
            f"(requested {self.config.modalities}, degraded={self.degraded}), "
            f"live={info.is_live}, pts_offset={self._pts_offset_ms} ms"
        )

    def _resolve_modalities(self, info: SourceInfo) -> Tuple[str, ...]:
        available = {'video': info.has_video, 'audio': info.has_audio}
        effective = tuple(m for m in self.config.modalities if available[m])
        missing = tuple(m for m in self.config.modalities if not available[m])

        if not effective:
            raise ModalityError(
                f"source {self.config.source!r} has none of requested modalities {self.config.modalities}"
            )
        if missing:
            if self.config.strict_modalities:
                raise ModalityError(f"source {self.config.source!r} has no {missing}")
            logging.warning(
                f"[{self.config.camera_id}] source has no {missing}; continuing with {effective}"
            )
        self.degraded = bool(missing)
        return effective

    def _close_source(self) -> None:
        source, self.source = self.source, None
        if source is not None:
            try:
                source.close()
            except Exception:
                logging.exception("Source close failed")

    def _loop(self) -> None:
        """
        Чтение событий источника и отправка в Kafka.
        """
        send_video = 'video' in self.effective_modalities
        send_audio = 'audio' in self.effective_modalities

        window_n = 100
        window_t = time.time()
        window_start_count = self.frame_count

        for item in self.source.read():
            if not self.is_running:
                break

            pts_ms = item.pts_ms + self._pts_offset_ms
            self._pacer.wait_until(pts_ms, stop_flag=self._stop_requested)
            if not self.is_running:
                break

            if self.source_info.is_live:
                wall_ts = item.capture_wall_ts
            else:
                wall_ts = self.session_start_wall_ts + pts_ms / 1000.0

            try:
                if isinstance(item, VideoFrame):
                    if send_video and self._decimator.accept(pts_ms):
                        self._send_video(item, pts_ms, wall_ts)
                elif isinstance(item, AudioChunk):
                    if send_audio:
                        self._send_audio(item, pts_ms, wall_ts)
            except Exception as e:
                logging.error(f"Error sending {type(item).__name__}: {e}")
                self.error_count += 1

            if send_video and (self.frame_count - window_start_count) >= window_n:
                now = time.time()
                dt = now - window_t
                fps_eff = window_n / dt if dt > 0 else 0
                logging.info(
                    f"[{self.config.camera_id}] producer pushed "
                    f"{self.frame_count} frames, {self.audio_chunks_sent} audio chunks; "
                    f"effective {fps_eff:.1f} FPS (target {self.config.frame_rate})"
                )
                window_t = now
                window_start_count = self.frame_count

    def _envelope_kwargs(self, seq: int, pts_ms: int, wall_ts: float) -> Dict:
        return {
            'camera_id': self.config.camera_id,
            'seq': seq,
            'pts_ms': pts_ms,
            'modalities': self.effective_modalities,
            'capture_wall_ts': wall_ts,
            'session_start_wall_ts': self.session_start_wall_ts,
        }

    def _send_video(self, frame: VideoFrame, pts_ms: int, wall_ts: float) -> None:
        """
        Сжатие и отправка кадра.
        """
        processed_frame = self._resize_frame(frame.image)

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

        seq = self.video_seq
        message = VideoMessage(
            **self._envelope_kwargs(seq, pts_ms, wall_ts),
            frame_id=str(seq),
            timestamp=wall_ts,
            frame_data=jpeg_data.tobytes(),
            quality=self.config.quality,
            frame_rate=self.config.frame_rate,
            processed_width=processed_frame.shape[1],
            processed_height=processed_frame.shape[0],
            original_width=frame.image.shape[1],
            original_height=frame.image.shape[0],
        ).to_msgpack()

        if self._send_to_kafka(self.config.video_topic, message, 'video', wall_ts):
            now = time.time()
            with self._stats_lock:
                self.video_seq += 1
                self.frame_count += 1
                self.last_frame_time = now
                self.last_video_pts_ms = pts_ms
                self._fps_window.append(now)

    def _send_audio(self, chunk: AudioChunk, pts_ms: int, wall_ts: float) -> None:
        """
        Отправка аудиочанка.
        """
        seq = self.audio_seq
        message = AudioMessage(
            **self._envelope_kwargs(seq, pts_ms, wall_ts),
            sample_rate=self.config.audio_sample_rate,
            channels=self.config.audio_channels,
            num_samples=chunk.num_samples,
            audio_data=chunk.pcm,
        ).to_msgpack()

        if self._send_to_kafka(self.config.audio_topic, message, 'audio', wall_ts):
            with self._stats_lock:
                self.audio_seq += 1
                self.audio_chunks_sent += 1
                self.audio_samples_sent += chunk.num_samples
                self.last_audio_pts_ms = pts_ms

    def _stream_params(self) -> Tuple[Optional[Dict], Optional[Dict], Dict[str, str]]:
        video = audio = None
        topics: Dict[str, str] = {}
        if 'video' in self.effective_modalities:
            topics['video'] = self.config.video_topic
            video = {
                'frame_rate': self.config.frame_rate,
                'format': 'jpeg',
                'quality': self.config.quality,
                'max_width': self.config.max_width,
                'max_height': self.config.max_height,
                'native_fps': self.native_fps,
            }
        if 'audio' in self.effective_modalities:
            topics['audio'] = self.config.audio_topic
            audio = {
                'sample_rate': self.config.audio_sample_rate,
                'channels': self.config.audio_channels,
                'chunk_ms': self.config.audio_chunk_ms,
                'sample_format': 's16le',
                'native_rate': self.source_info.audio_native_rate if self.source_info else None,
            }
        return video, audio, topics

    def _send_event(self, event: Literal['stream_start', 'stream_end'],
                    reason: Optional[EndReason] = None) -> None:
        """
        Отправка события сессии в `events_topic`.

        При переполнении локальной очереди не выбрасываем, а ждём — событий
        мало, и консьюмеры опираются на них.
        """
        video, audio, topics = self._stream_params()
        now = time.time()
        kwargs: Dict[str, Any] = {}
        if event == 'stream_end':
            with self._stats_lock:
                kwargs = {
                    'reason': reason,
                    'last_pts_ms': {'video': self.last_video_pts_ms, 'audio': self.last_audio_pts_ms},
                    'sent': {'video': self.frame_count, 'audio': self.audio_chunks_sent},
                }

        message = SessionEvent(
            camera_id=self.config.camera_id,
            event=event,
            wall_ts=now,
            session_start_wall_ts=self.session_start_wall_ts or now,
            requested_modalities=self.config.modalities,
            modalities=self.effective_modalities,
            degraded=self.degraded,
            is_live=bool(self.source_info and self.source_info.is_live),
            topics=topics,
            video=video,
            audio=audio,
            **kwargs,
        ).to_msgpack()

        produce_kwargs = self._produce_kwargs(self.config.events_topic, message, b'event', now)
        produce_kwargs['callback'] = self._event_delivery_callback
        for _ in range(50):
            try:
                self.events_producer.produce(**produce_kwargs)
                self.events_producer.poll(0)
                logging.info(f"[{self.config.camera_id}] {event} sent"
                             + (f" (reason={reason})" if reason else ""))
                return
            except BufferError:
                self.events_producer.poll(0.1)
            except Exception as e:
                logging.error(f"Failed to send {event}: {e}")
                self.error_count += 1
                return
        logging.error(f"Failed to send {event}: events queue is full")
        self.error_count += 1

    def _resize_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        #### Уменьшение кадра при превышении максимального размера.

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

    def _produce_kwargs(self, topic: str, message: bytes, stream: bytes, wall_ts: float) -> Dict[str, Any]:
        """
        Общие аргументы `produce()`:
          - ключ `camera_id` — все потоки камеры в партиции с одним номером;
          - партиция явно только если задана в конфиге, иначе — по ключу;
          - время сообщения в Kafka = время захвата;
          - заголовки для фильтрации без распаковки.
        """
        kwargs: Dict[str, Any] = {
            'topic': topic,
            'key': self.config.camera_id.encode('utf-8'),
            'value': message,
            'timestamp': int(wall_ts * 1000),
            'headers': [('schema_version', _SCHEMA_HEADER), ('stream', stream)],
        }
        partition = self.config.effective_partition
        if partition is not None:
            kwargs['partition'] = partition
        return kwargs

    def _send_to_kafka(self, topic: str, message: bytes,
                       stream: Literal['video', 'audio'], wall_ts: float) -> bool:
        """
        Отправка медиасообщения в кафку.

        Args:
            topic (str): <i>Топик.</i>
            message (bytes): <i>Сообщение в msgpack.</i>
            stream (str): <i>'video' или 'audio'.</i>
            wall_ts (float): <i>Время захвата.</i>

        Returns:
            bool: <i>Поставлено ли сообщение в очередь продюсера.</i>
        """
        try:
            self.producer.produce(
                **self._produce_kwargs(topic, message, stream.encode(), wall_ts),
                callback=self._delivery_callback,
            )
            self.producer.poll(0)
            return True

        except BufferError:
            # Брокер не успевает или недоступен: ждать нельзя — остановится
            # захват. Выбрасываем, консьюмер увидит дыру в seq.
            with self._stats_lock:
                self.dropped_local[stream] += 1
                dropped = self.dropped_local[stream]
            if dropped == 1 or dropped % 100 == 0:
                logging.warning(f"Producer queue is full, dropped {dropped} {stream} messages so far")
        except Exception as e:
            logging.error(f"Failed to send message to Kafka: {e}")
            self.error_count += 1
        return False

    def _delivery_callback(self, err: Optional[KafkaError], msg) -> None:
        """
        Результат доставки медиасообщения.

        Истёкшие по `message.timeout.ms` считаются отдельно: для медиа это
        штатная ситуация при сбое брокера, а не ошибка.
        """
        if err is None:
            return
        if err.code() == KafkaError._MSG_TIMED_OUT:
            with self._stats_lock:
                self.expired += 1
                expired = self.expired
            if expired == 1 or expired % 100 == 0:
                logging.warning(f"Media messages expired before delivery: {expired} so far")
        else:
            logging.error(f'Message delivery failed: {err}')
            with self._stats_lock:
                self.delivery_failed += 1
                self.error_count += 1

    def _event_delivery_callback(self, err: Optional[KafkaError], msg) -> None:
        if err is not None:
            logging.error(f'Session event delivery failed: {err}')
            with self._stats_lock:
                self.error_count += 1

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

        with self._stats_lock:
            fps = self._rolling_fps()
            last_pts = max(
                (p for p in (self.last_video_pts_ms, self.last_audio_pts_ms) if p is not None),
                default=None,
            )
            position_sec: Optional[float] = None
            progress: Optional[float] = None
            if last_pts is not None:
                position_sec = max(0, last_pts - self._pass_start_pts_ms) / 1000.0
                if self.duration_sec:
                    progress = min(1.0, position_sec / self.duration_sec)

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
                'modalities': list(self.effective_modalities),
                'requested_modalities': list(self.config.modalities),
                'degraded': self.degraded,
                'stream_started': self.stream_started,
                'end_reason': self.end_reason,
                'video': {
                    'frames_sent': self.frame_count,
                    'fps': fps,
                    'last_pts_ms': self.last_video_pts_ms,
                },
                'audio': {
                    'chunks_sent': self.audio_chunks_sent,
                    'seconds_sent': self.audio_samples_sent / self.config.audio_sample_rate,
                    'last_pts_ms': self.last_audio_pts_ms,
                },
                'kafka': {
                    'dropped_local': dict(self.dropped_local),
                    'expired': self.expired,
                    'delivery_failed': self.delivery_failed,
                },
            }

    def __del__(self):
        """
        Деструктор для продюсера.
        """
        try:
            self.stop()
        except Exception:
            pass
