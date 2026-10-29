"""
Детекторы кандидатов в моменты, требующие внимания: детерминированные правила на секундном ряде.

Каждый детектор возвращает кандидатов с интервалом, метриками, параметрами, интенсивностью и уверенностью
в [0, 1]. Уверенность отражает качество данных: долю секунд с лицом и долю рамок, предсказанных трекером.
"""

import numpy as np

from collections import Counter
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple

from .series import EmotionSeries
from .series import jsd

ABRUPT     = "abrupt_change"
DIVERGENCE = "face_voice_divergence"
FLAT       = "flat_affect"

# Детектор лиц работает раз в DETECTION_FREQUENCY кадров (по умолчанию 2), между запусками рамку штатно
# предсказывает трекер: до этой доли предсказанные рамки — норма, а не потеря лица
PREDICTED_NORM = 0.5


def sustained_type(emotion: str) -> str:
    return "sustained_sadness" if emotion == "sad" else f"sustained_{emotion}"


@dataclass
class Candidate:
    """Кандидат в момент: интервал в секундах от начала сессии."""
    type:       str
    start_s:    float
    end_s:      float
    intensity:  float
    confidence: float
    metrics:    Dict[str, Any] = field(default_factory=dict)
    params:     Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["duration_s"] = round(self.duration_s, 2)
        return out


def _runs(flags: np.ndarray, max_gap: int) -> List[Tuple[int, int]]:
    """Отрезки [i, j) истинных значений; провалы не длиннее max_gap склеиваются."""
    out: List[Tuple[int, int]] = []
    start = last = None
    for i, ok in enumerate(flags):
        if not ok:
            continue
        if start is None:
            start = i
        elif i - last - 1 > max_gap:
            out.append((start, last + 1))
            start = i
        last = i
    if start is not None:
        out.append((start, last + 1))
    return out


def _confidence(s: EmotionSeries, sl: slice, modality: str = "fused") -> float:
    """Доля секунд с данными; снижается, если трекер предсказывал рамки чаще штатного (лицо теряется)."""
    coverage  = float(np.mean(s.valid(modality)[sl])) if sl.stop > sl.start else 0.0
    predicted = float(np.mean(s.predicted[sl])) if sl.stop > sl.start else 0.0
    excess    = max(0.0, predicted - PREDICTED_NORM) / (1 - PREDICTED_NORM)
    return round(coverage * (1 - 0.5 * excess), 3)


def _quality(s: EmotionSeries, sl: slice) -> Dict[str, float]:
    return {"face_present": round(float(np.mean(s.face_present[sl])), 3),
            "predicted": round(float(np.mean(s.predicted[sl])), 3),
            "speech": round(float(np.mean(s.speech[sl])), 3)}


def detect_sustained(s: EmotionSeries, emotion: str, enter_p: float = 0.6, exit_delta: float = 0.1,
                     min_duration_s: float = 8, max_gap_s: float = 2) -> List[Candidate]:
    """
    Устойчивая эмоция: вероятность не ниже enter_p, с гистерезисом — интервал держится, пока она не ниже
    enter_p − exit_delta. Секунды без данных не длиннее max_gap_s интервал не рвут.

    Args:
        s              (EmotionSeries)   : Ряд.
        emotion        (str)             : Эмоция (метка из s.labels).
        enter_p        (float, optional) : Порог входа. Defaults to 0.6.
        exit_delta     (float, optional) : Гистерезис выхода. Defaults to 0.1.
        min_duration_s (float, optional) : Минимальная длительность, с. Defaults to 8.
        max_gap_s      (float, optional) : Допустимый провал данных, с. Defaults to 2.

    Returns:
        List[Candidate]: Кандидаты.
    """
    p        = s.fused[:, s.index(emotion)]
    max_gap  = int(round(max_gap_s / s.step_s))
    params   = {"emotion": emotion, "enter_p": enter_p, "exit_delta": exit_delta, "min_duration_s": min_duration_s,
                "max_gap_s": max_gap_s}
    out: List[Candidate] = []
    start = last = None
    gap   = 0

    def close() -> None:
        if start is None:
            return
        sl  = slice(start, last + 1)
        dur = (last + 1 - start) * s.step_s
        if dur < min_duration_s:
            return
        vals = p[sl][~np.isnan(p[sl])]
        out.append(Candidate(
            type=sustained_type(emotion), start_s=round(start * s.step_s, 2), end_s=round((last + 1) * s.step_s, 2),
            intensity=round(float(vals.mean()), 3), confidence=_confidence(s, sl),
            metrics={"mean_p": round(float(vals.mean()), 3), "max_p": round(float(vals.max()), 3),
                     **_quality(s, sl)},
            params=params))

    for i, v in enumerate(p):
        if np.isnan(v):
            if start is not None:
                gap += 1
                if gap > max_gap:
                    close()
                    start, gap = None, 0
            continue
        inside = v >= enter_p or (start is not None and v >= enter_p - exit_delta)
        if inside:
            if start is None:
                start = i
            last, gap = i, 0
        elif start is not None:
            close()
            start, gap = None, 0
    close()
    return out


