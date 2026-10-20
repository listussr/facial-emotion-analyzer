import time
import logging
import queue
import threading
import msgpack
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import cv2

from src.emotion_labels import EMOTION_LABELS, LABEL_SET, NUM_EMOTIONS
from src.session_config import SessionConfigStore, VisionSettings

from .kafka_io import KafkaIO
from .session_control import SessionControl
from .stream_metrics import LatencyWindow, StalenessFilter, capture_ts
from .processing import (
    EmotionRecognizer,
    FaceDetector,
    FaceIdentifier,
    get_tracker,
    represent_ltrb,
    fast_face_filter,
    get_emotion_smoothing_strategy,
)

RESULT_SCHEMA_VERSION = 2

# face_id трека, если идентификация для сессии выключена
ANONYMOUS_FACE_ID = 'anonymous'


class ProcessingPipeline:
    def __init__(self, kafka_settings: Dict, detector_settings: Dict, analyzer_settings: Dict,
                 tracker_settings: Dict, identifier_settings: Optional[Dict], emotion_frequency: int = 5,
                 cache_cleanup: int = 10, exp_smoothing_coef: float = 0.8, detection_frequency: int = 1,
                 session_store: Optional[SessionConfigStore] = None,
                 max_frame_age_ms: Optional[int] = 1000):
        """
        #### Пайплайн обработки видеопотока.

        Порядок обработки одного отдельно взятого кадра.
            -> Детекция лиц.
            -> Трекинг лиц.
            -> Идентификация лиц (если трек новый и идентификация включена).
            -> Распознавание эмоций.

        :param kafka_settings: Настройки кафки (см. класс `KafkaIO`); `events_topic` —
            топик событий сессий (stream_start / stream_end), None — не слушать.
        :param detector_settings: Настройки детектора лиц (см. класс `FaceDetector`).
        :param analyzer_settings: Настройки анализатора эмоций по умолчанию (см. `EmotionRecognizer`).
        :param tracker_settings: Настройки трекера лиц по умолчанию.
        :param identifier_settings: Настройки идентификатора лиц (см. `FaceIdentifier`);
            None — идентификация выключена для всех сессий (БД не нужна).
        :param emotion_frequency: Частота анализа эмоций по умолчанию (в кадрах).
        :param cache_cleanup: Частота очистки кэша (в кадрах).
        :param exp_smoothing_coef: Коэффицент экспоненциального сглаживания эмоций.
        :param detection_frequency: Частота детекции по умолчанию (в кадрах).
        :param session_store: Хранилище конфигов сессий (топик session-config);
            None — у всех сессий значения по умолчанию.
        :param max_frame_age_ms: Кадры старше этого возраста (по времени захвата)
            отбрасываются, не обрабатываясь; None — не отбрасывать.
        """
        self._default_tracker_settings: Dict = dict(tracker_settings or {})
        self._default_analyzer_settings: Dict = dict(analyzer_settings or {})
        self._events_topic: Optional[str] = (kafka_settings or {}).get("events_topic")

        self._init_kafka_io(kafka_settings)
        self._init_detector(detector_settings)
        self._init_analyzer(analyzer_settings)
        self._init_tracker(tracker_settings)
        self._init_identifier(identifier_settings)

        self._defaults = VisionSettings(
            tracker=self._default_tracker_settings.get("type", "bytetrack"),
            model=self._default_analyzer_settings.get("model", "resnet-18"),
            device=self._default_analyzer_settings.get("device", "cpu"),
            detection_frequency=detection_frequency,
            emotion_frequency=emotion_frequency,
            identification=self._identifier is not None,
        )
        self._session_control = SessionControl(self._defaults, store=session_store)
        if session_store is not None:
            session_store.set_on_change(self._session_control.on_config_change)

        # Per-session реестры
        self._session_trackers: Dict[str, "object"] = {}
        self._session_tracker_types: Dict[str, str] = {}
        self._recognizer_pool: Dict[Tuple[str, str, Optional[int]], "object"] = {}
        self._failed_recognizers: Dict[Tuple[str, str, Optional[int]], str] = {}
        # Дефолтный распознаватель
        self._recognizer_pool[self._recognizer_key(self._defaults)] = self._emotion_analyzer

        self._emotion_frequency = emotion_frequency
        self._detection_frequency = detection_frequency
        self._frame_num = 0
        self._cache_cleanup = cache_cleanup
        self._cached_faces = {}

        self._tracks_face_valid = {}

        self._emotion_smoothing = get_emotion_smoothing_strategy('ema_hysteresis')

        self._exp_smoothing_coef = exp_smoothing_coef

        self._staleness = StalenessFilter(max_frame_age_ms)
        self._latency = LatencyWindow()
        self._sessions_ended = 0

        # Идентификация лица (FaceNet-эмбеддинг + поиск в БД) уезжает в
        # фоновый пул, чтобы не держать Stage B. До завершения у трека
        # стоит pending-id; как только воркер получит реальный — обновит
        # cached["face_id"] напрямую.
        self._identity_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="pipeline-identify"
        )

        logging.info(
            f"Initialized frames processing Pipeline: defaults={self._defaults}, "
            f"labels={LABEL_SET}, max_frame_age_ms={max_frame_age_ms}, "
            f"session_store={'on' if session_store else 'off'}, events_topic={self._events_topic}"
        )

    def _init_kafka_io(self, kafka_settings: Dict):
        """
        #### Инициализация топиков кафки.
        """
        bootstrap_servers = kafka_settings.get("bootstrap_servers", "")
        input_topic = kafka_settings.get("input_topic", "")
        output_topic = kafka_settings.get("output_topic", "")
        group_id = kafka_settings.get("group_id", "emotion-pipeline-default")

        self._kafka = KafkaIO(
            bootstrap_servers,
            input_topic,
            output_topic,
            group_id,
            extra_input_topics=[self._events_topic] if self._events_topic else None,
        )
        logging.info("Initialized KafkaIO in pipeline.")

    def _init_detector(self, detector_settings: Dict):
        """
        #### Инициализиция детектора лиц.
        """
        min_confidence = detector_settings.get("min_detection_confidence", 0.5)
        self._face_detector = FaceDetector(min_confidence)
        logging.info("Initialized FaceDetector in pipeline.")

    def _init_analyzer(self, analyzer_settings: Dict):
        """
        #### Инициализация анализатора эмоций.
        """
        self._emotion_analyzer = EmotionRecognizer(**analyzer_settings)
        logging.info("Initialized EmotionAnalyzer in pipeline.")

    def _init_tracker(self, tracker_settings: Dict):
        """
        #### Инициализация трекера лиц.

        Тип трекера выбирается полем `type` в `tracker_settings`
        ("deepsort" | "bytetrack"). По умолчанию используется DeepSORT.
        """
        self._tracker = get_tracker(tracker_settings)
        logging.info("Initialized tracker in pipeline")

    def _init_identifier(self, identifier_settings: Optional[Dict]):
        """
        #### Инициализация идентификатора лиц (None — идентификация выключена).
        """
        if identifier_settings is None:
            self._identifier = None
            logging.info("Face identification disabled (no identifier settings)")
            return
        self._identifier = FaceIdentifier(**identifier_settings)
        logging.info("Initialized identifier in pipeline")

    def _recognizer_key(self, settings: VisionSettings) -> Tuple[str, str, Optional[int]]:
        """Ключ в пуле распознавателей: (model, device, num_threads)."""
        return (settings.model, settings.device, self._default_analyzer_settings.get("num_threads"))

    def _get_recognizer(self, settings: VisionSettings):
        """
        Возвращает EmotionRecognizer для настроек сессии.
        Разные сессии с одинаковой (model, device, threads) делят инстанс.
        Модель, которую не удалось загрузить (нет файла, не тот набор классов,
        нет CUDA), заменяется распознавателем по умолчанию — один раз с ошибкой в логе.
        """
        key = self._recognizer_key(settings)
        recog = self._recognizer_pool.get(key)
        if recog is not None:
            return recog
        if key in self._failed_recognizers:
            return self._emotion_analyzer
        logging.info(f"Spawning EmotionRecognizer for session: {key}")
        try:
            recog = EmotionRecognizer(model=key[0], device=key[1], num_threads=key[2])
        except Exception as e:
            self._failed_recognizers[key] = str(e)
            logging.error(f"Cannot load emotion model {key}: {e}; using default {self._defaults.model}")
            return self._emotion_analyzer
        self._recognizer_pool[key] = recog
        return recog

    def _get_session_tracker(self, camera_id: str, settings: VisionSettings):
        """
        Возвращает трекер именно для этой сессии (camera_id). Создаётся при
        первом кадре сессии и пересоздаётся, если тип трекера сменился в конфиге.
        """
        if self._session_control.take_reset(camera_id) or (
            camera_id in self._session_trackers
            and self._session_tracker_types.get(camera_id) != settings.tracker
        ):
            logging.info(f"[{camera_id}] tracker changed to {settings.tracker}; restarting tracks")
            self._end_tracks(camera_id)

        tracker = self._session_trackers.get(camera_id)
        if tracker is not None:
            return tracker
        per_session = dict(self._default_tracker_settings)
        per_session["type"] = settings.tracker
        logging.info(
            f"Spawning per-session tracker for {camera_id}: type={per_session.get('type')}"
        )
        tracker = get_tracker(per_session)
        self._session_trackers[camera_id] = tracker
        self._session_tracker_types[camera_id] = settings.tracker
        return tracker

    def _evict_session(self, camera_id: str) -> None:
        """Освобождаем per-session ресурсы, когда сессия закрылась."""
        self._session_tracker_types.pop(camera_id, None)
        if self._session_trackers.pop(camera_id, None) is not None:
            logging.info(f"Released tracker for {camera_id}")

    def _end_tracks(self, camera_id: str) -> None:
        """
        Завершить все треки камеры: сбросить таймсерии в БД, очистить кэш,
        освободить трекер. Используется при stream_end и смене трекера.
        """
        keys = [k for k in self._cached_faces if k[0] == camera_id]
        for key in keys:
            cached = self._cached_faces.pop(key, None)
            if cached is not None:
                self._flush_emotion_timeseries(cached, key)
        self._tracks_face_valid = {
            k: v for k, v in self._tracks_face_valid.items() if k[0] != camera_id
        }
        self._evict_session(camera_id)

    def _end_session(self, camera_id: str, reason: Optional[str]) -> None:
        """stream_end: сразу закрыть сессию, не дожидаясь таймаута неактивности."""
        self._end_tracks(camera_id)
        self._session_control.forget(camera_id)
        self._sessions_ended += 1
        logging.info(f"[{camera_id}] session ended (reason={reason})")

    # Количество секунд без обновлений, прежде чем считать трек ушедгим
    _INACTIVITY_TIMEOUT = 5.0

    def _clean_cache(self, tracks: List, camera_id: str):
        """
        #### Очистка кэша треков.

        Эвиктим записи в двух случаях:
          1) трек перестал быть активным в текущей камере (current behaviour);
          2) запись не обновлялась дольше `_INACTIVITY_TIMEOUT` секунд —
             ловит случай, когда сессия завершилась и новые кадры по её
             camera_id вообще больше не приходят.

        Перед удалением сбрасываем накопленную таймсерию эмоций в БД
        (через FaceIdentifier — он сериализует запись через свой executor).

        :param tracks: Список треков.
        :type tracks: List
        :param camera_id: Идентификатор камеры.
        :type camera_id: str
        """
        now = time.time()
        active_track_ids = {t.track_id for t in tracks if t.is_confirmed()}

        evicted_keys: List[tuple] = []
        for key, cached in self._cached_faces.items():
            cam_id, tid = key
            inactive_too_long = (now - cached.get("last_seen", now)) > self._INACTIVITY_TIMEOUT
            if (cam_id == camera_id and tid not in active_track_ids) or inactive_too_long:
                evicted_keys.append(key)

        for key in evicted_keys:
            cached = self._cached_faces.pop(key, None)
            if cached is not None:
                self._flush_emotion_timeseries(cached, key)

        evicted_set = set(evicted_keys)
        self._tracks_face_valid = {
            k: v for k, v in self._tracks_face_valid.items()
            if k not in evicted_set
            and not (k[0] == camera_id and k[1] not in active_track_ids)
        }

        # Если у камеры не осталось ни одной активной/живой записи в кэше —
        # освобождаем её per-session трекер (Kalman/Re-ID состояние больше не нужно).
        if camera_id and not any(k[0] == camera_id for k in self._cached_faces):
            self._evict_session(camera_id)

    def _flush_emotion_timeseries(self, cached: Dict, key: tuple) -> None:
        """
        Сформировать payload и отдать на асинхронную запись в БД.
        Если идентификация выключена или нет samples — тихо пропускаем
        (таймсерия в БД привязана к человеку).
        """
        samples = cached.get("emotion_samples") or []
        if not samples or self._identifier is None or cached.get("face_id") == ANONYMOUS_FACE_ID:
            return
        payload = {
            "camera_id": key[0],
            "track_id": key[1],
            "started_at": cached.get("started_at"),
            "ended_at": time.time(),
            "label_set": LABEL_SET,
            "labels": list(EMOTION_LABELS),
            "samples": samples,
        }
        face_id = cached.get("face_id")
        try:
            self._identifier.submit_emotion_timeseries(face_id, payload)
        except Exception:
            logging.exception("Failed to submit emotion timeseries")

    def _new_cache_entry(self) -> Dict:
        now = time.time()
        return {
            "face_id": "pending-init",
            "emotion_label": None,
            # None, а не нули: тогда сглаживание ставит метку по первому
            # предсказанию, а не ждёт двух уверенных «переключений» с пустого места
            "emotion_probs": None,
            "votes": np.zeros(NUM_EMOTIONS, dtype=np.int32),
            "emotion_counter": 0,
            "emotion_switch_counter": 0,
            "emotion_samples": [],
            "started_at": now,
            "last_seen": now,
        }

    def _update_identity(self, track, face_crop: np.ndarray, identification: bool = True) -> Dict:
        """
        #### Создать запись кэша для нового трека и запустить идентификацию в фоне.

        :param track: Трек с лицом.
        :param face_crop: Лицо с кадра.
        :type face_crop: np.ndarray
        :param identification: False — без идентификации (face_id = anonymous).
        :return: Словарь с данными для кэша (`face_id` пока pending-init).
        :rtype: Dict
        """
        cached = self._new_cache_entry()
        if not identification or self._identifier is None:
            cached["face_id"] = ANONYMOUS_FACE_ID
            return cached
        appearance = getattr(track, "last_feature", None)
        crop_copy = face_crop.copy()
        self._identity_executor.submit(
            self._fill_identity_async, cached, crop_copy, appearance
        )
        return cached

    def _fill_identity_async(
        self,
        cached: Dict,
        face_crop: np.ndarray,
        appearance: np.ndarray,
    ) -> None:
        """
        Воркер: считает эмбеддинг и идентификацию.

        identify() возвращает pending-id сразу, а уже из БД настоящий UUID
        прилетит позже — через колбэк on_resolved. Тогда мы и заменим
        cached["face_id"] на UUID, что позволит таймсерии эмоций корректно
        записаться в БД (FK-ограничение требует существующий user_id).
        """
        try:
            def _on_resolved(real_id: str) -> None:
                cached["face_id"] = real_id

            face_id = self._identifier.identify(
                face_crop, appearance, on_resolved=_on_resolved
            )
            cached["face_id"] = face_id
        except Exception:
            logging.exception("Identity worker failed; keeping pending id")

    def _apply_emotion_batch(self, pending: List, recognizer=None, frequency: Optional[int] = None):
        """
        #### Батч-инференс эмоций для накопленных лиц.

        Один кадр = одна сессия = один распознаватель. Если recognizer не задан,
        используется дефолтный.

        :param pending: Список кортежей `(cached, face_crop)`.
        :type pending: List
        :param recognizer: Конкретный EmotionRecognizer из пула.
        :param frequency: Частота анализа эмоций сессии (для сглаживания).
        """
        if not pending:
            return

        if recognizer is None:
            recognizer = self._emotion_analyzer
        frequency = frequency or self._emotion_frequency

        crops = [c for _, c in pending]
        probs_batch = np.asarray(recognizer.predict(crops))

        for (cached, _), probs in zip(pending, probs_batch):
            self._emotion_smoothing(
                cached,
                np.asarray(probs).ravel(),
                frequency=frequency,
                ema_coef=self._exp_smoothing_coef,
            )
            cached["emotion_counter"] = 0
            label = cached.get("emotion_label")
            if label is not None:
                emo_probs = cached.get("emotion_probs")
                cached["emotion_samples"].append({
                    "t": time.time(),
                    "label": label,
                    "scores": emo_probs.tolist() if emo_probs is not None else [],
                })

    def _handle_frame(self, frame: np.ndarray, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        #### Основная функция пайплайна с обработкой кадра.

        Порядок обработки одного отдельно взятого кадра.
            -> Детекция лиц.
            -> Трекинг лиц.
            -> Идентификация лиц (если трек новый).
            -> Распознавание эмоций.

        :param frame: Кадр видеопотока.
        :type frame: np.ndarray
        :param metadata: Метаданные к кадру (сообщение продюсера, schema v1 или v2).
        :type metadata: Dict[str, Any]
        :return: Словарь с данными для передачи в аннотатор эмоций.
        :rtype: Dict[str, Any]
        """
        self._frame_num += 1
        camera_id = metadata["camera_id"]

        settings = self._session_control.settings(camera_id, metadata.get("session_config"))
        frame_index = self._session_control.next_frame_index(camera_id)

        results = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "camera_id": camera_id,
            "frame_id": metadata["frame_id"],
            # Время кадра в потоке и время захвата — по ним результат
            # сопоставляется с аудио при слиянии модальностей.
            "seq": metadata.get("seq"),
            "pts_ms": metadata.get("pts_ms"),
            "capture_wall_ts": capture_ts(metadata),
            "label_set": LABEL_SET,
            "emotion_labels": list(EMOTION_LABELS),
            "faces": []
        }

        if (frame_index - 1) % settings.detection_frequency == 0:
            detections = self._face_detector.detect(frame)
        else:
            detections = None

        tracker = self._get_session_tracker(camera_id, settings)

        if detections:
            tracks = tracker.update(detections, frame)
        else:
            tracks = tracker.predict()

        gray_full = None
        frame_h, frame_w = frame.shape[:2]

        pending_faces = []
        result_entries = []

        for track in tracks:
            if not track.is_confirmed():
                continue

            if track.time_since_update > 0:
                continue

            track_id = track.track_id
            cache_key = (camera_id, track_id)

            ltrb = represent_ltrb(track)
            if ltrb is None:
                continue

            x, y, w, h = ltrb

            x1, x2 = max(x, 0), min(x + w, frame_w)
            y1, y2 = max(y, 0), min(y + h, frame_h)

            face_crop = frame[y1:y2, x1:x2]

            if face_crop.size == 0:
                continue

            face_valid = self._tracks_face_valid.get(cache_key)
            if face_valid is None:
                if track.hits < 3:
                    continue
                if gray_full is None:
                    gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray_crop = gray_full[y1:y2, x1:x2]
                face_valid = fast_face_filter(gray_crop, w, h)
                self._tracks_face_valid[cache_key] = face_valid

            if not face_valid:
                continue

            cached = self._cached_faces.get(cache_key)
            if cached is None:
                cached = self._update_identity(track, face_crop, identification=settings.identification)
                self._cached_faces[cache_key] = cached
            cached["last_seen"] = time.time()

            cached["emotion_counter"] += 1
            if cached["emotion_counter"] >= settings.emotion_frequency:
                pending_faces.append((cached, face_crop))

            entry = {
                "bbox": [x1, y1, x2, y2],
                "track_id": track_id,
                "face_id": cached["face_id"],
                "emotion": None,
                "emotion_scores": []
            }
            results["faces"].append(entry)
            result_entries.append((cached, entry))

        self._apply_emotion_batch(
            pending_faces,
            recognizer=self._get_recognizer(settings),
            frequency=settings.emotion_frequency,
        )

        for cached, entry in result_entries:
            entry["emotion"] = cached["emotion_label"]
            entry["emotion_scores"] = (
                cached["emotion_probs"].tolist()
                if cached["emotion_probs"] is not None else []
            )

        if self._frame_num % self._cache_cleanup == 0:
            self._clean_cache(tracks, camera_id)
            self._frame_num = 0

        return results

    def _handle_event(self, event: Dict[str, Any]) -> None:
        """Событие сессии из events_topic: stream_end закрывает сессию сразу."""
        camera_id = event.get("camera_id")
        if not camera_id:
            return
        if event.get("event") == "stream_end":
            self._end_session(camera_id, event.get("reason"))
        elif event.get("event") == "stream_start":
            logging.info(
                f"[{camera_id}] stream_start: modalities={event.get('modalities')}, "
                f"degraded={event.get('degraded')}"
            )

    def _stage_consume_decode(
        self,
        in_q: "queue.Queue",
        stop: threading.Event,
    ) -> None:
        """
        Стадия A: читает сообщения из Kafka, отбрасывает устаревшие кадры,
        декодирует JPEG в np.ndarray и кладёт в очередь для стадии B:
          ('frame', frame, payload) — кадр;
          ('event', payload)        — событие сессии (в том же порядке, что и кадры).

        Если очередь заполнена — `put` блокируется, обеспечивая backpressure;
        кадры, устаревшие за время ожидания, отсеются на следующей проверке
        в стадии B.
        """
        log = logging.getLogger("pipeline.A")
        log.info("Stage A (consume+decode) started")
        while not stop.is_set():
            msg = self._kafka.consume_message(timeout=0.5)
            if msg is None:
                continue
            try:
                payload = msgpack.unpackb(msg.value(), raw=False)
                if self._events_topic and msg.topic() == self._events_topic:
                    item = ('event', payload)
                else:
                    # Проверка возраста до декодирования JPEG — не тратим CPU на устаревшее
                    if self._staleness.is_stale(payload, time.time()):
                        continue
                    jpeg_data = payload["frame_data"]
                    frame = cv2.imdecode(np.frombuffer(jpeg_data, np.uint8), cv2.IMREAD_COLOR)
                    if frame is None:
                        logging.warning("Failed to decode frame")
                        continue
                    item = ('frame', frame, payload)
                while not stop.is_set():
                    try:
                        in_q.put(item, timeout=0.5)
                        break
                    except queue.Full:
                        continue
            except Exception:
                logging.exception("Stage A error")
        try:
            in_q.put_nowait(None)
        except queue.Full:
            pass
        log.info("Stage A stopped")

    def _stage_produce(
        self,
        out_q: "queue.Queue",
    ) -> None:
        """
        Стадия C: забирает готовый результат и публикует в Kafka.
        """
        log = logging.getLogger("pipeline.C")
        log.info("Stage C (produce) started")
        while True:
            item = out_q.get()
            if item is None:
                break
            try:
                self._kafka.produce(item)
            except Exception:
                logging.exception("Stage C error")
        log.info("Stage C stopped")

    def process(self):
        """
        #### Запуск пайплайна обработки.

        Архитектура — три ступени с ограниченными очередями между ними:


        - A: consume + decode (+ отбрасывание устаревших кадров, события сессий)

        - B: detect / track / identify / emotion

        - C: produce result

        Каждая ступень загружает разные ресурсы: A — сеть/декодер, B — CPU/GPU, C — сеть.
        """
        logging.info("Processing pipeline started (threaded)")

        in_q: queue.Queue = queue.Queue(maxsize=4)
        out_q: queue.Queue = queue.Queue(maxsize=4)
        stop = threading.Event()

        consumer_thread = threading.Thread(
            target=self._stage_consume_decode,
            args=(in_q, stop),
            name="pipeline-consume",
            daemon=True,
        )
        producer_thread = threading.Thread(
            target=self._stage_produce,
            args=(out_q,),
            name="pipeline-produce",
            daemon=True,
        )
        consumer_thread.start()
        producer_thread.start()

        frame_count = 0

        WINDOW = 100
        last_window_t = None
        window_dropped = 0
        IDLE_SWEEP_INTERVAL = 2.0
        last_idle_sweep = time.time()

        try:
            while not stop.is_set():
                try:
                    item = in_q.get(timeout=0.5)
                except queue.Empty:
                    if time.time() - last_idle_sweep > IDLE_SWEEP_INTERVAL:
                        try:
                            self._clean_cache([], "")
                        except Exception:
                            logging.exception("Idle cache sweep failed")
                        last_idle_sweep = time.time()
                    continue
                if item is None:
                    break

                if item[0] == 'event':
                    try:
                        self._handle_event(item[1])
                    except Exception:
                        logging.exception("Session event handling failed")
                    continue

                _, frame, payload = item
                # Кадр мог устареть, пока ждал в очереди
                if self._staleness.is_stale(payload, time.time()):
                    continue
                try:
                    result = self._handle_frame(frame, payload)
                    result["processed_wall_ts"] = time.time()
                    ts = result.get("capture_wall_ts")
                    if ts is not None:
                        self._latency.add(result["processed_wall_ts"] - ts)
                    while not stop.is_set():
                        try:
                            out_q.put(result, timeout=0.5)
                            break
                        except queue.Full:
                            continue

                    frame_count += 1
                    if last_window_t is None:
                        last_window_t = time.time()
                    if frame_count % WINDOW == 0:
                        now = time.time()
                        dt = now - last_window_t
                        fps = WINDOW / dt if dt > 0 else 0
                        last_window_t = now
                        lat = self._latency.summary_ms()
                        dropped = self._staleness.dropped - window_dropped
                        window_dropped = self._staleness.dropped
                        self._latency.reset()
                        logging.info(
                            f"Analyzed {frame_count} frames "
                            f"({fps:.1f} FPS over last {WINDOW}), "
                            f"latency p50={lat['p50']:.0f} p95={lat['p95']:.0f} max={lat['max']:.0f} ms, "
                            f"dropped stale={dropped} (total {self._staleness.dropped}) "
                            f"[in_q={in_q.qsize()}/{in_q.maxsize}, "
                            f"out_q={out_q.qsize()}/{out_q.maxsize}]"
                        )
                except Exception:
                    logging.exception("Stage B error on frame")
        except KeyboardInterrupt:
            logging.info("Pipeline interrupted, shutting down")
        finally:
            stop.set()
            try:
                for key, cached in list(self._cached_faces.items()):
                    self._flush_emotion_timeseries(cached, key)
                self._cached_faces.clear()
            except Exception:
                logging.exception("Final emotion flush failed")
            try:
                out_q.put(None, timeout=1.0)
            except queue.Full:
                pass
            consumer_thread.join(timeout=2.0)
            producer_thread.join(timeout=2.0)

            try:
                self._identity_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                logging.exception("Identity executor shutdown failed")

            try:
                self._kafka.close()
            except Exception:
                logging.exception("KafkaIO close failed")
            logging.info("Pipeline stopped")
