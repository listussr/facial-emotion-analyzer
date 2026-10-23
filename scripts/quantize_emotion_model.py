"""
Динамическая INT8-квантизация старых ONNX-моделей эмоций: рядом с fp32 создаётся <name>.int8.onnx.

Запуск:
    python scripts/quantize_emotion_model.py
    python scripts/quantize_emotion_model.py --model resnet_18.onnx
"""

import argparse
import os
import sys

from pathlib import Path

from onnxruntime.quantization import quantize_dynamic
from onnxruntime.quantization import QuantType


MODELS_DIR = Path(__file__).resolve().parents[1] / "src" / "frames_processing" / \
    "processing" / "emotion_recognition" / "models"

TARGETS = {
    "resnet-18-int8":   ("resnet_18.onnx",          "resnet_18.int8.onnx"),
    "convnext-int8":    ("convnext_gelu_head.onnx", "convnext_gelu_head.int8.onnx"),
    "swin-tiny-int8":   ("swin_tiny.onnx",          "swin_tiny.int8.onnx"),
}


def quantize(src: Path, dst: Path, weight_type: QuantType = QuantType.QUInt8) -> None:
    """
    Динамическая квантизация ONNX-модели; QUInt8 быстрее QInt8 на x86 без VNNI.

    Args:
        src         (Path)               : Модель fp32.
        dst         (Path)               : Файл int8.
        weight_type (QuantType, optional): Тип весов. Defaults to QuantType.QUInt8.
    """
    if not src.exists():
        print(f"  [skip] source not found: {src.name}")
        return
    if dst.exists():
        print(f"  [skip] already exists: {dst.name} (delete to re-quantize)")
        return
    print(f"  quantizing {src.name} -> {dst.name} (weight={weight_type.name}) ...", flush=True)
    quantize_dynamic(
        model_input=str(src),
        model_output=str(dst),
        weight_type=weight_type,
    )
    src_mb = src.stat().st_size / (1024 * 1024)
    dst_mb = dst.stat().st_size / (1024 * 1024)
    print(f"  done: {src_mb:.1f} MB -> {dst_mb:.1f} MB")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", help="Имя файла внутри models/ для одной модели "
                                    "(например resnet_18.onnx).")
    ap.add_argument("--all", action="store_true",
                    help="Квантовать все известные модели из TARGETS.")
    ap.add_argument("--qtype", choices=["uint8", "int8"], default="uint8",
                    help="Тип весов: uint8 (по умолчанию, лучше на x86 без VNNI) или int8.")
    args = ap.parse_args()

    weight_type = QuantType.QUInt8 if args.qtype == "uint8" else QuantType.QInt8

    if not MODELS_DIR.exists():
        print(f"Models dir not found: {MODELS_DIR}", file=sys.stderr)
        return 1

    if args.model:
        src = MODELS_DIR / args.model
        dst = src.with_suffix(".int8.onnx")
        quantize(src, dst, weight_type=weight_type)
        return 0

    print(f"Quantizing models in {MODELS_DIR} (weight={weight_type.name})")
    for nice_name, (src_name, dst_name) in TARGETS.items():
        print(f"[{nice_name}]")
        quantize(MODELS_DIR / src_name, MODELS_DIR / dst_name, weight_type=weight_type)
    return 0


if __name__ == "__main__":
    sys.exit(main())