def detect_abrupt_changes(s: EmotionSeries, window_s: float = 3, min_jsd: float = 0.3, min_new_p: float = 0.5,
                          min_coverage: float = 0.67, suppress_s: float = 5) -> List[Candidate]:
    """
    Резкая смена: распределения за window_s до и после секунды сильно различаются (JSD ≥ min_jsd), ведущая
    эмоция меняется и новая держится с вероятностью ≥ min_new_p. Из близких смен остаётся самая сильная.

    Args:
        s            (EmotionSeries)   : Ряд.
        window_s     (float, optional) : Окно сравнения до и после, с. Defaults to 3.
        min_jsd      (float, optional) : Порог расстояния. Defaults to 0.3.
        min_new_p    (float, optional) : Порог вероятности новой эмоции. Defaults to 0.5.
        min_coverage (float, optional) : Доля секунд с данными в каждом окне. Defaults to 0.67.
        suppress_s   (float, optional) : Подавление соседних смен, с. Defaults to 5.

    Returns:
        List[Candidate]: Кандидаты.
    """
    w      = max(1, int(round(window_s / s.step_s)))
    valid  = s.valid()
    params = {"window_s": window_s, "min_jsd": min_jsd, "min_new_p": min_new_p, "min_coverage": min_coverage,
              "suppress_s": suppress_s}
    found  = []
    for i in range(w, len(s) - w + 1):
        before, after = slice(i - w, i), slice(i, i + w)
        if valid[before].mean() < min_coverage or valid[after].mean() < min_coverage:
            continue
        mb, ma = s.mean_probs(before), s.mean_probs(after)
        d      = jsd(mb, ma)
        db, da = int(mb.argmax()), int(ma.argmax())
        if d >= min_jsd and db != da and ma[da] >= min_new_p:
            found.append((d, i, db, da, float(mb[db]), float(ma[da])))

    out: List[Candidate] = []
    taken: List[int] = []
    suppress = int(round(suppress_s / s.step_s))
    for d, i, db, da, pb, pa in sorted(found, reverse=True):
        if any(abs(i - j) <= suppress for j in taken):
            continue
        taken.append(i)
        sl = slice(i - w, i + w)
        out.append(Candidate(
            type=ABRUPT, start_s=round((i - w) * s.step_s, 2), end_s=round((i + w) * s.step_s, 2),
            intensity=round(min(1.0, d), 3), confidence=_confidence(s, sl),
            metrics={"at_s": round(i * s.step_s, 2), "jsd": round(d, 3), "from": s.labels[db], "from_p": round(pb, 3),
                     "to": s.labels[da], "to_p": round(pa, 3), **_quality(s, sl)},
            params=params))
    return sorted(out, key=lambda c: c.start_s)


