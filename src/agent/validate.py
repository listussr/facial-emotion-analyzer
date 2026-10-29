"""Проверка ответов модели кодом: допустимые значения, доказательства с реальными метками, времена внутри момента."""

import re

from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Tuple

from src.emotion_moments import Moment
from .findings import Evidence
from .findings import Finding
from .prompts import LEVELS
from .prompts import VERDICTS

TIME_RE        = re.compile(r"\b(\d{1,3}):([0-5]\d)\b")
TIME_TOLERANCE = 30.0


def mentioned_times(text: str) -> List[float]:
    """Времена мм:сс из текста, в секундах."""
    return [int(m.group(1)) * 60 + int(m.group(2)) for m in TIME_RE.finditer(text or "")]


def validate_finding(args: Dict[str, Any], moment: Moment, labels: Iterable[str],
                     tolerance_s: float = TIME_TOLERANCE) -> Tuple[Optional[Finding], List[str]]:
    """
    Вердикт модели или список ошибок.

    Проверяется: verdict, confidence, significance — из допустимых; summary и explanation не пустые;
    у attention и likely_artifact есть доказательства, и каждое ссылается на метку реального результата
    (c0 — данные детекторов); времена мм:сс в текстах лежат не дальше tolerance_s от момента.

    Args:
        args        (Dict[str, Any])  : Аргументы submit_finding.
        moment      (Moment)          : Разбираемый момент.
        labels      (Iterable[str])   : Метки результатов, доступные модели в этом разборе.
        tolerance_s (float, optional) : Допуск времени, с. Defaults to TIME_TOLERANCE.

    Returns:
        Tuple[Optional[Finding], List[str]]: Вердикт (None при ошибках) и ошибки.
    """
    errors: List[str] = []
    labels = set(labels) | {"c0"}
    verdict, confidence, significance = args.get("verdict"), args.get("confidence"), args.get("significance")
    if verdict not in VERDICTS:
        errors.append(f"verdict должен быть одним из {list(VERDICTS)}")
    if confidence not in LEVELS:
        errors.append(f"confidence должен быть одним из {list(LEVELS)}")
    if significance is not None and significance not in LEVELS:
        errors.append(f"significance должен быть одним из {list(LEVELS)}")
    summary     = str(args.get("summary") or "").strip()
    explanation = str(args.get("explanation") or "").strip()
    check       = str(args.get("operator_check") or "").strip()
    if not summary:
        errors.append("summary пустой")
    if not explanation:
        errors.append("explanation пустой")

    evidence: List[Evidence] = []
    for item in args.get("evidence") or []:
        if not isinstance(item, dict) or not str(item.get("fact") or "").strip():
            errors.append("каждое доказательство — объект с непустым fact")
            continue
        source = str(item.get("source") or "").strip().strip("[]")
        if source not in labels:
            errors.append(f"доказательство «{item['fact'][:60]}» ссылается на {source or 'ничто'}; "
                          f"доступные метки: {', '.join(sorted(labels))}")
            continue
        evidence.append(Evidence(fact=str(item["fact"]).strip(), source=source))
    if verdict in ("attention", "likely_artifact") and not evidence and not any("доказательств" in e for e in errors):
        errors.append("для этого вердикта нужно хотя бы одно доказательство с меткой источника")

    lo, hi = moment.start_s - tolerance_s, moment.end_s + tolerance_s
    for t in mentioned_times(" ".join((summary, explanation, check))):
        if not lo <= t <= hi:
            errors.append(f"время {int(t) // 60:02d}:{int(t) % 60:02d} вне момента "
                          f"{int(moment.start_s) // 60:02d}:{int(moment.start_s) % 60:02d}–"
                          f"{int(moment.end_s) // 60:02d}:{int(moment.end_s) % 60:02d}")
    if errors:
        return None, errors
    return Finding(moment_id=moment.id, verdict=verdict, confidence=confidence, significance=significance,
                   summary=summary, explanation=explanation, evidence=evidence, operator_check=check), []


def validate_summary(args: Dict[str, Any]) -> Tuple[Optional[Dict[str, str]], List[str]]:
    """Заголовок и итог: оба непустые, заголовок — одна строка."""
    headline = str(args.get("headline") or "").strip()
    summary  = str(args.get("summary") or "").strip()
    errors   = [e for e, bad in (("headline пустой", not headline), ("summary пустой", not summary),
                                 ("headline длиннее 240 символов", len(headline) > 240)) if bad]
    return (None, errors) if errors else ({"headline": " ".join(headline.split()), "summary": summary}, [])
