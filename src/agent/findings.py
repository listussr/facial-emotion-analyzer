"""Вердикты модели по моментам и данные разбора, из которых собирается отчёт."""

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional

from src.emotion_moments import EmotionSeries
from src.emotion_moments import Moment
from src.emotion_moments import Profile

Verdict = Literal["attention", "likely_artifact", "not_significant"]

VERDICT_RU   = {"attention": "стоит посмотреть", "likely_artifact": "вероятно, артефакт",
                "not_significant": "незначимо", None: "без оценки модели"}
VERDICT_MARK = {"attention": "●", "likely_artifact": "◐", "not_significant": "○", None: "◇"}
VERDICT_RANK = {"attention": 0, None: 1, "likely_artifact": 2, "not_significant": 3}
LEVEL_RU     = {"high": "высокая", "medium": "средняя", "low": "низкая"}


@dataclass
class Evidence:
    """Факт из результата ручки; source — имя ручки или id её вызова."""
    fact:   str
    source: str = ""


@dataclass
class Finding:
    """Вердикт модели по моменту (submit_finding); significance — уровень по мнению модели."""
    moment_id:      str
    verdict:        Verdict
    confidence:     str = "medium"
    significance:   Optional[str] = None
    summary:        str = ""
    explanation:    str = ""
    evidence:       List[Evidence] = field(default_factory=list)
    operator_check: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RunData:
    """
    Всё, из чего собирается отчёт разбора.

    findings пуст, если модель не использовалась; момент без вердикта (модель не ответила или ответ не прошёл
    проверку) показывается с данными детекторов. headline и summary — тексты модели (иначе шаблонные);
    notes — замечания разбора (недоступна модель, исчерпано время).
    """
    run_id:      str
    session_id:  str
    created_at:  str
    series:      EmotionSeries
    profile:     Profile
    overview:    Dict[str, Any]
    moments:     List[Moment]
    privacy:     str = "local"
    model:       Optional[str] = None
    status:      str = "done"
    meta:        Optional[Dict[str, Any]] = None
    findings:    Dict[str, Finding] = field(default_factory=dict)
    headline:    Optional[str] = None
    summary:     Optional[str] = None
    top_k:       int = 10
    notes:       List[str] = field(default_factory=list)
    finished_at: Optional[str] = None

    def verdict(self, moment_id: str) -> Optional[str]:
        f = self.findings.get(moment_id)
        return f.verdict if f else None

    def ordered(self) -> List[Moment]:
        """Моменты по вердикту (сначала «стоит посмотреть»), затем по значимости детекторов."""
        return sorted(self.moments, key=lambda m: (VERDICT_RANK[self.verdict(m.id)], -m.significance))

    def split(self):
        """Карточки (первые top_k без вердикта «незначимо») и остальные кандидаты для таблицы."""
        cards, rest = [], []
        for m in self.ordered():
            if len(cards) < self.top_k and self.verdict(m.id) != "not_significant":
                cards.append(m)
            else:
                rest.append(m)
        return cards, rest

    def counts(self) -> Dict[Optional[str], int]:
        out: Dict[Optional[str], int] = {}
        for m in self.moments:
            v = self.verdict(m.id)
            out[v] = out.get(v, 0) + 1
        return out

    def findings_document(self) -> Dict[str, Any]:
        """findings.json: моменты с данными детекторов и вердиктами модели."""
        return {
            "run_id": self.run_id, "session_id": self.session_id, "created_at": self.created_at,
            "profile": self.profile.name, "privacy": self.privacy, "model": self.model, "status": self.status,
            "finished_at": self.finished_at, "headline": self.headline, "summary": self.summary,
            "notes": self.notes, "overview": self.overview,
            "moments": [{**m.to_dict(), "finding": self.findings[m.id].to_dict() if m.id in self.findings else None}
                        for m in sorted(self.moments, key=lambda m: m.start_s)],
        }
