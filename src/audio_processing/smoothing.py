"""Сглаживание голосовых оценок по окнам сессии."""

import numpy as np

from typing import Optional

from src.emotion_labels import label_of


class VoiceSmoother:
    """
    EMA с гистерезисом смены метки и порогом отказа.

    Метка меняется после switch_windows уверенных окон подряд; из «не определено» уверенная метка берётся сразу.
    Окна без речи не обновляют состояние — эмоция держится между фразами.
    """

    def __init__(self, ema_coef: float = 0.5, switch_confidence: float = 0.35, switch_windows: int = 2,
                 undetermined_below: float = 0.4):
        self.ema_coef           = ema_coef
        self.switch_confidence  = switch_confidence
        self.switch_windows     = switch_windows
        self.undetermined_below = undetermined_below
        self.probs: Optional[np.ndarray] = None
        self._held: Optional[str] = None
        self._switch_votes = 0

    @property
    def label(self) -> Optional[str]:
        if self.probs is None or float(self.probs.max()) < self.undetermined_below:
            return None
        return self._held

    def update(self, probs: np.ndarray) -> None:
        probs = np.asarray(probs, dtype=np.float64).ravel()
        if self.probs is None:
            self.probs = probs.copy()
            self._held = label_of(int(np.argmax(probs)))
            return
        was_undetermined = self.label is None
        self.probs       = self.ema_coef * self.probs + (1 - self.ema_coef) * probs
        candidate        = label_of(int(np.argmax(self.probs)))
        if was_undetermined:
            self._held         = candidate
            self._switch_votes = 0
            return
        if candidate != self._held and probs.max() >= self.switch_confidence:
            self._switch_votes += 1
        else:
            self._switch_votes = max(0, self._switch_votes - 1)
        if self._switch_votes >= self.switch_windows:
            self._held         = candidate
            self._switch_votes = 0
