"""
Окна кадров по трекам лица для модели эмоций по клипу.

Модель обучалась на 16 кадрах окна с одной медианной рамкой, запасом 25 % и временами на шкале 0..3 с.
В потоке кадры кодируются по мере поступления: трек прореживается до n_frames на окно, рамка —
скользящая медиана, раз в hop_ms по признакам последних window_ms запускается временная голова.
"""

import math
import cv2
import numpy as np

from collections import deque
from dataclasses import dataclass
from typing import Deque
from typing import Dict
from typing import Optional
from typing import Tuple

from src.emotion_labels import label_of

FACE_SIZE    = 224
FACE_MARGIN  = 0.25
TRAIN_FRAMES = 16
CLIP_SECONDS = 3.0


def frame_times(n_frames: int) -> np.ndarray:
    """
    Времена кадров окна на шкале обучения; для 8 кадров — центры 8 сегментов из 16.

    Args:
        n_frames (int): 8 или 16.

    Returns:
        np.ndarray: Времена, с.
    """
    if n_frames == TRAIN_FRAMES:
        idx = np.arange(TRAIN_FRAMES)
    else:
        edges = np.linspace(0, TRAIN_FRAMES, n_frames + 1)
        idx   = np.floor((edges[:-1] + edges[1:]) / 2).astype(int)
    return ((idx + 0.5) / TRAIN_FRAMES * CLIP_SECONDS).astype(np.float32)


def sample_indices(total: int, n: int) -> np.ndarray:
    return np.linspace(0, max(total - 1, 0), n).round().astype(int)


@dataclass(frozen=True)
class ClipConfig:
    """
    Параметры окна: n_frames кадров на window_ms, шаг hop_ms.

    Окно не считается, пока в нём меньше min_coverage кадров.
    """
    n_frames: int = 8
    window_ms: int = 3000
    hop_ms: int = 1000
    min_coverage: float = 0.6

    def __post_init__(self):
        if self.n_frames not in (8, TRAIN_FRAMES):
            raise ValueError(f"n_frames must be 8 or {TRAIN_FRAMES}, got {self.n_frames}")
        if self.window_ms <= 0 or self.hop_ms <= 0:
            raise ValueError("window_ms and hop_ms must be positive")
        if not 0 < self.min_coverage <= 1:
            raise ValueError("min_coverage must be in (0, 1]")

    @property
    def sample_interval_ms(self) -> float:
        return self.window_ms / self.n_frames

    @property
    def min_frames(self) -> int:
        return max(1, math.ceil(self.min_coverage * self.n_frames))


def box_iou(a, b) -> float:
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw                 = max(0.0, min(ax2, bx2) - max(a[0], b[0]))
    ih                 = max(0.0, min(ay2, by2) - max(a[1], b[1]))
    inter              = iw * ih
    union              = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


class BoxStabilizer:
    """
    Скользящая медиана рамок трека: дрожание рамки ухудшает качество модели.

    При резком скачке рамки (IoU с медианой ниже reset_iou) история сбрасывается, иначе медиана режет мимо лица.
    """

    def __init__(self, history: int = 7, reset_iou: float = 0.5):
        self._boxes: Deque[Tuple[float, float, float, float]] = deque(maxlen=history)
        self._reset_iou = reset_iou
        self._current: Optional[Tuple[int, int, int, int]] = None
        self.resets = 0

    def update(self, box) -> Tuple[int, int, int, int]:
        box = tuple(float(v) for v in box)
        if self._current is not None and box_iou(box, self._current) < self._reset_iou:
            self._boxes.clear()
            self.resets += 1
        self._boxes.append(box)
        self._current = tuple(int(round(v)) for v in np.median(np.asarray(self._boxes), axis=0))
        return self._current


