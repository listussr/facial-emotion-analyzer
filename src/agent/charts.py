"""SVG-графики отчёта (matplotlib): таймлайн сессии с полосами моментов и мини-графики интервалов."""

import io
import re
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from typing import Dict  # noqa: E402
from typing import List  # noqa: E402
from typing import Optional  # noqa: E402

from src.emotion_moments import LABELS_RU  # noqa: E402
from src.emotion_moments import EmotionSeries  # noqa: E402

EMOTION_COLORS = {"anger": "#ef4444", "disgust": "#10b981", "fear": "#8b5cf6", "happy": "#fbbf24",
                  "neutral": "#94a3b8", "sad": "#3b82f6"}
VERDICT_COLORS = {"attention": "#dc2626", "likely_artifact": "#d97706", "not_significant": "#64748b", None: "#4f46e5"}
INK            = "#334155"
GRID           = "#e2e8f0"

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "svg.fonttype": "none",
                     "axes.edgecolor": GRID, "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK})


def _svg(fig) -> str:
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight", transparent=True)
    plt.close(fig)
    svg = buf.getvalue()
    svg = svg[svg.index("<svg"):]
    return re.sub(r'(<svg[^>]*?) width="[^"]+" height="[^"]+"', r'\1 width="100%"', svg, count=1)


def _mmss_ticks(ax, start: float, end: float) -> None:
    span  = max(1.0, end - start)
    step  = next((s for s in (5, 10, 15, 30, 60, 120, 300, 600, 900, 1800) if span / s <= 10), 3600)
    ticks = np.arange(np.ceil(start / step) * step, end + 0.01, step)
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{int(t) // 60:02d}:{int(t) % 60:02d}" for t in ticks])


def _emotion_lines(ax, s: EmotionSeries, sl: slice) -> None:
    t = s.t[sl] + s.step_s / 2
    for k, label in enumerate(s.labels):
        ax.plot(t, s.fused[sl, k], color=EMOTION_COLORS.get(label, INK), linewidth=1.3, label=LABELS_RU.get(label, label))
    ax.set_ylim(0, 1)
    ax.set_yticks([0, 0.5, 1])
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def _strip(ax, s: EmotionSeries, sl: slice, values: np.ndarray, color: str, title: str) -> None:
    t = s.t[sl]
    ax.bar(t + s.step_s / 2, values[sl], width=s.step_s, color=color, linewidth=0)
    ax.set_ylim(0, 1)
    ax.set_yticks([])
    ax.set_ylabel(title, rotation=0, ha="right", va="center", fontsize=7)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)


def timeline_svg(s: EmotionSeries, moments: List[Dict], width_in: float = 10.0) -> str:
    """
    Таймлайн всей сессии: вероятности эмоций, пронумерованные полосы моментов (цвет — вердикт), дорожки
    «лицо в кадре» и «речь».

    Args:
        s        (EmotionSeries)  : Ряд.
        moments  (List[Dict])     : Моменты: number, start_s, end_s, verdict (None — без вердикта модели).
        width_in (float, optional): Ширина рисунка, дюймы. Defaults to 10.0.

    Returns:
        str: Разметка SVG.
    """
    sl  = slice(0, len(s))
    fig = plt.figure(figsize=(width_in, 3.4))
    gs  = fig.add_gridspec(3, 1, height_ratios=[6, 0.6, 0.6], hspace=0.15)
    ax  = fig.add_subplot(gs[0])
    _emotion_lines(ax, s, sl)
    for m in moments:
        color = VERDICT_COLORS.get(m.get("verdict"), VERDICT_COLORS[None])
        ax.axvspan(m["start_s"], max(m["end_s"], m["start_s"] + s.step_s), color=color, alpha=0.12, linewidth=0)
        ax.text((m["start_s"] + m["end_s"]) / 2, 1.02, str(m["number"]), ha="center", va="bottom", fontsize=8,
                color=color, fontweight="bold")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=6, frameon=False, fontsize=7)
    ax.set_xticks([])
    face = fig.add_subplot(gs[1], sharex=ax)
    _strip(face, s, sl, s.face_present, "#94a3b8", "лицо")
    face.set_xticks([])
    speech = fig.add_subplot(gs[2], sharex=ax)
    _strip(speech, s, sl, s.speech, "#6366f1", "речь")
    ax.set_xlim(0, max(s.duration_s, s.step_s))
    _mmss_ticks(speech, 0, s.duration_s)
    return _svg(fig)


def moment_svg(s: EmotionSeries, start_s: float, end_s: float, pad_s: float = 15, verdict: Optional[str] = None) -> str:
    """Мини-график интервала момента ±pad_s с подсветкой самого момента."""
    beg = max(0.0, start_s - pad_s)
    end = min(s.duration_s, end_s + pad_s)
    sl  = s.window(beg, end)
    fig, ax = plt.subplots(figsize=(5.0, 1.6))
    _emotion_lines(ax, s, sl)
    ax.axvspan(start_s, max(end_s, start_s + s.step_s), color=VERDICT_COLORS.get(verdict, VERDICT_COLORS[None]),
               alpha=0.12, linewidth=0)
    ax.set_xlim(beg, max(end, beg + s.step_s))
    _mmss_ticks(ax, beg, end)
    return _svg(fig)
