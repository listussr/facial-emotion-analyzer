"""
Голосовое состояние камеры для слияния.

Окна голоса приходят позже кадров — после своего конца. К окну лица подбирается окно голоса с наибольшим
перекрытием (best_window), для голоса сцены — последнее окно с речью до кадра (evidence_at).
Окна без речи не стирают голосовую оценку, но и не освежают её.
"""

from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

FUTURE_TOLERANCE_MS = 200


def overlap_ms(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


@dataclass
class VoiceEvidence:
    """Голосовая оценка сцены на момент кадра: age_ms — от конца последнего окна с речью."""
    scores:      List[float]
    label:       Optional[str]
    speech_prob: float
    age_ms:      int
    window_seq:  int
    speaking:    bool


class VoiceState:
    """Последние history окон audio-analytics камеры, отсортированные по концу окна."""

    def __init__(self, history: int = 64):
        self.history = history
        self._windows: List[Dict[str, Any]] = []
        self.windows_seen = 0
        self.out_of_order = 0

    def add(self, window: Dict[str, Any]) -> None:
        self.windows_seen += 1
        end = window["pts_end_ms"]
        if self._windows and end < self._windows[-1]["pts_end_ms"]:
            self.out_of_order += 1
        self._windows.append(window)
        self._windows.sort(key=lambda w: w["pts_end_ms"])
        if len(self._windows) > self.history:
            del self._windows[: len(self._windows) - self.history]

    def evidence_at(self, pts_ms: int) -> Optional[VoiceEvidence]:
        """
        Голос сцены для кадра: последнее окно с речью, закончившееся не позже кадра с допуском.

        Args:
            pts_ms (int): Время кадра.

        Returns:
            Optional[VoiceEvidence]: Оценка или None, если речи ещё не было.
        """
        limit  = pts_ms + FUTURE_TOLERANCE_MS
        before = [w for w in self._windows if w["pts_end_ms"] <= limit]
        if not before:
            return None
        speech = [w for w in before if w.get("is_speech") and w.get("smoothed_scores")]
        if not speech:
            return None
        last = speech[-1]
        return VoiceEvidence(
            scores=list(last["smoothed_scores"]),
            label=last.get("smoothed_label"),
            speech_prob=float(last.get("speech_prob") or 0.0),
            age_ms=max(0, int(pts_ms - last["pts_end_ms"])),
            window_seq=int(last.get("window_seq", -1)),
            speaking=before[-1] is last,
        )

    def best_window(self, start_ms: float, end_ms: float, min_overlap_ms: float) -> Optional[Dict[str, Any]]:
        """
        Окно с речью и логитами, сильнее всего перекрывающее окно лица.

        Args:
            start_ms       (float): Начало окна лица.
            end_ms         (float): Конец окна лица.
            min_overlap_ms (float): Минимальное перекрытие.

        Returns:
            Optional[Dict[str, Any]]: Окно голоса или None.
        """
        best, best_overlap = None, 0.0
        for w in self._windows:
            if not w.get("is_speech") or not w.get("logits"):
                continue
            ov = overlap_ms(start_ms, end_ms, w["pts_start_ms"], w["pts_end_ms"])
            if ov > best_overlap or (ov == best_overlap and best is not None
                                     and w["pts_end_ms"] > best["pts_end_ms"]):
                best, best_overlap = w, ov
        if best is None or best_overlap < min_overlap_ms:
            return None
        return best

    def latest_speech_window(self, pts_ms: int, max_age_ms: int) -> Optional[Dict[str, Any]]:
        """
        Последнее окно с речью до кадра не старше max_age_ms — для лица, у которого ещё нет окна.

        Args:
            pts_ms     (int): Время кадра.
            max_age_ms (int): Максимальный возраст окна.

        Returns:
            Optional[Dict[str, Any]]: Окно голоса или None.
        """
        limit = pts_ms + FUTURE_TOLERANCE_MS
        for w in reversed(self._windows):
            if w["pts_end_ms"] > limit:
                continue
            if pts_ms - w["pts_end_ms"] > max_age_ms:
                return None
            if w.get("is_speech") and w.get("logits"):
                return w
        return None

    def __len__(self) -> int:
        return len(self._windows)