def crop_face(frame_bgr: np.ndarray, box, margin: float = FACE_MARGIN, size: int = FACE_SIZE) -> Optional[np.ndarray]:
    """
    Кроп лица как при обучении: запас margin с каждой стороны, resize без сохранения пропорций, RGB.

    Args:
        frame_bgr (np.ndarray)     : Кадр BGR.
        box                        : Рамка (x, y, w, h).
        margin    (float, optional): Запас рамки. Defaults to FACE_MARGIN.
        size      (int, optional)  : Сторона кропа. Defaults to FACE_SIZE.

    Returns:
        Optional[np.ndarray]: Кроп RGB или None для вырожденной рамки.
    """
    H, W       = frame_bgr.shape[:2]
    x, y, w, h = box
    dx, dy     = int(w * margin), int(h * margin)
    x, y, w, h = x - dx, y - dy, w + 2 * dx, h + 2 * dy
    x, y       = max(0, x), max(0, y)
    w, h       = min(w, W - x), min(h, H - y)
    if w <= 1 or h <= 1:
        return None
    face = cv2.resize(frame_bgr[y:y + h, x:x + w], (size, size), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(face, cv2.COLOR_BGR2RGB)


class TrackClip:
    """
    Признаки кадров одного трека по PTS и расписание окон.

    Расписание отсчётов сдвигается в mark_sampled отдельно от add: признак может прийти позже из фонового энкодера.
    PTS, ушедший назад больше чем на два окна, начинает трек заново.
    """

    def __init__(self, config: ClipConfig):
        self.config = config
        self._feats: Deque[Tuple[float, np.ndarray]] = deque()
        self._next_sample_ms: Optional[float] = None
        self._next_window_ms: Optional[float] = None
        self.windows = 0

    def wants_frame(self, pts_ms: float) -> bool:
        if self._next_sample_ms is None or pts_ms + 0.5 >= self._next_sample_ms:
            return True
        return pts_ms < self._next_sample_ms - 2 * self.config.window_ms

    def mark_sampled(self, pts_ms: float) -> None:
        interval = self.config.sample_interval_ms
        if self._next_sample_ms is not None and pts_ms < self._next_sample_ms - 2 * self.config.window_ms:
            self.reset()
        if self._next_sample_ms is None or pts_ms - self._next_sample_ms > 2 * interval:
            self._next_sample_ms = pts_ms + interval
        else:
            self._next_sample_ms += interval

    def add(self, pts_ms: float, feat: np.ndarray, sampled: bool = False) -> None:
        """
        Признак кадра в окно; опоздавшие и выпавшие из окна отбрасываются.

        Args:
            pts_ms  (float)         : Время кадра.
            feat    (np.ndarray)    : Признак кадра.
            sampled (bool, optional): Расписание уже сдвинуто mark_sampled. Defaults to False.
        """
        if not sampled:
            self.mark_sampled(pts_ms)
        if self._feats and pts_ms <= self._feats[-1][0]:
            return
        self._feats.append((pts_ms, np.asarray(feat, np.float32)))
        while self._feats and self._feats[0][0] <= pts_ms - self.config.window_ms:
            self._feats.popleft()

    def window(self, pts_ms: float) -> Optional[Dict]:
        """
        Окно, если пора его считать.

        Args:
            pts_ms (float): Время текущего кадра.

        Returns:
            Optional[Dict]: feats, times, start_ms, end_ms и число реальных кадров frames или None.
        """
        if self._next_window_ms is not None and pts_ms + 0.5 < self._next_window_ms:
            return None
        items = [(t, f) for t, f in self._feats if t > pts_ms - self.config.window_ms]
        if len(items) < self.config.min_frames:
            return None
        n   = self.config.n_frames
        idx = sample_indices(len(items), n)
        if self._next_window_ms is None or pts_ms - self._next_window_ms > self.config.hop_ms:
            self._next_window_ms = pts_ms + self.config.hop_ms
        else:
            self._next_window_ms += self.config.hop_ms
        self.windows += 1
        return {
            "feats": np.stack([items[i][1] for i in idx]),
            "times": frame_times(n),
            "start_ms": items[0][0],
            "end_ms": items[-1][0],
            "frames": len(items),
        }

    def reset(self) -> None:
        self._feats.clear()
        self._next_sample_ms = None
        self._next_window_ms = None

    def __len__(self) -> int:
        return len(self._feats)


class WindowSmoother:
    """
    EMA вероятностей по окнам с порогом отказа.

    keep 0.5 — примерно среднее по 2–3 окнам; максимум ниже undetermined_below — метка None.
    """

    def __init__(self, keep: float = 0.5, undetermined_below: float = 0.4):
        if not 0 <= keep < 1:
            raise ValueError("keep must be in [0, 1)")
        self.keep               = keep
        self.undetermined_below = undetermined_below
        self.probs: Optional[np.ndarray] = None

    def update(self, probs: np.ndarray) -> Tuple[Optional[str], np.ndarray]:
        probs = np.asarray(probs, np.float32)
        if self.probs is None or self.probs.shape != probs.shape:
            self.probs = probs.copy()
        else:
            self.probs = self.keep * self.probs + (1 - self.keep) * probs
        return self.label, self.probs

    @property
    def label(self) -> Optional[str]:
        if self.probs is None or float(self.probs.max()) < self.undetermined_below:
            return None
        return label_of(int(self.probs.argmax()))
