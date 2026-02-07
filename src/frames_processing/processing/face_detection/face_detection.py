import numpy as np
from typing import List, Tuple
import mediapipe as mp
import cv2
import logging

from .filters import fast_face_filter

mp_face_detection = mp.solutions.face_detection

class FaceDetector(object):
    def __init__(self, min_detection_confidence: float = 0.7):
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

        dummy = np.zeros((128, 128, 3), dtype=np.uint8)
        for _ in range(3):
            self.face_detection.process(dummy)

    def _geometry_post_filters(self, height: int, width: int, frame_height: int, frame_width: int) -> bool:
        """
        #### Фильтр на основе базовых геометрических соотношений.
        
        :param height: Высота `bounding box`-a.
        :type height: int
        :param width: Ширина `bounding box`-a.
        :type width: int
        :param frame_height: Высота кадра.
        :type frame_height: int
        :param frame_width: Ширина кадра
        :type frame_width: int
        :return: 
        :rtype: bool
        """
        ratio = height / max(width, 1)
        area = width * height
        frame_area = frame_width * frame_height
        return not any([
            width <= 0 or height <= 0,
            width < 40 or height < 40,
            ratio < 0.75 or ratio > 1.7,
            area < 0.002 * frame_area or area > 0.5 * frame_area,
        ])

    def detect(self, image: np.ndarray, scale: float = 0.5) -> List[Tuple[float, float, float, float, float]]:
        """
        #### Детекция лиц на изображении.
        
        :param image: Изображение в формате BGR или RGB (H, W, 3).
        :type image: np.ndarray
        :param scale: Коэффицент уменьшения изображения при детекции лиц для увеличения fps.
        :type scale: float
        :return: Список информации о лицах в виде (x_left, y_left, width, height, confidence).
        :rtype: List[Tuple[float, float, float, float, float]]
        """
        if image is None or image.size == 0:
            logging.warning("Faces detector got empty image")
            return []

        orig_h, orig_w = image.shape[:2]

        if scale != 1.0:
            small = cv2.resize(
                image,
                (int(orig_w * scale), int(orig_h * scale)),
                interpolation=cv2.INTER_LINEAR
            )
        else:
            small = image

        small_rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        results = self.face_detection.process(small_rgb)
        detections = []

        if not results.detections:
            return detections

        small_h, small_w = small.shape[:2]
        for detection in results.detections:
            bbox = detection.location_data.relative_bounding_box
            conf = detection.score[0]
            
            x_min = int(bbox.xmin * small_w / scale)
            y_min = int(bbox.ymin * small_h / scale)
            width = int(bbox.width * small_w / scale)
            height = int(bbox.height * small_h / scale)

            x_min = max(0, x_min)
            y_min = max(0, y_min)
            width = min(orig_w - x_min, width)
            height = min(orig_h - y_min, height)

            if not self._geometry_post_filters(height, width, orig_h, orig_w):
                continue

            detections.append([x_min, y_min, width, height, conf])

        return detections

    def __del__(self):
        if hasattr(self, 'face_detection') and self.face_detection:
            self.face_detection.close()
