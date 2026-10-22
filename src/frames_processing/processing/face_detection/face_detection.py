"""Детекторы лиц YuNet и MediaPipe с общим форматом результата [x, y, w, h, conf]."""

import logging
import cv2
import numpy as np

from typing import List
from typing import Literal
from typing import Optional
from typing import Tuple

Detection = List[float]


def geometry_post_filter(height: int, width: int, frame_height: int, frame_width: int) -> bool:
    """
    Отсев рамок по размеру, пропорциям и доле площади кадра.

    Args:
        height       (int): Высота рамки.
        width        (int): Ширина рамки.
        frame_height (int): Высота кадра.
        frame_width  (int): Ширина кадра.

    Returns:
        bool: Рамка похожа на лицо.
    """
    ratio      = height / max(width, 1)
    area       = width * height
    frame_area = frame_width * frame_height
    return not any([
        width <= 0 or height <= 0,
        width < 40 or height < 40,
        ratio < 0.75 or ratio > 1.7,
        area < 0.002 * frame_area or area > 0.5 * frame_area,
    ])


def _clip_box(x: float, y: float, w: float, h: float, frame_w: int, frame_h: int) -> Tuple[int, int, int, int]:
    x, y = max(0, int(x)), max(0, int(y))
    return x, y, min(frame_w - x, int(w)), min(frame_h - y, int(h))


class YuNetFaceDetector(object):
    """
    Детектор YuNet (cv2.FaceDetectorYN) — на его рамках обучалась модель эмоций CREMA-D.

    Кадр уменьшается до max_side по большей стороне: 480 — масштаб видео CREMA-D и вдвое дешевле 640,
    для мелких лиц нужно больше.
    """

    def __init__(self, min_detection_confidence: float = 0.6, model_path: Optional[str] = None,
                 max_side: int = 480, nms_threshold: float = 0.3):
        if model_path is None:
            from src.emotion_kit.preprocess import DEFAULT_YUNET
            model_path = str(DEFAULT_YUNET)
        self._threshold = min_detection_confidence
        self._max_side  = max_side
        self._det = cv2.FaceDetectorYN.create(
            str(model_path), "", (320, 320), min_detection_confidence, nms_threshold, 5000
        )
        self._size: Optional[Tuple[int, int]] = None

    def detect(self, image: np.ndarray) -> List[Detection]:
        """
        Детекция лиц.

        Args:
            image (np.ndarray): Кадр BGR.

        Returns:
            List[Detection]: Лица [x, y, w, h, conf] в координатах кадра.
        """
        if image is None or image.size == 0:
            logging.warning("Faces detector got empty image")
            return []

        orig_h, orig_w = image.shape[:2]
        scale          = min(1.0, self._max_side / max(orig_h, orig_w))
        small = image if scale == 1.0 else cv2.resize(
            image, (int(orig_w * scale), int(orig_h * scale)), interpolation=cv2.INTER_AREA
        )
        size = (small.shape[1], small.shape[0])
        if self._size != size:
            self._det.setInputSize(size)
            self._size = size

        _, faces   = self._det.detect(small)
        detections = []
        if faces is None:
            return detections
        for face in faces:
            conf = float(face[-1])
            if conf < self._threshold:
                continue
            x, y, w, h = _clip_box(*(face[:4] / scale), orig_w, orig_h)
            if not geometry_post_filter(h, w, orig_h, orig_w):
                continue
            detections.append([x, y, w, h, conf])
        return detections


class MediaPipeFaceDetector(object):
    """Детектор MediaPipe, прежний детектор по умолчанию; кадр уменьшается в scale раз."""

    def __init__(self, min_detection_confidence: float = 0.7, scale: float = 0.5):
        import mediapipe as mp
        self.face_detection = mp.solutions.face_detection.FaceDetection(
            min_detection_confidence=min_detection_confidence,
            model_selection=0
        )
        self._scale = scale

        dummy = np.zeros((128, 128, 3), dtype=np.uint8)
        for _ in range(3):
            self.face_detection.process(dummy)

    def detect(self, image: np.ndarray) -> List[Detection]:
        """
        Детекция лиц.

        Args:
            image (np.ndarray): Кадр BGR.

        Returns:
            List[Detection]: Лица [x, y, w, h, conf] в координатах кадра.
        """
        if image is None or image.size == 0:
            logging.warning("Faces detector got empty image")
            return []

        orig_h, orig_w = image.shape[:2]
        scale          = self._scale

        if scale != 1.0:
            small = cv2.resize(
                image,
                (int(orig_w * scale), int(orig_h * scale)),
                interpolation=cv2.INTER_LINEAR
            )
        else:
            small = image

        small_rgb  = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        results    = self.face_detection.process(small_rgb)
        detections = []

        if not results.detections:
            return detections

        small_h, small_w = small.shape[:2]
        for detection in results.detections:
            bbox = detection.location_data.relative_bounding_box
            conf = detection.score[0]

            x_min, y_min, width, height = _clip_box(
                bbox.xmin * small_w / scale, bbox.ymin * small_h / scale,
                bbox.width * small_w / scale, bbox.height * small_h / scale,
                orig_w, orig_h,
            )

            if not geometry_post_filter(height, width, orig_h, orig_w):
                continue

            detections.append([x_min, y_min, width, height, conf])

        return detections

    def __del__(self):
        if hasattr(self, "face_detection") and self.face_detection:
            self.face_detection.close()


def FaceDetector(min_detection_confidence: float = 0.6,
                 backend: Literal["yunet", "mediapipe"] = "yunet", **kwargs):
    """
    Фабрика детекторов лиц.

    Args:
        min_detection_confidence (float, optional)  : Порог уверенности. Defaults to 0.6.
        backend                  (Literal, optional): "yunet" или "mediapipe". Defaults to "yunet".

    Raises:
        ValueError: Неизвестный backend.

    Returns:
        Детектор с методом detect(image_bgr).
    """
    if backend == "yunet":
        return YuNetFaceDetector(min_detection_confidence, **kwargs)
    if backend == "mediapipe":
        return MediaPipeFaceDetector(min_detection_confidence, **kwargs)
    raise ValueError(f"Unknown face detector backend '{backend}'. Available: ['yunet', 'mediapipe']")
