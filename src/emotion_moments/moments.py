"""Моменты, требующие внимания: кандидаты всех детекторов профиля, слияние пересекающихся и значимость."""

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List

from .detectors import ABRUPT
from .detectors import Candidate
from .detectors import detect_abrupt_changes
from .detectors import detect_face_voice_divergence
from .detectors import detect_flat_affect
from .detectors import detect_sustained
from .profile import Profile
from .series import EmotionSeries

TYPE_TITLES = {
    "abrupt_change":         "резкая смена эмоции",
    "sustained_anger":       "устойчивый гнев",
    "sustained_sadness":     "устойчивая грусть",
    "face_voice_divergence": "расхождение лица и голоса",
    "flat_affect":           "бедная мимика",
}


@dataclass
class Moment:
    """Момент: слитые пересекающиеся кандидаты; significance — по детекторам, level — high / medium / low."""
    id:           str
    start_s:      float
    end_s:        float
    types:        List[str]
    significance: float
    level:        str
    candidates:   List[Candidate] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def titles(self) -> List[str]:
        return [TYPE_TITLES.get(t, t) for t in self.types]

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "start_s": self.start_s, "end_s": self.end_s, "duration_s": round(self.duration_s, 2),
                "types": self.types, "significance": self.significance, "level": self.level,
                "candidates": [c.to_dict() for c in self.candidates]}


def candidate_significance(c: Candidate, profile: Profile) -> float:
    """
    Значимость кандидата = вес типа × интенсивность × уверенность × множитель длительности.

    Множитель длительности растёт от 0.5 до 1 к significance.ref_duration_s; у резкой смены он равен 1:
    она короткая по определению.

    Args:
        c       (Candidate): Кандидат.
        profile (Profile)  : Профиль.

    Returns:
        float: Значимость в [0, 1].
    """
    if c.type.startswith("sustained_"):
        weight = ((profile.detector("sustained").get("emotions") or {}).get(c.params.get("emotion")) or {}).get("weight", 1.0)
    else:
        weight = profile.detector(c.type).get("weight", 1.0)
    ref      = float(profile.significance.get("ref_duration_s", 30))
    duration = 1.0 if c.type == ABRUPT else 0.5 + 0.5 * min(1.0, c.duration_s / ref)
    return round(min(1.0, weight * c.intensity * c.confidence * duration), 3)


def find_candidates(series: EmotionSeries, profile: Profile) -> List[Candidate]:
    """Кандидаты всех детекторов, включённых в профиль, по времени начала."""
    out: List[Candidate] = []
    sustained = dict(profile.detector("sustained"))
    emotions  = sustained.pop("emotions", None) or {}
    for emotion in emotions:
        if emotion in series.labels:
            out += detect_sustained(series, emotion, **sustained)
    for name, fn in (("abrupt_change", detect_abrupt_changes), ("face_voice_divergence", detect_face_voice_divergence),
                     ("flat_affect", detect_flat_affect)):
        params = {k: v for k, v in profile.detector(name).items() if k != "weight"}
        if name in profile.detectors:
            out += fn(series, **params)
    return sorted(out, key=lambda c: (c.start_s, c.end_s))


def merge_candidates(candidates: List[Candidate], profile: Profile) -> List[Moment]:
    """
    Слияние пересекающихся (или разделённых не больше merge_gap_s) кандидатов в моменты.

    Значимость момента — наибольшая среди его кандидатов плюс extra_type_bonus за каждый дополнительный тип.

    Args:
        candidates (List[Candidate]): Кандидаты.
        profile    (Profile)        : Профиль.

    Returns:
        List[Moment]: Моменты по времени, id m1, m2, ...
    """
    groups: List[List[Candidate]] = []
    for c in sorted(candidates, key=lambda c: c.start_s):
        if groups and c.start_s <= max(x.end_s for x in groups[-1]) + profile.merge_gap_s:
            groups[-1].append(c)
        else:
            groups.append([c])

    bonus = float(profile.significance.get("extra_type_bonus", 0.1))
    out: List[Moment] = []
    for n, group in enumerate(groups, start=1):
        types = list(dict.fromkeys(c.type for c in group))
        sig   = round(min(1.0, max(candidate_significance(c, profile) for c in group) + bonus * (len(types) - 1)), 3)
        out.append(Moment(id=f"m{n}", start_s=min(c.start_s for c in group), end_s=max(c.end_s for c in group),
                          types=types, significance=sig, level=profile.level(sig), candidates=group))
    return out


def detect_moments(series: EmotionSeries, profile: Profile) -> List[Moment]:
    """
    Моменты сессии по профилю, от самого значимого.

    Args:
        series  (EmotionSeries): Ряд.
        profile (Profile)      : Профиль.

    Returns:
        List[Moment]: Моменты.
    """
    return sorted(merge_candidates(find_candidates(series, profile), profile), key=lambda m: -m.significance)
