import time
import logging
import json
import msgpack
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
        #### Идентификация лица.
        
        :param track: Трек с лицом.
        :param face_crop: Лицо с кадра.
        :type face_crop: np.ndarray
        :return: Словарь с данными эмоций и face_id для вставки в кэш.
        :rtype: Dict
        """
        appearance = getattr(track, "last_feature", None)

        face_id = self._identifier.identify(face_crop, appearance)

        return {
            "face_id": face_id,
            "emotion_label": None,
            "emotion_probs": np.zeros(8),
            "votes": np.zeros(8, dtype=np.int32),
            "emotion_counter": 0,
            "emotion_switch_counter": 0,
        }

    def _update_emotion(self, cached: Dict, face_crop: np.ndarray):
        """
        #### Предсказание эмоции по лицу.
        
        :param cached: Кэшированные данные.
        :type cached: Dict
        :param face_crop: Изображение лица.
        :type face_crop: np.ndarray
        """
        cached["emotion_counter"] += 1

        if cached["emotion_counter"] < self._emotion_frequency:
            return

        probs = np.asarray(self._emotion_analyzer.predict(face_crop)).ravel()
        self._emotion_smoothing(
            cached,
            probs,
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

        gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

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
            frame_h, frame_w = frame.shape[:2]

            x1, x2 = max(x, 0), min(x + w, frame_w)
            y1, y2 = max(y, 0), min(y + h, frame_h)

            face_crop = frame[y1:y2, x1:x2]
            gray_crop = gray_full[y1:y2, x1:x2]

            if face_crop.size == 0:
                continue

            face_valid = self._tracks_face_valid.get(cache_key)
            if face_valid is None:
                if track.hits < 3: 
                    continue
                face_valid = fast_face_filter(gray_crop, w, h)
                self._tracks_face_valid[cache_key] = face_valid

            if not face_valid:
                continue

            cached = self._cached_faces.get(cache_key)
            if cached is None:
                cached = self._update_identity(track, face_crop)
                self._cached_faces[cache_key] = cached

            face_id = cached["face_id"]

            self._update_emotion(cached, face_crop)

            results["faces"].append({
                "bbox": [x1, y1, x2, y2],
                "track_id": track_id,
                "face_id": face_id,
                "emotion": cached["emotion_label"],
                "emotion_scores": (
                    cached["emotion_probs"].tolist()
                    if cached["emotion_probs"] is not None else []
                )
            })

        if self._frame_num % self._cache_cleanup == 0:
            self._clean_cache(tracks, metadata["camera_id"])
            self._frame_num = 0

        return results

    def process(self):
        """
        #### Запуск пайплайна обработки.

        Порядок обработки одного отдельно взятого кадра.
            -> Детекция лиц.
            -> Трекинг лиц.
            -> Идентификация лиц (если трек новый).
            -> Распознавание эмоций.
        """
        logging.info("Processing pipeline started")
        frame_count = 0
        start_time = time.time()

        while True:
            raw_msg = self._kafka.consume()
            if raw_msg is None:
                time.sleep(0.01)
                continue

            try:
                payload = msgpack.unpackb(raw_msg, raw=False)
                jpeg_data = payload["frame_data"]
                frame = cv2.imdecode(np.frombuffer(jpeg_data, np.uint8), cv2.IMREAD_COLOR)

                if frame is None:
                    logging.warning("Failed to decode frame")
                    continue

                result = self._handle_frame(frame, payload)
                self._kafka.produce(result)

                frame_count += 1
                if frame_count % 100 == 0:
                    elapsed = time.time() - start_time
                    fps = frame_count / elapsed if elapsed > 0 else 0
                    logging.info(f"Analyzed {frame_count} frames ({fps:.1f} FPS)")

            except Exception as e:
                logging.exception("Error in pipeline")
