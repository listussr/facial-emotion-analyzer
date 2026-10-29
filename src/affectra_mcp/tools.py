"""
Ручки данных одного разбора: обзор, ряды, детекторы, контекст момента.

Каждая ручка возвращает короткий текст: шапку с пояснением (что это, единицы, шаг) и таблицу или строки.
В профиле приватности external наружу не уходят имя сессии, id людей и абсолютное время.
"""

import json
import numpy as np

from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from mcp.server.mcpserver.exceptions import ToolError as MCPToolError

from src.emotion_moments import EmotionSeries
from src.emotion_moments import Profile
from src.emotion_moments import TYPE_TITLES
from src.emotion_moments import detect_moments
from src.emotion_moments import session_overview
from src.emotion_moments.detectors import detect_abrupt_changes
from src.emotion_moments.detectors import detect_sustained
from .formatting import SHORT_RU
from .formatting import mmss
from .formatting import pick_step
from .formatting import shares
from .formatting import table

PRIVACY_LEVELS = ("external", "local")
MODALITY_RU    = {"fused": "итог (лицо и голос)", "face": "только лицо", "voice": "только голос"}
QUALITY_RU     = {"low_face": "лицо в кадре меньше 60% времени",
                  "many_predicted": "больше 75% рамок лица предсказал трекер — детектор часто терял лицо",
                  "others_in_frame": "в кадре часто есть другие люди",
                  "no_voice": "оценок голоса нет (нет речи или звука)"}


class ToolError(MCPToolError, ValueError):
    """Неверные параметры ручки: текст уходит модели как результат с признаком ошибки."""


@dataclass
class SessionContext:
    """Данные разбора: ряд субъекта, профиль, уровень приватности и метаданные сессии (только для local)."""
    series:  EmotionSeries
    profile: Profile
    privacy: str = "local"
    meta:    Optional[Dict[str, Any]] = None


