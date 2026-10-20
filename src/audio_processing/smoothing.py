"""
Сглаживание голосовых оценок по окнам одной сессии.

Окна перекрываются и идут раз в hop_ms, поэтому достаточно EMA с
гистерезисом смены метки (как у видео). Окна без речи не меняют состояние:
последняя голосовая эмоция держится между фразами, а её «свежесть» слияние
оценит по pts.
"""
from typing import Optional

import numpy as np

from src.emotion_labels import label_of


class VoiceSmoother:
    def __init__(self, ema_coef: float = 0.6, switch_confidence: float = 0.35, switch_windows: int = 2):
        """
        Args:
            ema_coef: Вес истории в EMA (0 — без сглаживания).
            switch_confidence: Минимальная уверенность окна, чтобы считать его голосом за смену метки.
            switch_windows: Сколько таких окон подряд нужно для смены метки.
        """
        self.ema_coef = ema_coef
        self.switch_confidence = switch_confidence
        self.switch_windows = switch_windows
        self.probs: Optional[np.ndarray] = None
        self.label: Optional[str] = None
        self._switch_votes = 0

    def update(self, probs: np.ndarray) -> None:
        probs = np.asarray(probs, dtype=np.float64).ravel()
        if self.probs is None:
            self.probs = probs.copy()
            self.label = label_of(int(np.argmax(probs)))
            return
        self.probs = self.ema_coef * self.probs + (1 - self.ema_coef) * probs
        candidate = label_of(int(np.argmax(self.probs)))
        if candidate != self.label and probs.max() >= self.switch_confidence:
            self._switch_votes += 1
        else:
            self._switch_votes = max(0, self._switch_votes - 1)
        if self._switch_votes >= self.switch_windows:
            self.label = candidate
            self._switch_votes = 0
