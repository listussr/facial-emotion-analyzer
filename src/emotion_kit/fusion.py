"""Алгоритмы слияния выходов аудио- и видеомодели (numpy, без обучения на устройстве).

Каждый метод принимает выходы backends (dict с logits/probs/emb) или None для
отсутствующей модальности и возвращает {"scores", "probs", "label", "method"}.
Параметры обученных методов — models/fusion_params.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .backends import CLASSES, MODELS, softmax

METHODS = ("weighted_logits", "weighted_logits_robust", "mean_probs", "confidence_weighted",
           "stack_logits", "stack_hidden", "audio_only", "video_only")


def _log_softmax(z):
    z = np.asarray(z, np.float64)
    m = z.max(-1, keepdims=True)
    return z - m - np.log(np.exp(z - m).sum(-1, keepdims=True))


class Fusion:
    def __init__(self, params_path=None):
        p = Path(params_path or MODELS / "fusion_params.json")
        self.params = json.loads(p.read_text(encoding="utf-8"))

    def _stack(self, name, x):
        p = self.params[name]
        z = (x - np.array(p["mean"])) / np.array(p["scale"])
        return z @ np.array(p["coef"]).T + np.array(p["intercept"])

    def fuse(self, method: str, a: dict | None, v: dict | None) -> dict:
        """a, v — выходы аудио/видео (или None, если модальности нет в окне)."""
        if a is None and v is None:
            raise ValueError("нет ни одной модальности")
        if method == "audio_only" or (method != "video_only" and v is None):
            if a is None:
                raise ValueError("audio_only: аудио нет")
            return self._result(a["logits"], "audio_only" if method == "audio_only" else
                                f"{method} -> audio_only (нет видео)")
        if method == "video_only" or a is None:
            if v is None:
                raise ValueError("video_only: видео нет")
            return self._result(v["logits"], "video_only" if method == "video_only" else
                                f"{method} -> video_only (нет аудио)")
        la, lv = a["logits"], v["logits"]
        if method in ("weighted_logits", "weighted_logits_robust"):
            al = self.params[method]["alpha"]
            s = al * la + (1 - al) * lv
        elif method == "mean_probs":
            s = np.log(0.5 * softmax(la) + 0.5 * softmax(lv) + 1e-9)
        elif method == "confidence_weighted":
            lps = [_log_softmax(la), _log_softmax(lv)]
            ws = [np.log(len(CLASSES)) + (np.exp(lp) * lp).sum() + 1e-6 for lp in lps]
            s = sum(w / sum(ws) * lp for w, lp in zip(ws, lps))
        elif method == "stack_logits":
            s = self._stack(method, np.concatenate([la, lv]))
        elif method == "stack_hidden":
            s = self._stack(method, np.concatenate([a["emb"], v["emb"]]))
        else:
            raise ValueError(f"неизвестный метод {method}; доступны {METHODS}")
        return self._result(s, method)

    @staticmethod
    def _result(scores, method):
        scores = np.asarray(scores, np.float32).reshape(-1)
        pr = softmax(scores)
        return {"method": method, "scores": scores, "probs": pr, "label": CLASSES[int(pr.argmax())]}
