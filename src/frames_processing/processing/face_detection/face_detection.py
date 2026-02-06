import numpy as np
from typing import List, Tuple
import mediapipe as mp
import cv2
import logging

mp_face_detection = mp.solutions.face_detection
mp_drawing = mp.solutions.drawing_utils

class FaceDetector(object):
    def __init__(self, min_detection_confidence: float = 0.5):
        """
        #### Детектор лиц на изображении. 
        
        :param min_detection_confidence: Порог уверенности алгоритма.
        :type min_detection_confidence: float
        """
        self.face_detection = mp_face_detection.FaceDetection(
            min_detection_confidence=min_detection_confidence,
            model_selection=0
        )
        self._next_id = 0

    def detect(self, image: np.ndarray) -> List[Tuple[float, float, float, float, float]]:
        """
        #### Детекция лиц на изображении.
        
        :param image: Изображение в формате BGR или RGB (H, W, 3).
        :type image: np.ndarray
        :return: Список информации о лицах в виде (x_left, y_left, width, height, confidence).
        :rtype: List[Tuple[float, float, float, float, float]]
        """
        if image is None or image.size == 0:
            logging.warning("Faces detector got empty image")
            return []

        if image.shape[2] == 3:
            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        else:
            rgb_image = image

        results = self.face_detection.process(rgb_image)
        detections = []

        if results.detections:
            h, w = image.shape[:2]
            for detection in results.detections:
                bbox = detection.location_data.relative_bounding_box
                conf = detection.score[0]
                x_min = int(bbox.xmin * w)
                y_min = int(bbox.ymin * h)
                width = int(bbox.width * w)
                height = int(bbox.height * h)

                x_min = max(0, x_min)
                y_min = max(0, y_min)
                width = min(w - x_min, width)
                height = min(h - y_min, height)

                if width <= 0 or height <= 0:
                    continue

                detections.append([x_min, y_min, width, height, conf])

        return detections

    def __del__(self):
        if hasattr(self, 'face_detection') and self.face_detection:
            self.face_detection.close()