def detect_face_voice_divergence(s: EmotionSeries, min_p: float = 0.5, min_jsd: float = 0.3, min_speech: float = 0.5,
                                 min_duration_s: float = 4, max_gap_s: float = 2) -> List[Candidate]:
    """
    Расхождение лица и голоса: во время речи ведущие эмоции лица и голоса разные, обе уверенные (≥ min_p),
    распределения далеки (JSD ≥ min_jsd) — дольше min_duration_s.

    Args:
        s              (EmotionSeries)   : Ряд.
        min_p          (float, optional) : Уверенность каждой модальности. Defaults to 0.5.
        min_jsd        (float, optional) : Порог расстояния. Defaults to 0.3.
        min_speech     (float, optional) : Доля кадров секунды с речью. Defaults to 0.5.
        min_duration_s (float, optional) : Минимальная длительность, с. Defaults to 4.
        max_gap_s      (float, optional) : Допустимый провал, с. Defaults to 2.

    Returns:
        List[Candidate]: Кандидаты.
    """
    params = {"min_p": min_p, "min_jsd": min_jsd, "min_speech": min_speech, "min_duration_s": min_duration_s,
              "max_gap_s": max_gap_s}
    flags  = np.zeros(len(s), bool)
    dists  = np.zeros(len(s))
    for i in range(len(s)):
        f, v = s.face[i], s.voice[i]
        if np.isnan(f[0]) or np.isnan(v[0]) or s.speech[i] < min_speech:
            continue
        df, dv   = int(f.argmax()), int(v.argmax())
        dists[i] = jsd(f, v)
        flags[i] = df != dv and f[df] >= min_p and v[dv] >= min_p and dists[i] >= min_jsd

    out: List[Candidate] = []
    for i, j in _runs(flags, int(round(max_gap_s / s.step_s))):
        if (j - i) * s.step_s < min_duration_s:
            continue
        sl    = slice(i, j)
        rows  = [k for k in range(i, j) if flags[k]]
        faces = Counter(s.labels[int(s.face[k].argmax())] for k in rows)
        voice = Counter(s.labels[int(s.voice[k].argmax())] for k in rows)
        out.append(Candidate(
            type=DIVERGENCE, start_s=round(i * s.step_s, 2), end_s=round(j * s.step_s, 2),
            intensity=round(float(dists[rows].mean()), 3), confidence=round(len(rows) / (j - i), 3),
            metrics={"face": faces.most_common(1)[0][0], "voice": voice.most_common(1)[0][0],
                     "mean_jsd": round(float(dists[rows].mean()), 3), **_quality(s, sl)},
            params=params))
    return out


def detect_flat_affect(s: EmotionSeries, window_s: float = 60, max_variability: float = 0.03,
                       min_coverage: float = 0.7) -> List[Candidate]:
    """
    Бедная мимика: в окнах по window_s (с шагом в половину окна) распределение эмоций почти не меняется —
    средний JSD секунд до среднего по окну не выше max_variability. Соседние такие окна сливаются.

    Args:
        s               (EmotionSeries)   : Ряд.
        window_s        (float, optional) : Окно, с. Defaults to 60.
        max_variability (float, optional) : Порог изменчивости. Defaults to 0.03.
        min_coverage    (float, optional) : Доля секунд с данными в окне. Defaults to 0.7.

    Returns:
        List[Candidate]: Кандидаты.
    """
    params = {"window_s": window_s, "max_variability": max_variability, "min_coverage": min_coverage}
    w      = int(round(window_s / s.step_s))
    valid  = s.valid()
    hits: List[Tuple[int, int, float]] = []
    for i in range(0, max(0, len(s) - w + 1), max(1, w // 2)):
        sl = slice(i, i + w)
        if valid[sl].mean() < min_coverage:
            continue
        rows = s.fused[sl][valid[sl]]
        mean = rows.mean(axis=0)
        var  = float(np.mean([jsd(r, mean) for r in rows]))
        if var <= max_variability:
            hits.append((i, i + w, var))

    segments: List[List[Any]] = []
    for i, j, var in hits:
        if segments and i <= segments[-1][1]:
            segments[-1][1] = j
            segments[-1][2].append(var)
        else:
            segments.append([i, j, [var]])

    out: List[Candidate] = []
    for i, j, vars_ in segments:
        sl     = slice(i, j)
        var    = float(np.mean(vars_))
        shares = s.dominant_shares(sl)
        top    = max(shares, key=shares.get) if shares else None
        out.append(Candidate(
            type=FLAT, start_s=round(i * s.step_s, 2), end_s=round(j * s.step_s, 2),
            intensity=round(float(np.clip(1 - var / max_variability, 0.2, 1.0)), 3), confidence=_confidence(s, sl),
            metrics={"variability": round(var, 4), "dominant": top, "dominant_share": shares.get(top), **_quality(s, sl)},
            params=params))
    return out
