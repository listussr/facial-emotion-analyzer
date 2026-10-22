"""Автономные определения моделей (PyTorch) — без кода исследовательского проекта.

Имена параметров совпадают с исходными чекпойнтами, поэтому
``model.load_state_dict(torch.load(...)["state_dict"])`` загружается строго.

AudioEmotionNet: WavLM-Base+ -> взвешенная сумма 13 скрытых слоёв -> проекция 256
                 -> пулинг с вниманием -> 6 логитов.
VideoEmotionNet: Swin-T (FER-веса, дообучен) по кадрам -> проекция 256 + кодировка
                 времени -> 1 слой трансформера -> пулинг с вниманием -> 6 логитов.
"""
from __future__ import annotations

import math

import torch
import torch.nn as nn

CLASSES = ["ANG", "DIS", "FEA", "HAP", "NEU", "SAD"]
CLIP_SECONDS = 3.0          # шкала времени кадров, на которой обучена видеомодель


class AttentionPool(nn.Module):
    def __init__(self, dim: int, hidden: int = 128):
        super().__init__()
        self.score = nn.Sequential(nn.Linear(dim, hidden), nn.Tanh(), nn.Linear(hidden, 1))

    def forward(self, x, mask=None):
        w = self.score(x).squeeze(-1)                       # (B, T)
        if mask is not None:
            w = w.masked_fill(~mask, float("-inf"))
        a = w.softmax(dim=1)
        return (x * a.unsqueeze(-1)).sum(1)


def time_positional_encoding(times: torch.Tensor, dim: int) -> torch.Tensor:
    """Синусоидальная кодировка физического времени (секунды), чётные — sin, нечётные — cos."""
    half = dim // 2
    freq = torch.exp(torch.arange(half, dtype=torch.float32, device=times.device)
                     * (-math.log(10000.0) / max(half - 1, 1)))
    ang = (times * 100.0).unsqueeze(-1) * freq
    return torch.stack([torch.sin(ang), torch.cos(ang)], -1).flatten(-2)


# ===================================================================== аудио
class _SSL(nn.Module):
    def __init__(self, hf_config: dict):
        super().__init__()
        from transformers import WavLMConfig, WavLMModel
        self.model = WavLMModel(WavLMConfig(**hf_config))


class LayerWeights(nn.Module):
    def __init__(self, n: int):
        super().__init__()
        self.w = nn.Parameter(torch.zeros(n))

    def forward(self, hs):                                  # (L, B, T, D) -> (B, T, D)
        return (hs * self.w.softmax(0).view(-1, 1, 1, 1)).sum(0)


class AudioEmotionNet(nn.Module):
    """Вход: wave (B, L) float32, 16 кГц, предобработан ``preprocess.prepare_audio``;
    mask (B, L) bool — значимые отсчёты (None = все). Выход: logits (B, 6), emb (B, 256)."""

    def __init__(self, hf_config: dict, hidden: int = 256, num_classes: int = 6):
        super().__init__()
        self.backbone = _SSL(hf_config)
        n_layers = hf_config["num_hidden_layers"] + 1
        dim = hf_config["hidden_size"]
        self.layers = LayerWeights(n_layers)
        self.proj = nn.Sequential(nn.LayerNorm(dim), nn.Linear(dim, hidden), nn.GELU(), nn.Dropout(0.2))
        self.pool = AttentionPool(hidden)
        self.head = nn.Sequential(nn.LayerNorm(hidden), nn.Dropout(0.2), nn.Linear(hidden, num_classes))

    def forward(self, wave, mask=None):
        m = self.backbone.model
        if mask is None:
            out = m(wave, attention_mask=torch.ones_like(wave, dtype=torch.long),
                    output_hidden_states=True)
            fm = None
        else:
            out = m(wave, attention_mask=mask.long(), output_hidden_states=True)
            lens = m._get_feat_extract_output_lengths(mask.sum(1))
        hs = torch.stack(out.hidden_states)                 # (13, B, T, 768)
        if mask is not None:
            fm = torch.arange(hs.shape[2], device=wave.device)[None] < lens[:, None]
        seq = self.proj(self.layers(hs))
        emb = self.pool(seq, fm)
        return {"logits": self.head(emb), "emb": emb}


# ===================================================================== видео
class _FrameEncoder(nn.Module):
    def __init__(self):
        super().__init__()
        import torchvision
        net = torchvision.models.swin_t(weights=None)
        net.head = nn.Identity()                            # кадр -> вектор 768
        self.net = net


class TemporalHead(nn.Module):
    def __init__(self, dim_in: int = 768, hidden: int = 256, n_layers: int = 1, n_heads: int = 4,
                 num_classes: int = 6):
        super().__init__()
        self.hidden = hidden
        self.proj = nn.Sequential(nn.LayerNorm(dim_in), nn.Linear(dim_in, hidden), nn.GELU(), nn.Dropout(0.2))
        layer = nn.TransformerEncoderLayer(hidden, n_heads, 2 * hidden, 0.2, activation="gelu",
                                           batch_first=True, norm_first=True)
        self.enc = nn.TransformerEncoder(layer, n_layers, enable_nested_tensor=False)
        self.pool = AttentionPool(hidden)
        self.head = nn.Sequential(nn.LayerNorm(hidden), nn.Dropout(0.2), nn.Linear(hidden, num_classes))

    def forward(self, feats, times):
        """feats (B, T, 768), times (B, T) в секундах шкалы 0..3 -> logits (B, 6), emb (B, 256)."""
        h = self.proj(feats)
        h = self.enc(h + time_positional_encoding(times, self.hidden))
        emb = self.pool(h)
        return {"logits": self.head(emb), "emb": emb}


class VideoEmotionNet(nn.Module):
    """Вход: frames (B, T, 3, 224, 224) float32 — кропы лица, нормированные ImageNet;
    times (B, T) — см. ``preprocess.frame_times``. Выход: logits (B, 6), emb (B, 256)."""

    def __init__(self):
        super().__init__()
        self.backbone = _FrameEncoder()
        self.temporal = TemporalHead()

    def encode_frames(self, x):                             # (N, 3, 224, 224) -> (N, 768)
        return self.backbone.net(x)

    def forward(self, frames, times):
        B, T = frames.shape[:2]
        f = self.encode_frames(frames.flatten(0, 1)).view(B, T, -1)
        return self.temporal(f, times)


def load_audio_model(path, device="cpu") -> AudioEmotionNet:
    ck = torch.load(path, map_location=device, weights_only=False)
    model = AudioEmotionNet(ck["hf_config"])
    sd = dict(ck["state_dict"])
    want = set(model.state_dict())
    # весовая нормировка pos_conv: новые torch/transformers — parametrizations.weight.original0/1,
    # старые — weight_g / weight_v
    for old, new in ((".parametrizations.weight.original0", ".weight_g"),
                     (".parametrizations.weight.original1", ".weight_v")):
        for k in [k for k in sd if k.endswith(old)]:
            if k not in want and k.replace(old, new) in want:
                sd[k.replace(old, new)] = sd.pop(k)
    model.load_state_dict(sd)
    return model.eval().to(device)


def load_video_model(path, device="cpu") -> VideoEmotionNet:
    ck = torch.load(path, map_location=device, weights_only=False)
    model = VideoEmotionNet()
    model.load_state_dict(ck["state_dict"])
    return model.eval().to(device)
