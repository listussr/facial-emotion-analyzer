"""Инференс отдельных модальностей на CPU: ONNX Runtime (fp32 / int8) или PyTorch.

Единый контракт: predict(...) -> {"logits": (6,), "probs": (6,), "emb": (256,)}.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

# В проекте модели лежат в <репозиторий>/models/crema; другой каталог — CREMA_MODELS_DIR
MODELS = Path(os.getenv("CREMA_MODELS_DIR") or Path(__file__).resolve().parents[2] / "models" / "crema")
CLASSES = ["ANG", "DIS", "FEA", "HAP", "NEU", "SAD"]


def softmax(z):
    z = np.asarray(z, np.float64)
    e = np.exp(z - z.max(-1, keepdims=True))
    return (e / e.sum(-1, keepdims=True)).astype(np.float32)


def _out(logits, emb):
    logits = np.asarray(logits, np.float32).reshape(-1)
    return {"logits": logits, "probs": softmax(logits), "emb": np.asarray(emb, np.float32).reshape(-1)}


def _session(path, threads):
    import onnxruntime as ort
    so = ort.SessionOptions()
    if threads:
        so.intra_op_num_threads = threads
    # Проект: модели работают рядом с другими сервисами — потоки ORT не крутятся
    # в активном ожидании после каждого запуска (иначе отбирают CPU у соседей)
    so.inter_op_num_threads = 1
    so.add_session_config_entry("session.intra_op.allow_spinning", "0")
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


# ===================================================================== ONNX
class AudioONNX:
    def __init__(self, precision: str = "fp32", threads: int | None = None, models_dir=MODELS):
        name = "audio_wavlm_base_plus" + ("_int8" if precision == "int8" else "") + ".onnx"
        self.sess = _session(Path(models_dir) / name, threads)

    def predict(self, wave: np.ndarray) -> dict:
        """wave: (L,) float32 из preprocess.prepare_audio."""
        lg, emb = self.sess.run(["logits", "emb"], {"wave": wave[None].astype(np.float32)})
        return _out(lg, emb)


class VideoONNX:
    """Два графа: кадровый энкодер (кадр -> 768) и временная голова (T x 768 -> логиты).
    Кадры можно кодировать по мере поступления и хранить признаки — голова дешёвая."""

    def __init__(self, precision: str = "fp32", threads: int | None = None, models_dir=MODELS,
                 batch: int = 16):
        sfx = "_int8" if precision == "int8" else ""
        self.enc = _session(Path(models_dir) / f"video_frame_encoder_swin_t{sfx}.onnx", threads)
        self.head = _session(Path(models_dir) / "video_temporal_head.onnx", threads)
        self.batch = batch

    def encode_frames(self, x: np.ndarray) -> np.ndarray:
        """x: (N, 3, 224, 224) float32 (preprocess.faces_to_input) -> (N, 768)."""
        outs = [self.enc.run(["feat"], {"frames": x[i:i + self.batch]})[0]
                for i in range(0, len(x), self.batch)]
        return np.concatenate(outs).astype(np.float32)

    def predict_features(self, feats: np.ndarray, times: np.ndarray) -> dict:
        """feats: (T, 768), times: (T,) — см. preprocess.frame_times."""
        lg, emb = self.head.run(["logits", "emb"], {"feats": feats[None].astype(np.float32),
                                                    "times": times[None].astype(np.float32)})
        return _out(lg, emb)

    def predict(self, x: np.ndarray, times: np.ndarray) -> dict:
        return self.predict_features(self.encode_frames(x), times)


# ==================================================================== torch
class AudioTorch:
    def __init__(self, threads: int | None = None, models_dir=MODELS):
        import torch
        from .torch_models import load_audio_model
        if threads:
            torch.set_num_threads(threads)
        self.torch = torch
        self.model = load_audio_model(Path(models_dir) / "audio_wavlm_base_plus.pt")

    def predict(self, wave):
        with self.torch.inference_mode():
            o = self.model(self.torch.from_numpy(wave[None].astype(np.float32)))
        return _out(o["logits"].numpy(), o["emb"].numpy())


class VideoTorch:
    def __init__(self, threads: int | None = None, models_dir=MODELS, batch: int = 16):
        import torch
        from .torch_models import load_video_model
        if threads:
            torch.set_num_threads(threads)
        self.torch, self.batch = torch, batch
        self.model = load_video_model(Path(models_dir) / "video_fer_swin_temporal.pt")

    def encode_frames(self, x):
        t = self.torch
        with t.inference_mode():
            return np.concatenate([self.model.encode_frames(t.from_numpy(x[i:i + self.batch])).numpy()
                                   for i in range(0, len(x), self.batch)])

    def predict_features(self, feats, times):
        t = self.torch
        with t.inference_mode():
            o = self.model.temporal(t.from_numpy(feats[None].astype(np.float32)),
                                    t.from_numpy(times[None].astype(np.float32)))
        return _out(o["logits"].numpy(), o["emb"].numpy())

    def predict(self, x, times):
        return self.predict_features(self.encode_frames(x), times)


def load_backends(backend: str = "onnx", precision: str = "fp32", threads: int | None = None,
                  models_dir=MODELS):
    """backend: 'onnx' (precision 'fp32' | 'int8') или 'torch'. -> (audio, video)."""
    if backend == "onnx":
        return (AudioONNX(precision, threads, models_dir), VideoONNX(precision, threads, models_dir))
    if backend == "torch":
        return AudioTorch(threads, models_dir), VideoTorch(threads, models_dir)
    raise ValueError(backend)


def manifest(models_dir=MODELS) -> dict:
    return json.loads((Path(models_dir) / "manifest.json").read_text(encoding="utf-8"))
