"""Разбор одного клипа из командной строки.

    python -m emotion_kit.cli --video clip.mp4 [--audio clip.wav] [--backend onnx|torch]
                              [--precision fp32|int8] [--frames 16|8] [--fusion all|<метод>]

Без --audio звук читается из видеофайла (нужен ffmpeg в PATH). Вывод — JSON.
"""
import argparse
import json
import time

from . import preprocess as P
from .fusion import METHODS
from .pipeline import EmotionPipeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video")
    ap.add_argument("--audio")
    ap.add_argument("--backend", default="onnx", choices=["onnx", "torch"])
    ap.add_argument("--precision", default="fp32", choices=["fp32", "int8"])
    ap.add_argument("--frames", type=int, default=16, choices=[16, 8])
    ap.add_argument("--fusion", default="all")
    ap.add_argument("--threads", type=int)
    a = ap.parse_args()
    methods = [m for m in METHODS if m not in ("audio_only", "video_only")] if a.fusion == "all" else [a.fusion]
    pipe = EmotionPipeline(a.backend, a.precision, a.frames, methods[0], a.threads)
    t0 = time.time()
    wave = None
    if a.audio or a.video:
        wave = P.read_audio_raw(a.audio or a.video)       # сырой сигнал; подготовка — в pipeline
    frames = P.read_video_frames(a.video) if a.video else None
    res = pipe.analyze(audio=wave, sr=P.SR, frames=frames, methods=methods)
    res["seconds"] = round(time.time() - t0, 3)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
