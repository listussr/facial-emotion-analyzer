"""
Слияние оценок лица и голоса по окнам алгоритмами emotion_kit.

Методы: stack_hidden (по умолчанию, логрегрессия на эмбеддингах, лучший при шуме), stack_logits,
weighted_logits[_robust], confidence_weighted, mean_probs, audio_only, video_only. Без одной модальности
метод переходит на оставшуюся. Параметры обучены на выходах именно моделей из models/crema.
"""

import logging
import numpy as np

from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from src.emotion_labels import NUM_EMOTIONS
from src.emotion_labels import label_of

_log = logging.getLogger("fusion.fuse")

METHODS = ("stack_hidden", "stack_logits", "weighted_logits", "weighted_logits_robust",
           "confidence_weighted", "mean_probs", "audio_only", "video_only")
DEFAULT_METHOD    = "stack_hidden"
UNTRAINED_METHODS = ("confidence_weighted", "mean_probs", "audio_only", "video_only")
FALLBACK_METHOD   = "mean_probs"


@dataclass(frozen=True)
class FusionParams:
    """Метод слияния; voice_max_age_ms — для голоса без окна лица, min_overlap_ms — для пары окон."""
    method:           str = DEFAULT_METHOD
    voice_max_age_ms: int = 3000
    min_overlap_ms:   int = 1000


def _modality(window: Optional[Dict[str, Any]]) -> Optional[Dict[str, np.ndarray]]:
    if not window:
        return None
    logits = window.get("logits")
    if logits is None or len(logits) != NUM_EMOTIONS:
        return None
    emb = window.get("emb")
    return {
        "logits": np.asarray(logits, np.float32),
        "emb": np.asarray(emb, np.float32) if emb is not None else None,
    }


class ModalityFusion:
    """
    Обёртка над emotion_kit.fusion.Fusion.

    Без fusion_params.json обученные методы заменяются на mean_probs; без эмбеддинга у модели
    stack_hidden заменяется на stack_logits.
    """

    def __init__(self, params_path=None):
        from src.emotion_kit.fusion import Fusion
        try:
            self._kit    = Fusion(params_path)
            self.trained = True
        except (FileNotFoundError, OSError) as e:
            _log.warning(f"fusion params not available ({e}); trained methods fall back to {FALLBACK_METHOD}")
            self._kit        = Fusion.__new__(Fusion)
            self._kit.params = {}
            self.trained     = False

    def resolve(self, method: str) -> str:
        if method not in METHODS:
            return DEFAULT_METHOD if self.trained else FALLBACK_METHOD
        if not self.trained and method not in UNTRAINED_METHODS:
            return FALLBACK_METHOD
        return method

    def fuse(self, method: str, face_window: Optional[Dict[str, Any]],
             voice_window: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Слияние окна лица и окна голоса.

        Args:
            method       (str)                     : Метод слияния.
            face_window  (Optional[Dict[str, Any]]): Окно лица с logits и emb.
            voice_window (Optional[Dict[str, Any]]): Окно голоса с logits и emb.

        Returns:
            Optional[Dict[str, Any]]: probs, label и фактически применённый used; None — нет нужной модальности.
        """
        v = _modality(face_window)
        a = _modality(voice_window)
        if a is None and v is None:
            return None
        method = self.resolve(method)
        if method == "audio_only" and a is None or method == "video_only" and v is None:
            return None
        note = ""
        if method == "stack_hidden" and a is not None and v is not None and (a["emb"] is None or v["emb"] is None):
            method, note = "stack_logits", "stack_hidden -> "
        out   = self._kit.fuse(method, a, v)
        probs = np.asarray(out["probs"], np.float64)
        return {
            "probs": [round(float(p), 5) for p in probs],
            "label": label_of(int(probs.argmax())),
            "used": note + out["method"],
        }


class FusedSmoother:
    """
    EMA слитых вероятностей по окнам с порогом отказа.

    Повторное слияние того же окна (пришёл лучше совпадающий голос) заменяет его вклад, а не добавляет второй.
    """

    def __init__(self, keep: float = 0.5, undetermined_below: float = 0.4):
        self.keep               = keep
        self.undetermined_below = undetermined_below
        self.probs: Optional[np.ndarray] = None
        self._before_last: Optional[np.ndarray] = None
        self._last_key: Any = None

    def update(self, key: Any, probs: List[float]) -> None:
        """
        Обновление по окну.

        Args:
            key   (Any)        : Идентификатор окна лица или, без него, окна голоса.
            probs (List[float]): Слитые вероятности.
        """
        probs = np.asarray(probs, np.float64)
        if key != self._last_key:
            self._before_last = self.probs
            self._last_key    = key
        base       = self._before_last
        self.probs = probs.copy() if base is None else self.keep * base + (1 - self.keep) * probs

    @property
    def label(self) -> Optional[str]:
        if self.probs is None or float(self.probs.max()) < self.undetermined_below:
            return None
        return label_of(int(self.probs.argmax()))

    @property
    def scores(self) -> List[float]:
        return [] if self.probs is None else [round(float(p), 5) for p in self.probs]
