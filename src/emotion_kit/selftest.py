"""Проверка установки: веса читаются, бэкенды дают эталонные выходы.

    python -m emotion_kit.selftest [--torch]

Эталон (models/selftest.npz) снят исходной PyTorch-моделью на фиксированном
синтетическом входе. fp32 ONNX должен совпасть до ~1e-3, int8 — по классу.
"""
import argparse
import sys
import time

import numpy as np

from .backends import MODELS, AudioONNX, VideoONNX


def check(name, got, ref, tol):
    d = float(np.abs(got - ref).max())
    same = int(np.argmax(got)) == int(np.argmax(ref))
    ok = d <= tol if tol is not None else same
    print(f"  {name:28s} max|Δ| {d:.2e}  класс совпал: {same}  -> {'OK' if ok else 'FAIL'}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--torch", action="store_true", help="проверить и PyTorch-веса")
    args = ap.parse_args()
    z = np.load(MODELS / "selftest.npz")
    ok = True
    for prec, tol in (("fp32", 2e-3), ("int8", None)):
        print(f"ONNX {prec}:")
        t = time.time()
        a = AudioONNX(prec).predict(z["wave"])
        v = VideoONNX(prec).predict(z["frames"], z["times"])
        ok &= check("аудио logits", a["logits"], z["audio_logits"], tol)
        ok &= check("видео logits", v["logits"], z["video_logits"], tol)
        print(f"  время {time.time() - t:.1f} c (с загрузкой)")
    if args.torch:
        from .backends import AudioTorch, VideoTorch
        print("PyTorch:")
        a = AudioTorch().predict(z["wave"])
        v = VideoTorch().predict(z["frames"], z["times"])
        ok &= check("аудио logits", a["logits"], z["audio_logits"], 1e-4)
        ok &= check("видео logits", v["logits"], z["video_logits"], 1e-4)
    print("ИТОГ:", "OK" if ok else "ЕСТЬ ОШИБКИ")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
