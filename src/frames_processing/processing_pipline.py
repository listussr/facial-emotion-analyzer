import time
import logging
import queue
import threading
import json
import msgpack
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

import numpy as np
import cv2

from .kafka_io import KafkaIO
from .processing import (
    EmotionRecognizer,
    FaceDetector,
    FaceIdentifier,
    get_tracker,
    represent_ltrb,
    fast_face_filter,
    get_emotion_smoothing_strategy,
)

class ProcessingPipeline:
    def __init__(self, kafka_settings: Dict, detector_settings: Dict, analyzer_settings: Dict, 
                 tracker_settings: Dict, identifier_settings: Dict, emotion_frequency: int = 5,
                 cache_cleanup: int = 10, exp_smoothing_coef: float = 0.8, detection_frequency: int = 1):
        """
        #### Пайплайн обработки видеопотока.
        
        Порядок обработки одного отдельно взятого кадра.
            -> Детекция лиц.
            -> Трекинг лиц.
            -> Идентификация лиц (если трек новый).
            -> Распознавание эмоций.

        :param kafka_settings: Настройки кафки (см. класс `KafkaIO`).
        :type kafka_settings: Dict
        :param detector_settings: Настройки детектора лиц (см. класс `FaceDetector`).
        :type detector_settings: Dict
        :param analyzer_settings: Настройки анализатора эмоций (см. класс `EmotionRecognizer`).
        :type analyzer_settings: Dict
        :param tracker_settings: Настройки трекера лиц (см. функцию `initialize_deepsort`).
        :type tracker_settings: Dict
        :param identifier_settings: Настройки идентификатора лиц (см. класс `FaceIdentifier`).
        :type identifier_settings: Dict
        :param emotion_frequency: Частота анализа эмоций (в кадрах).
        :type emotion_frequency: int
        :param cache_cleanup: Частота очистки кэша (в кадрах).
        :type cache_cleanup: int
        :param exp_smoothing_coef: Коэффицент экспоненциального сглаживания эмоций.
        :type exp_smoothing_coef: float
        :param detection_frequency: Частота детекции кадров через mediapipe (в кадрах).
        :type detection_frequency: int
        """
        self._init_kafka_io(kafka_settings)
        self._init_detector(detector_settings)
        self._init_analyzer(analyzer_settings)
        self._init_tracker(tracker_settings)
        self._init_identifier(identifier_settings)

        self._emotion_frequency = emotion_frequency
        self._detection_frequency = detection_frequency
        self._frame_num = 0
        self._cache_cleanup = cache_cleanup
        self._cached_faces = {}

        self._tracks_face_valid = {}

        self._emotion_smoothing = get_emotion_smoothing_strategy('ema_hysteresis')

        self._exp_smoothing_coef = exp_smoothing_coef

        # Идентификация лица (FaceNet-эмбеддинг + поиск в БД) уезжает в
        # фоновый пул, чтобы не держать Stage B. До завершения у трека
        # стоит pending-id; как только воркер получит реальный — обновит
        # cached["face_id"] напрямую.
        self._identity_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="pipeline-identify"
        )

        logging.info("Initialized frames processing Pipeline")

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
            group_id
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

    def _init_identifier(self, identifier_settings: Dict):
        """
        #### Инициализация идентификатора лиц.
        """
        self._identifier = FaceIdentifier(**identifier_settings)
        logging.info("Initialized identifier in pipeline")

    def _clean_cache(self, tracks: List, camera_id: str):
        """
        #### Очистка кэша треков.
        
        :param tracks: Список треков.
        :type tracks: List
        :param camera_id: Идентификатор камеры.
        :type camera_id: str
        """
        active_track_ids = {t.track_id for t in tracks if t.is_confirmed()}

        self._cached_faces = {
            (cam_id, tid): data
            for (cam_id, tid), data in self._cached_faces.items()
            if not (cam_id == camera_id and tid not in active_track_ids)
        }
        self._tracks_face_valid = {
            k: v for k, v in self._tracks_face_valid.items()
            if not (k[0] == camera_id and k[1] not in active_track_ids)
        }

    def _update_identity(self, track, face_crop: np.ndarray) -> Dict:
        """
        #### Создать запись кэша для нового трека и запустить идентификацию в фоне.

        :param track: Трек с лицом.
        :param face_crop: Лицо с кадра.
        :type face_crop: np.ndarray
        :return: Словарь с данными для кэша (`face_id` пока pending-init).
        :rtype: Dict
        """
        cached: Dict = {
            "face_id": "pending-init",
            "emotion_label": None,
            "emotion_probs": np.zeros(8),
            "votes": np.zeros(8, dtype=np.int32),
            "emotion_counter": 0,
            "emotion_switch_counter": 0,
        }
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
        Воркер: считает эмбеддинг, ищет/создаёт запись в БД, дописывает
        реальный face_id обратно в `cached`. Любые ошибки логируем —
        пайплайн продолжит работать с pending-id.
        """
        try:
            face_id = self._identifier.identify(face_crop, appearance)
            cached["face_id"] = face_id
        except Exception:
            logging.exception("Identity worker failed; keeping pending id")

    def _apply_emotion_batch(self, pending: List):
        """
        #### Батч-инференс эмоций для накопленных лиц.

        Все лица текущего кадра, у которых счётчик достиг порога, прогоняются
        через модель одним вызовом. Это значительно быстрее, чем вызывать
        предсказание по одному лицу за раз.

        :param pending: Список кортежей `(cached, face_crop)`.
        :type pending: List
        """
        if not pending:
            return

        crops = [c for _, c in pending]
        probs_batch = np.asarray(self._emotion_analyzer.predict(crops))

        for (cached, _), probs in zip(pending, probs_batch):
            self._emotion_smoothing(
                cached,
                np.asarray(probs).ravel(),
                frequency=self._emotion_frequency,
                ema_coef=self._exp_smoothing_coef,
            )
            cached["emotion_counter"] = 0

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
        :param metadata: Метаданные к кадру.
        :type metadata: Dict[str, Any]
        :return: Словарь с данными для передачи в аннотатор эмоций.
        :rtype: Dict[str, Any]
        """
        self._frame_num += 1
        results = {
            "camera_id": metadata["camera_id"],
            "frame_id": metadata["frame_id"],
            "faces": []
        }

        if self._frame_num % self._detection_frequency == 0:
            detections = self._face_detector.detect(frame)
        else:
            detections = None

        if detections:
            tracks = self._tracker.update(detections, frame)
        else:
            tracks = self._tracker.predict()

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
            cache_key = (metadata["camera_id"], track_id)

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
                cached = self._update_identity(track, face_crop)
                self._cached_faces[cache_key] = cached

            cached["emotion_counter"] += 1
            if cached["emotion_counter"] >= self._emotion_frequency:
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

        self._apply_emotion_batch(pending_faces)

        for cached, entry in result_entries:
            entry["emotion"] = cached["emotion_label"]
            entry["emotion_scores"] = (
                cached["emotion_probs"].tolist()
                if cached["emotion_probs"] is not None else []
            )

        if self._frame_num % self._cache_cleanup == 0:
            self._clean_cache(tracks, metadata["camera_id"])
            self._frame_num = 0

        return results

    def _stage_consume_decode(
        self,
        in_q: "queue.Queue",
        stop: threading.Event,
    ) -> None:
        """
        Стадия A: читает сообщения из Kafka, декодирует JPEG в np.ndarray
        и кладёт `(frame, payload)` в очередь для стадии B.

        Если очередь заполнена — `put` блокируется, обеспечивая backpressure:
        чтение из Kafka замедляется до скорости обработки, а не растит
        потребление памяти.
        """
        log = logging.getLogger("pipeline.A")
        log.info("Stage A (consume+decode) started")
        while not stop.is_set():
            raw_msg = self._kafka.consume(timeout=0.5)
            if raw_msg is None:
                continue
            try:
                payload = msgpack.unpackb(raw_msg, raw=False)
                jpeg_data = payload["frame_data"]
                frame = cv2.imdecode(np.frombuffer(jpeg_data, np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    logging.warning("Failed to decode frame")
                    continue
                while not stop.is_set():
                    try:
                        in_q.put((frame, payload), timeout=0.5)
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


        - A: consume + decode
    
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

        try:
            while not stop.is_set():
                try:
                    item = in_q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is None:
                    break
                frame, payload = item
                try:
                    result = self._handle_frame(frame, payload)
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
                        logging.info(
                            f"Analyzed {frame_count} frames "
                            f"({fps:.1f} FPS over last {WINDOW}) "
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
