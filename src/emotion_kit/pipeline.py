"""Высокоуровневый API: окно (звук + кадры) -> эмоции по аудио, по видео и после слияния."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import preprocess as P
from .backends import CLASSES, MODELS, load_backends
from .fusion import METHODS, Fusion


def _summary(o):
    if o is None:
        return None
    return {"label": CLASSES[int(np.argmax(o["probs"]))],
            "probs": {c: round(float(p), 4) for c, p in zip(CLASSES, o["probs"])}}


class EmotionPipeline:
    """backend: 'onnx' | 'torch'; precision (для onnx): 'fp32' | 'int8';
    n_frames: 16 (как при обучении) или 8 (в ~2 раза быстрее видео);
    fusion: метод по умолчанию из fusion.METHODS."""

    def __init__(self, backend="onnx", precision="fp32", n_frames=16, fusion="weighted_logits",
                 threads=None, models_dir=MODELS):
        models_dir = Path(models_dir)
        assert n_frames in (8, 16), "поддерживаются 16 или 8 кадров"
        assert fusion in METHODS, f"fusion из {METHODS}"
        self.audio, self.video = load_backends(backend, precision, threads, models_dir)
        self.cropper = P.FaceCropper(models_dir / "face_detection_yunet_2023mar.onnx")
        self.fusion = Fusion(models_dir / "fusion_params.json")
        self.method, self.n_frames = fusion, n_frames
        self.times, self.fi = P.frame_times(n_frames)

    # ---- отдельные модальности (сырые выходы: logits, probs, emb)
    def run_audio(self, wave, sr=P.SR):
        w = P.prepare_audio(wave, sr)
        return None if w is None else self.audio.predict(w)

    def run_video(self, frames, face_box=None):
        """frames: кадры окна RGB uint8 (любое число >= 1, (H, W, 3)); face_box — рамка
        (x, y, w, h) отслеживаемого человека или None (самое крупное лицо)."""
        if frames is None or len(frames) == 0:
            return None
        f16 = P.select_frames(frames, P.N_FRAMES)
        faces = self.cropper.crop_window(f16, face_box)
        if faces is None:
            return None
        x = P.faces_to_input(faces)[self.fi]
        return self.video.predict(x, self.times)

    # ---- всё вместе
    def analyze(self, audio=None, sr=P.SR, frames=None, face_box=None, methods=None):
        a = self.run_audio(audio, sr) if audio is not None else None
        v = self.run_video(frames, face_box) if frames is not None else None
        res = {"audio": _summary(a), "video": _summary(v), "fusion": {}}
        if a is None and v is None:
            return res
        for m in methods or [self.method]:
            f = self.fusion.fuse(m, a, v)
            res["fusion"][m] = {"label": f["label"], "used": f["method"],
                                "probs": {c: round(float(p), 4) for c, p in zip(CLASSES, f["probs"])}}
        return res
