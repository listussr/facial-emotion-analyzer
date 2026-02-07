import time
import logging
import json
import base64
from typing import Any, Dict, List

import numpy as np
import cv2

from .kafka_io import KafkaIO
from .processing import EmotionRecognizer, FaceDetector, FaceIdentifier, initialize_deepsort, represent_ltrb, represent_detections, fast_face_filter

class ProcessingPipeline:
    def __init__(self, kafka_settings: Dict, detector_settings: Dict, analyzer_settings: Dict, 
                 tracker_settings: Dict, identifier_settings: Dict, emotion_frequency: int = 5,
                 cache_cleanup: int = 10):
        self._init_kafka_io(kafka_settings)
        self._init_detector(detector_settings)
        self._init_analyzer(analyzer_settings)
        self._init_tracker(tracker_settings)
        self._init_identifier(identifier_settings)

        self._emotion_frequency = emotion_frequency
        self._frame_num = 0
        self._cache_cleanup = cache_cleanup
        self._cached_faces = {}

        self._tracks_face_valid = {}

        logging.info("Initialized frames processing Pipeline")

    def _init_kafka_io(self, kafka_settings: Dict):
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
        min_confidence = detector_settings.get("min_detection_confidence", 0.5)
        self._face_detector = FaceDetector(min_confidence)
        logging.info("Initialized FaceDetector in pipeline.")

    def _init_analyzer(self, analyzer_settings: Dict):
        model_path = analyzer_settings.get("model_path", r"src\frames_processing\processing\models\resnet_18.pth")
        self._emotion_analyzer = EmotionRecognizer(model_path)
        logging.info("Initialized EmotionAnalyzer in pipeline.")

    def _init_tracker(self, tracker_settings: Dict):
        self._tracker = initialize_deepsort(tracker_settings)
        logging.info("Initialized DeepSort in pipeline")

    def _init_identifier(self, identifier_settings: Dict):
        self._identifier = FaceIdentifier(**identifier_settings)
        logging.info("Initialized identifier in pipeline")

    def _clean_cache(self, tracks: List, camera_id: str):
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
        Идентификация лица — вызывается ТОЛЬКО при новом треке
        """
        appearance = getattr(track, "last_feature", None)

        face_id = self._identifier.identify(face_crop, appearance)

        return {
            "face_id": face_id,
            "emotion_label": None,
            "emotion_probs": None,
            "emotion_counter": 0
        }
    
    def _update_emotion(self, cached: Dict, face_crop: np.ndarray):
        cached["emotion_counter"] += 1

        if cached["emotion_counter"] < self._emotion_frequency:
            return

        face_resized = cv2.resize(face_crop, (112, 112))
        probs = np.asarray(self._emotion_analyzer.predict(face_resized))

        cached["emotion_label"] = self._emotion_analyzer.idx_to_label(np.argmax(probs))
        cached["emotion_probs"] = probs
        cached["emotion_counter"] = 0

    def _handle_frame(self, frame: np.ndarray, metadata: Dict[str, Any]) -> Dict[str, Any]:
        self._frame_num += 1
        results = {
            "camera_id": metadata["camera_id"],
            "frame_id": metadata["frame_id"],
            "faces": []
        }

        detections = self._face_detector.detect(frame)

        detections_deepsort = represent_detections(detections)

        tracks = self._tracker.update_tracks(detections_deepsort, frame=frame)

        gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        for track in tracks:
            if not track.is_confirmed():
                continue

            if track.time_since_update > 0:
                continue

            track_id = track.track_id
            cache_key = (metadata["camera_id"], track_id)

            x, y, w, h = represent_ltrb(track)
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
        logging.info("Processing pipeline started")
        frame_count = 0
        start_time = time.time()

        while True:
            raw_msg = self._kafka.consume()
            if raw_msg is None:
                time.sleep(0.01)
                continue

            try:
                payload = json.loads(raw_msg.decode('utf-8'))
                jpeg_data = base64.b64decode(payload["frame_data"])
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
                logging.error(f"Processing error: {e}", exc_info=True)
