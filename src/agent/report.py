"""Сборка HTML-отчёта разбора по шаблону: тексты модели (если есть), данные детекторов и SVG-графики."""

import json
import os

from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from jinja2 import Environment
from jinja2 import FileSystemLoader
from jinja2 import select_autoescape

from src.emotion_moments import LABELS_RU
from src.emotion_moments import TYPE_TITLES
from src.emotion_moments import Moment
from .charts import moment_svg
from .charts import timeline_svg
from .findings import LEVEL_RU
from .findings import VERDICT_MARK
from .findings import VERDICT_RANK
from .findings import VERDICT_RU
from .findings import RunData

PUBLIC_URL   = os.getenv("AFFECTRA_PUBLIC_URL", "http://localhost:8080").rstrip("/")
TEMPLATES    = Path(__file__).resolve().parent / "templates"
QUALITY_TEXT = {"low_face": "лицо часто отсутствует в кадре", "many_predicted": "много рамок предсказано трекером",
                "others_in_frame": "в кадре часто есть другие люди", "no_voice": "голос не оценивался"}
DETECTOR_RULES = [
    ("sustained", "устойчивые гнев и грусть",
     "вероятность эмоции не ниже порога входа дольше минимальной длительности; выход — ниже порога на гистерезис"),
    ("abrupt_change", TYPE_TITLES["abrupt_change"],
     "распределения за окно до и после секунды далеки, ведущая эмоция сменилась и новая уверенная"),
    ("face_voice_divergence", TYPE_TITLES["face_voice_divergence"],
     "во время речи ведущие эмоции лица и голоса разные и обе уверенные"),
    ("flat_affect", TYPE_TITLES["flat_affect"],
     "в длинном окне распределение эмоций почти не меняется"),
]

_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))


def mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def recording_link(session_id: str, start_s: float) -> str:
    return f"{PUBLIC_URL}/recordings/{session_id}?t={int(start_s)}"


def _number(m: Moment) -> int:
    return int(m.id[1:]) if m.id[1:].isdigit() else 0


def detector_lines(m: Moment) -> List[str]:
    """Человекочитаемые данные кандидатов момента — для карточек без объяснения модели."""
    out = []
    for c in m.candidates:
        x = c.metrics
        if c.type.startswith("sustained_"):
            text = (f"{TYPE_TITLES.get(c.type, c.type)}: средняя вероятность {x['mean_p']:.2f}, максимум "
                    f"{x['max_p']:.2f}")
        elif c.type == "abrupt_change":
            text = (f"резкая смена на {mmss(x['at_s'])}: {LABELS_RU.get(x['from'])} {x['from_p']:.2f} → "
                    f"{LABELS_RU.get(x['to'])} {x['to_p']:.2f}, расстояние {x['jsd']:.2f}")
        elif c.type == "face_voice_divergence":
            text = (f"лицо — {LABELS_RU.get(x['face'])}, голос — {LABELS_RU.get(x['voice'])}, расстояние "
                    f"{x['mean_jsd']:.2f}")
        elif c.type == "flat_affect":
            text = (f"изменчивость {x['variability']:.3f}, ведущая эмоция {LABELS_RU.get(x['dominant'], '—')} "
                    f"{(x.get('dominant_share') or 0):.0%}")
        else:
            text = f"{c.type}: {json.dumps(x, ensure_ascii=False)}"
        lost = f", лицо терялось (трекер {x['predicted']:.0%})" if x.get("predicted", 0) > 0.6 else ""
        out.append(f"{text}; лицо в кадре {x.get('face_present', 0):.0%}, речь {x.get('speech', 0):.0%}{lost}")
    return out


def default_headline(run: RunData) -> str:
    """Заголовок без модели: число моментов и самый значимый."""
    if not run.moments:
        return "Моментов, требующих внимания, по правилам профиля не найдено."
    top = max(run.moments, key=lambda m: m.significance)
    n   = len(run.moments)
    return (f"Детекторы нашли моментов: {n}. Самый значимый — {mmss(top.start_s)}–{mmss(top.end_s)}, "
            f"{', '.join(top.titles())}.")


def _moment_view(run: RunData, m: Moment, chart: bool) -> Dict[str, Any]:
    f = run.findings.get(m.id)
    return {
        "id": m.id, "number": _number(m), "start": mmss(m.start_s),
        "interval": f"{mmss(m.start_s)}–{mmss(m.end_s)}", "duration": f"{m.duration_s:.0f} с",
        "titles": ", ".join(m.titles()), "significance": m.significance, "level": LEVEL_RU.get(m.level, m.level),
        "verdict": f.verdict if f else None, "confidence": LEVEL_RU.get(f.confidence, f.confidence) if f else None,
        "llm_level": LEVEL_RU.get(f.significance, f.significance) if f and f.significance else None,
        "summary": f.summary if f else "", "explanation": f.explanation if f else "",
        "evidence": f.evidence if f else [], "operator_check": f.operator_check if f else "",
        "detector_lines": detector_lines(m),
        "link": recording_link(run.session_id, m.start_s) if run.session_id else None,
        "chart": moment_svg(run.series, m.start_s, m.end_s, verdict=f.verdict if f else None) if chart else "",
    }


def build_report(run: RunData) -> str:
    """
    HTML-отчёт разбора: шапка, итог, таймлайн, карточки моментов, остальные кандидаты, методика.

    Без вердиктов модели карточки показывают данные детекторов с пометкой «без объяснения модели».

    Args:
        run (RunData): Данные разбора.

    Returns:
        str: Самодостаточный HTML (стили и графики встроены).
    """
    cards, rest = run.split()
    timeline    = timeline_svg(run.series, [{"number": _number(m), "start_s": m.start_s, "end_s": m.end_s,
                                             "verdict": run.verdict(m.id)} for m in run.moments])
    meta        = run.meta if run.privacy == "local" else None
    title       = (meta or {}).get("name") or run.session_id
    started_at  = (meta or {}).get("started_at")
    detectors   = []
    for name, title_, rule in DETECTOR_RULES:
        if name in run.profile.detectors:
            params = {k: v for k, v in run.profile.detector(name).items() if k != "weight"}
            detectors.append({"title": title_, "rule": rule, "params": json.dumps(params, ensure_ascii=False)})

    return _env.get_template("report.html.j2").render(
        run=run, title=title, started_at=_human_time(started_at), created_at=_human_time(run.created_at),
        duration=mmss(run.series.duration_s), headline=run.headline or default_headline(run), summary=run.summary,
        counts=run.counts(), verdict_order=sorted(VERDICT_RANK, key=VERDICT_RANK.get), verdicts=VERDICT_RU,
        marks=VERDICT_MARK, quality="; ".join(QUALITY_TEXT.get(q, q) for q in run.overview.get("quality", [])),
        timeline=timeline, cards=[_moment_view(run, m, True) for m in cards],
        rest=[_moment_view(run, m, False) for m in rest], detectors=detectors, pct=lambda x: f"{(x or 0):.0%}",
    )


def _human_time(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso)).astimezone().strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return str(iso)
