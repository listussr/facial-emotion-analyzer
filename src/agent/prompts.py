"""Промпты агента на русском и схемы ответов модели (submit_finding, submit_summary)."""

from typing import Any
from typing import Dict
from typing import List

from src.emotion_moments import Moment
from src.emotion_moments import Profile

VERDICTS = ("attention", "likely_artifact", "not_significant")
LEVELS   = ("low", "medium", "high")

SYSTEM = """Вы — помощник исследователя, разбирающий запись сессии анализа эмоций. Вы — первичный контур проверки:
отмечаете, к чему оператору стоит присмотреться. Решение принимает оператор.

Что значат данные. Один человек в кадре (субъект P1). Числа — вероятности ВЫРАЖЕННОЙ эмоции из 6 классов
(гнев, отвращение, страх, радость, нейтральное, грусть) по моделям лица и голоса, обученным на актёрской речи.
Это не чувства человека. Время — секунды от начала сессии, формат мм:сс.

Правила.
1. Опирайтесь только на результаты ручек. Каждый результат помечен меткой в квадратных скобках: [c0] — данные
   детекторов о моменте, [c1], [c2]… — ваши вызовы. В доказательствах указывайте метку источника.
2. Сначала проверьте, не артефакт ли это: лицо пропадало, трекер терял лицо (доля предсказанных рамок заметно
   выше 50%), в кадре другие люди, нет речи при расхождении лица и голоса.
3. Говорите осторожно: «похоже», «стоит проверить». Не ставьте диагнозов, не судите о лжи, здоровье, усталости
   или характере человека.
4. Вердикт: attention — стоит посмотреть; likely_artifact — вероятно артефакт данных; not_significant —
   незначимо (слабо, коротко или обычно для этой сессии).
5. Сделайте не больше {max_calls} вызовов ручек, затем обязательно вызовите submit_finding. Пишите по-русски,
   кратко."""

MOMENT = """Обзор сессии [{overview_label}]:
{overview}

Момент {id}: {interval} ({start:.0f}–{end:.0f} с), длительность {duration:.0f} с.
Типы: {types}. Значимость по детекторам: {significance:.2f} ({level}).
[c0] данные детекторов:
{detectors}

Разберите момент: при необходимости посмотрите контекст (get_moment_context) и ряды в его интервале, затем
вызовите submit_finding."""

SUMMARY_SYSTEM = """Вы пишете итог разбора сессии для исследователя по уже вынесенным вердиктам моментов. Не добавляйте
фактов, которых нет во вердиктах. Не ставьте диагнозов и не судите о человеке. Пишите по-русски, кратко.
Ответьте вызовом submit_summary."""

SUMMARY = """Обзор сессии:
{overview}

Вердикты по моментам (мм:сс от начала):
{findings}

Напишите заголовок объявления (одно предложение: сколько моментов стоит посмотреть и самый важный) и итог
(2–4 предложения: что стоит посмотреть в первую очередь и насколько можно доверять данным)."""

NUDGE_SUBMIT  = "Бюджет вызовов исчерпан. Вызовите submit_finding с вердиктом по уже полученным данным."
NUDGE_TOOL    = "Ответьте вызовом инструмента submit_finding (текст без вызова не принимается)."
RETRY_INVALID = "Ответ не прошёл проверку: {errors}. Исправьте и вызовите submit_finding ещё раз."

SUBMIT_FINDING = {
    "type": "function",
    "function": {
        "name": "submit_finding",
        "description": "Итоговый вердикт по моменту. Вызовите один раз, когда разобрались.",
        "parameters": {
            "type": "object",
            "properties": {
                "verdict":        {"type": "string", "enum": list(VERDICTS),
                                   "description": "attention — стоит посмотреть; likely_artifact — вероятно "
                                                  "артефакт; not_significant — незначимо"},
                "confidence":     {"type": "string", "enum": list(LEVELS), "description": "уверенность в вердикте"},
                "significance":   {"type": "string", "enum": list(LEVELS), "description": "значимость момента"},
                "summary":        {"type": "string", "maxLength": 400, "description": "суть в 1–2 предложениях"},
                "explanation":    {"type": "string", "maxLength": 1200, "description": "почему такой вердикт"},
                "evidence":       {"type": "array", "maxItems": 6, "items": {
                    "type": "object",
                    "properties": {"fact": {"type": "string", "description": "факт с числами из результата"},
                                   "source": {"type": "string", "description": "метка результата: c0, c1, …"}},
                    "required": ["fact", "source"]}},
                "operator_check": {"type": "string", "maxLength": 400,
                                   "description": "что посмотреть оператору, с интервалом мм:сс"},
            },
            "required": ["verdict", "confidence", "significance", "summary", "explanation", "evidence",
                         "operator_check"],
        },
    },
}

SUBMIT_SUMMARY = {
    "type": "function",
    "function": {
        "name": "submit_summary",
        "description": "Заголовок объявления и итог разбора.",
        "parameters": {
            "type": "object",
            "properties": {"headline": {"type": "string", "maxLength": 240},
                           "summary": {"type": "string", "maxLength": 900}},
            "required": ["headline", "summary"],
        },
    },
}


def system_prompt(profile: Profile, max_calls: int, no_think: bool) -> str:
    text = SYSTEM.format(max_calls=max_calls)
    if profile.prompt_notes:
        text += "\n\nСценарий. " + profile.prompt_notes.strip()
    return text + ("\n/no_think" if no_think else "")


def moment_prompt(m: Moment, overview: str, overview_label: str, detectors: List[str], interval: str) -> str:
    return MOMENT.format(overview_label=overview_label, overview=overview, id=m.id, interval=interval,
                         start=m.start_s, end=m.end_s, duration=m.duration_s, types=", ".join(m.titles()),
                         significance=m.significance, level=m.level, detectors="\n".join(f"- {x}" for x in detectors))


def summary_prompt(overview: str, findings: List[Dict[str, Any]]) -> str:
    lines = [f"- {f['interval']} {f['types']}: {f['verdict']} (уверенность {f['confidence']}) — {f['summary']}"
             for f in findings] or ["- моментов нет"]
    return SUMMARY.format(overview=overview, findings="\n".join(lines))