class AffectraTools:
    """Реализация ручек поверх SessionContext; методы — то, что видит модель."""

    max_points = 120

    def __init__(self, ctx: SessionContext):
        if ctx.privacy not in PRIVACY_LEVELS:
            raise ValueError(f"privacy must be one of {PRIVACY_LEVELS}")
        self.ctx      = ctx
        self.s        = ctx.series
        self._moments = None

    # ---------- обзор ----------

    def get_session_overview(self) -> str:
        """Обзор сессии: длительность, доля времени с лицом и речью, ведущие эмоции по модальностям, качество."""
        ov    = session_overview(self.s)
        lines = ["# обзор сессии (один человек в кадре — субъект P1)"]
        if self.ctx.privacy == "local" and self.ctx.meta:
            m = self.ctx.meta
            lines.append(f"сессия: {m.get('name') or m.get('camera_id')} ({m.get('camera_id')}), начало "
                         f"{m.get('started_at') or 'неизвестно'}")
            people = self.s.subject.get("people") or []
            if people:
                lines.append(f"субъект: {', '.join(people)}")
        lines.append(f"длительность: {mmss(ov['duration_s'])} ({ov['duration_s']:.0f} с); лицо в кадре "
                     f"{ov['face_present']:.0%} времени; речь {ov['speech']:.0%} времени")
        for modality, data in ov["modalities"].items():
            lines.append(f"{MODALITY_RU[modality]}: {data['seconds']} с с данными; ведущая эмоция по времени — "
                         f"{shares(data['dominant'])}")
        quality = [QUALITY_RU.get(q, q) for q in ov["quality"]]
        lines.append("качество: " + ("; ".join(quality) if quality else "без замечаний"))
        return "\n".join(lines)

    def get_profile(self) -> str:
        """Профиль сценария: пороги детекторов, по которым найдены моменты."""
        p = self.ctx.profile
        return (f"# профиль сценария «{p.title}» ({p.name})\n{p.description.strip()}\n"
                f"детекторы: {json.dumps(p.detectors, ensure_ascii=False)}\n"
                f"значимость: {json.dumps(p.significance, ensure_ascii=False)}; слияние моментов ближе "
                f"{p.merge_gap_s} с")

    # ---------- ряды ----------

    def _range(self, from_s: float, to_s: Optional[float]) -> tuple:
        end = self.s.duration_s if to_s is None else min(float(to_s), self.s.duration_s)
        beg = max(0.0, float(from_s))
        if end <= beg:
            raise ToolError(f"пустой интервал: {from_s}–{to_s}; сессия длится {self.s.duration_s:.0f} с")
        return beg, end

    def get_emotion_series(self, from_s: float = 0, to_s: Optional[float] = None, modality: str = "fused",
                           step_s: Optional[float] = None) -> str:
        """
        Ряд вероятностей 6 эмоций субъекта в интервале [from_s, to_s) секунд от начала сессии.

        modality: fused — итог (лицо и голос), face — только лицо, voice — только голос. Не больше 120 строк:
        для длинного интервала шаг растёт сам; для подробностей запросите интервал уже.
        """
        if modality not in MODALITY_RU:
            raise ToolError(f"modality: одно из {list(MODALITY_RU)}")
        beg, end = self._range(from_s, to_s)
        step     = pick_step(end - beg, self.s.step_s, step_s, self.max_points)
        rows     = []
        t        = beg
        while t < end:
            sl   = self.s.window(t, min(end, t + step))
            mean = self.s.mean_probs(sl, modality)
            face = float(np.mean(self.s.face_present[sl])) if sl.stop > sl.start else 0.0
            rows.append([int(t), *([None] * len(self.s.labels) if mean is None else [float(x) for x in mean]), face])
            t += step
        header = ["t_s", *[SHORT_RU.get(x, x) for x in self.s.labels], "лицо"]
        return (f"# {MODALITY_RU[modality]}: {mmss(beg)}–{mmss(end)}, шаг {step:g} с; средние вероятности за шаг; "
                f"пусто — нет данных; «лицо» — доля кадров с лицом субъекта\n" + table(header, rows))

    def get_voice_windows(self, from_s: float = 0, to_s: Optional[float] = None) -> str:
        """Секунды с речью в интервале: доля речи и вероятности эмоций голоса. Не больше 120 строк."""
        beg, end = self._range(from_s, to_s)
        sl       = self.s.window(beg, end)
        idx      = [i for i in range(sl.start, sl.stop) if self.s.speech[i] > 0 or not np.isnan(self.s.voice[i, 0])]
        if not idx:
            return f"# голос: {mmss(beg)}–{mmss(end)} — речи нет"
        stride = max(1, int(np.ceil(len(idx) / self.max_points)))
        rows   = [[int(self.s.t[i]), float(self.s.speech[i]), *[float(x) for x in self.s.voice[i]]]
                  for i in idx[::stride]]
        header = ["t_s", "речь", *[SHORT_RU.get(x, x) for x in self.s.labels]]
        note   = f", каждая {stride}-я секунда" if stride > 1 else ""
        return (f"# голос: {mmss(beg)}–{mmss(end)}, секунды с речью{note}; «речь» — доля кадров с речью; "
                f"пусто — голос не оценён\n" + table(header, rows))

    # ---------- детекторы ----------

    def moments(self):
        if self._moments is None:
            self._moments = detect_moments(self.s, self.ctx.profile)
        return self._moments

    def moment(self, moment_id: str):
        for m in self.moments():
            if m.id == moment_id:
                return m
        raise ToolError(f"нет момента {moment_id}; есть: {', '.join(m.id for m in self.moments()) or 'никаких'}")

    def detect_moments(self) -> str:
        """Кандидаты в моменты, требующие внимания, по правилам профиля — от самого значимого."""
        ms = self.moments()
        if not ms:
            return "# моменты: по правилам профиля не найдено ни одного"
        lines = [f"# моменты: {len(ms)}, от самого значимого; значимость 0–1 по детекторам"]
        for m in ms:
            kinds = ", ".join(TYPE_TITLES.get(t, t) for t in m.types)
            lines.append(f"{m.id} {mmss(m.start_s)}–{mmss(m.end_s)} ({m.start_s:.0f}–{m.end_s:.0f} с): {kinds}; "
                         f"значимость {m.significance:.2f} ({m.level})")
            for c in m.candidates:
                lines.append(f"  · {c.type}: интенсивность {c.intensity:.2f}, уверенность {c.confidence:.2f}, "
                             f"{json.dumps(c.metrics, ensure_ascii=False)}")
        return "\n".join(lines)

    def find_sustained(self, emotion: str, min_prob: float = 0.6, min_duration_s: float = 8) -> str:
        """Интервалы, где эмоция (anger, disgust, fear, happy, neutral, sad) держится с вероятностью ≥ min_prob."""
        if emotion not in self.s.labels:
            raise ToolError(f"emotion: одна из {list(self.s.labels)}")
        found = detect_sustained(self.s, emotion, enter_p=float(min_prob), min_duration_s=float(min_duration_s))
        head  = f"# устойчиво {SHORT_RU.get(emotion, emotion)} ≥ {min_prob} не короче {min_duration_s} с"
        if not found:
            return head + ": не найдено"
        return head + "\n" + "\n".join(
            f"{mmss(c.start_s)}–{mmss(c.end_s)} ({c.start_s:.0f}–{c.end_s:.0f} с): средняя {c.metrics['mean_p']:.2f}, "
            f"максимум {c.metrics['max_p']:.2f}, лицо {c.metrics['face_present']:.0%}" for c in found)

    def find_abrupt_changes(self, min_jsd: float = 0.3, min_hold_s: float = 3) -> str:
        """Резкие смены ведущей эмоции: распределения за min_hold_s до и после различаются сильнее min_jsd (0–1)."""
        found = detect_abrupt_changes(self.s, window_s=float(min_hold_s), min_jsd=float(min_jsd))
        head  = f"# резкие смены: расстояние ≥ {min_jsd}, окно {min_hold_s} с"
        if not found:
            return head + ": не найдено"
        return head + "\n" + "\n".join(
            f"{mmss(c.metrics['at_s'])} ({c.metrics['at_s']:.0f} с): {SHORT_RU[c.metrics['from']]} "
            f"{c.metrics['from_p']:.2f} → {SHORT_RU[c.metrics['to']]} {c.metrics['to_p']:.2f}, "
            f"расстояние {c.metrics['jsd']:.2f}" for c in found)

    # ---------- контекст ----------

    def _segment(self, title: str, beg: float, end: float) -> str:
        if end <= beg:
            return f"{title}: нет (край сессии)"
        sl     = self.s.window(beg, end)
        face   = float(np.mean(self.s.face_present[sl]))
        pred   = float(np.mean(self.s.predicted[sl]))
        speech = float(np.mean(self.s.speech[sl]))
        others = float(np.mean(self.s.others[sl]))
        text   = (f"{title} {mmss(beg)}–{mmss(end)}: ведущая эмоция {shares(self.s.dominant_shares(sl))}; "
                  f"лицо {face:.0%}, предсказано трекером {pred:.0%}, речь {speech:.0%}")
        voice  = self.s.dominant_shares(sl, "voice")
        if voice:
            text += f"; голос: {shares(voice)}"
        if others > 0.05:
            text += f"; другие люди в кадре {others:.0%}"
        return text

    def get_moment_context(self, start_s: float, end_s: float, pad_s: float = 15) -> str:
        """Что было до, во время и после интервала: ведущие эмоции, голос и качество данных (лицо, трекер, речь)."""
        beg, end = self._range(start_s, end_s)
        pad      = max(0.0, float(pad_s))
        return "\n".join([
            f"# контекст {mmss(beg)}–{mmss(end)} (±{pad:g} с); доли — от секунд с данными; «предсказано трекером» "
            f"до ~50% — норма (детектор раз в 2 кадра), больше — лицо терялось",
            self._segment("до", max(0.0, beg - pad), beg),
            self._segment("во время", beg, end),
            self._segment("после", end, min(self.s.duration_s, end + pad)),
        ])


TOOL_NAMES: List[str] = ["get_session_overview", "get_profile", "get_emotion_series", "get_voice_windows",
                         "detect_moments", "find_sustained", "find_abrupt_changes", "get_moment_context"]
