import time
import logging
import json
import base64
from typing import Any, Dict

import numpy as np
import cv2

from .kafka_io import KafkaIO
from .processing import EmotionRecognizer, FaceDetector, FaceIdentifier, initialize_deepsort, represent_ltrb, represent_detections

class ProcessingPipeline:
    def __init__(self, kafka_settings: Dict, detector_settings: Dict, analyzer_settings: Dict, 
                 tracker_settings: Dict, identifier_settings: Dict, analyze_frequency: int = 5):
        self._init_kafka_io(kafka_settings)
        self._init_detector(detector_settings)
        self._init_analyzer(analyzer_settings)
        self._init_tracker(tracker_settings)
        self._init_identifier(identifier_settings)
        self._last_result_cache = {}
        self._analysis_frequency = analyze_frequency
        self._frame_counter = 0
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

    def _handle_frame(self, frame: np.ndarray, metadata: Dict[str, Any]) -> Dict[str, Any]:
        results = {
            "camera_id": metadata["camera_id"],
            "frame_id": metadata["frame_id"],
            "faces": []
        }

        detections = self._face_detector.detect(frame)

        detections_deepsort = represent_detections(detections)


        tracks = self._tracker.update_tracks(detections_deepsort, frame=frame)

        for track in tracks:
            if not track.is_confirmed():
                continue

            track_id = track.track_id
            x, y, w, h = represent_ltrb(track)

            x1, x2, y1, y2 = x, x + w, y, y + h

            face_crop = frame[y1:y2, x1:x2]

            face_id = self._identifier.identify(face_crop)
            emotion_probs = self._emotion_analyzer.predict(face_crop)
            emotion_label = self._emotion_analyzer.idx_to_label(np.argmax(emotion_probs))

            results["faces"].append({
                "bbox": [x1, y1, x2, y2],
                "face_id": face_id,
                "track_id": track_id,
                "emotion": emotion_label,
                "emotion_scores": emotion_probs.tolist()
            })

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

                camera_id = payload["camera_id"]
                self._frame_counter += 1

                should_analyze = (self._analysis_frequency <= 1) or \
                                (self._frame_counter % self._analysis_frequency == 0)

                if should_analyze:
                    result = self._handle_frame(frame, payload)
                    self._last_result_cache[camera_id] = result
                    self._kafka.produce(result)

                    frame_count += 1
                    if frame_count % 100 == 0:
                        elapsed = time.time() - start_time
                        fps = frame_count / elapsed if elapsed > 0 else 0
                        logging.info(f"Analyzed {frame_count} frames ({fps:.1f} FPS)")

                else:
                    last_result = self._last_result_cache.get(camera_id)
                    if last_result is not None:
                        inherited_result = {
                            "camera_id": camera_id,
                            "frame_id": payload["frame_id"],
                            "faces": last_result["faces"]
                        }
                        self._kafka.produce(inherited_result)
                        logging.info(f"Sent to kafka at timestamp: {payload['timestamp']}: {last_result['faces']}")
                    else:

                        empty_result = {
                            "camera_id": camera_id,
                            "frame_id": payload["frame_id"],
                            "faces": []
                        }
                        self._kafka.produce(empty_result)
                        logging.info(f"Sent to kafka at timestamp: {payload['timestamp']}: {last_result['faces']}")

            except Exception as e:
                logging.error(f"Processing error: {e}", exc_info=True)
