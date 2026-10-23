"""
Бенчмарк ProcessingPipeline для отчёта/НИР.

Гоняем один и тот же видеофайл через несколько конфигов (трекер × модель ×
число ORT-потоков × pipelining on/off), считаем FPS, p50/p95/p99 latency на
кадр и пик RSS поверх baseline. Каждый конфиг — отдельный subprocess, чтобы
память не перетекала между запусками.

Запуск (из корня проекта):
    python scripts/benchmark.py
    python scripts/benchmark.py --video src/test_videos/film.mp4 --frames 400
    python scripts/benchmark.py --configs bytetrack_fp32_t7 bytetrack_int8_t7

Перед запуском должны быть подняты:
    docker compose -f kafka_server\\docker-compose.yml up -d
    docker compose -f faces_emotions_database\\docker-compose.yaml up -d

Результаты:
    scripts/bench_results/results.csv
    scripts/bench_results/fps.png
    scripts/bench_results/latency.png
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time

from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

OUT_DIR = REPO / "scripts" / "bench_results"
OUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Config:
    """Конфиг прогона: трекер, модель, потоки ONNX Runtime и включён ли конвейер стадий A/B/C."""
    name: str
    tracker: str
    model: str
    num_threads: int
    threaded: bool


DEFAULT_CONFIGS: List[Config] = [
    Config("deepsort_resnet18_t7",     "deepsort",  "resnet-18",       7, threaded=True),
    Config("bytetrack_resnet18_t2",    "bytetrack", "resnet-18",       2, threaded=True),
    Config("bytetrack_resnet18_t7",    "bytetrack", "resnet-18",       7, threaded=True),
    Config("bytetrack_resnet18_int8",  "bytetrack", "resnet-18-int8",  7, threaded=True),
    Config("bytetrack_resnet50",       "bytetrack", "resnet-50",       7, threaded=True),
    Config("bytetrack_resnet50_int8",  "bytetrack", "resnet-50-int8",  7, threaded=True),
    Config("bytetrack_effnetb3",       "bytetrack", "efficientnet-b3", 7, threaded=True),
    Config("bytetrack_effnetb3_int8",  "bytetrack", "efficientnet-b3-int8", 7, threaded=True),
    Config("bytetrack_convnext",       "bytetrack", "convnext",        7, threaded=True),
    Config("bytetrack_convnext_int8",  "bytetrack", "convnext-int8",   7, threaded=True),
    Config("bytetrack_convnext_gelu",  "bytetrack", "convnext-gelu",   7, threaded=True),
    Config("bytetrack_swin",           "bytetrack", "swin-tiny",       7, threaded=True),
    Config("bytetrack_swin_int8",      "bytetrack", "swin-tiny-int8",  7, threaded=True),
    Config("bytetrack_resnet18_t7_seq","bytetrack", "resnet-18",       7, threaded=False),
]


def run_worker(cfg: Config, video_path: Path, n_frames: int, warmup: int) -> Dict:
    """
    Прогон одного конфига в subprocess; модули пайплайна импортируются здесь, а не в driver.

    Args:
        cfg        (Config): Конфиг прогона.
        video_path (Path)  : Видео.
        n_frames   (int)   : Кадров для замера.
        warmup     (int)   : Прогревочных кадров.

    Returns:
        Dict: FPS, задержки по перцентилям и прирост RSS.
    """
    import gc
    import cv2
    import psutil

    from src.frames_processing.processing_pipline import ProcessingPipeline

    proc = psutil.Process(os.getpid())

    pipeline = ProcessingPipeline(
        kafka_settings={
            "bootstrap_servers": "localhost:9092",
            "input_topic": "raw-video-frames",
            "output_topic": "vision-analytics",
            "group_id": f"benchmark-{cfg.name}-{int(time.time())}",
        },
        detector_settings={"min_detection_confidence": 0.5},
        analyzer_settings={"model": cfg.model, "device": "cpu", "num_threads": cfg.num_threads},
        tracker_settings={"type": cfg.tracker, "n_init": 1, "max_age": 30, "frame_rate": 30},
        identifier_settings={
            "host": "localhost", "port": 5433, "database": "emotions",
            "user": "app_user", "password": "basic_app_password", "threshold": 0.4,
        },
        detection_frequency=1,
    )

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    metadata = {"camera_id": f"bench_{cfg.name}", "frame_id": "0"}

    for i in range(warmup):
        ret, frame = cap.read()
        if not ret:
            break
        metadata["frame_id"] = f"warm_{i}"
        pipeline._handle_frame(frame, metadata)

    pipeline._cached_faces.clear()
    pipeline._tracks_face_valid.clear()
    pipeline._frame_num = 0
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    gc.collect()
    baseline_rss = proc.memory_info().rss

    frame_times: List[float] = []
    peak_box = [baseline_rss]

    def peek():
        rss = proc.memory_info().rss
        if rss > peak_box[0]:
            peak_box[0] = rss

    t_start = time.time()
    _run_handle_frame_loop(cap, pipeline, metadata, n_frames, frame_times, peek)
    elapsed  = time.time() - t_start
    peak_rss = peak_box[0]

    for key, cached in list(pipeline._cached_faces.items()):
        try:
            pipeline._flush_emotion_timeseries(cached, key)
        except Exception:
            pass

    cap.release()
    pipeline._identity_executor.shutdown(wait=False, cancel_futures=True)
    try:
        pipeline._kafka.close()
    except Exception:
        pass

    if not frame_times:
        return {
            "config": cfg.name,
            "frames": 0,
            "elapsed_s": 0,
            "fps": 0,
            "mean_ms": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0,
            "delta_rss_mb": 0,
            "error": "no frames decoded",
        }

    sorted_t  = sorted(frame_times)
    p         = lambda q: sorted_t[max(0, min(len(sorted_t) - 1, int(len(sorted_t) * q)))]
    final_rss = proc.memory_info().rss

    return {
        "config": cfg.name,
        "tracker": cfg.tracker,
        "model": cfg.model,
        "num_threads": cfg.num_threads,
        "threaded": cfg.threaded,
        "frames": len(frame_times),
        "elapsed_s": round(elapsed, 3),
        "fps": round(len(frame_times) / elapsed, 2) if elapsed > 0 else 0,
        "mean_ms": round(statistics.mean(frame_times), 2),
        "p50_ms": round(p(0.50), 2),
        "p95_ms": round(p(0.95), 2),
        "p99_ms": round(p(0.99), 2),
        "delta_rss_mb": round((max(peak_rss, final_rss) - baseline_rss) / (1024 * 1024), 1),
    }


def _run_handle_frame_loop(cap, pipeline, metadata, n_frames, out_times, peek):
    import time as _t
    import cv2 as _cv2  # noqa
    for i in range(n_frames):
        ret, frame = cap.read()
        if not ret:
            break
        metadata["frame_id"] = f"f{i}"
        t0                   = _t.time()
        pipeline._handle_frame(frame, metadata)
        out_times.append((_t.time() - t0) * 1000.0)
        if i % 20 == 0:
            peek()


def _spawn(cfg: Config, video: Path, n_frames: int, warmup: int) -> Dict:
    """Прогон конфига в отдельном процессе, чтобы память не перетекала между запусками."""
    print(f"\n>> Running {cfg.name} ...")
    cmd = [
        sys.executable, str(Path(__file__).resolve()),
        "--worker",
        "--config", json.dumps(asdict(cfg)),
        "--video", str(video),
        "--frames", str(n_frames),
        "--warmup", str(warmup),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr, file=sys.stderr)
        return {"config": cfg.name, "error": res.stderr.strip().splitlines()[-1] if res.stderr else "subprocess failed"}
    for line in reversed(res.stdout.splitlines()):
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    return {"config": cfg.name, "error": "no RESULT line"}


def _write_csv(rows: List[Dict], path: Path) -> None:
    import csv
    if not rows:
        return
    keys = sorted({k for r in rows for k in r.keys()})
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  wrote {path}")


def _plot(rows: List[Dict]) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — пропускаем графики (pip install matplotlib)")
        return

    rows = [r for r in rows if "fps" in r and not r.get("error")]
    if not rows:
        return
    rows.sort(key=lambda r: r.get("fps", 0))

    names = [r["config"] for r in rows]

    fps     = [r["fps"] for r in rows]
    fig, ax = plt.subplots(figsize=(9, max(3, 0.5 * len(rows))))
    bars    = ax.barh(names, fps, color="#6366f1")
    ax.set_xlabel("FPS")
    ax.set_title("ProcessingPipeline throughput")
    for bar, v in zip(bars, fps):
        ax.text(v + 0.2, bar.get_y() + bar.get_height() / 2, f"{v:.1f}", va="center", fontsize=9)
    plt.tight_layout()
    fps_path = OUT_DIR / "fps.png"
    plt.savefig(fps_path, dpi=140)
    plt.close()
    print(f"  wrote {fps_path}")

    p50     = [r["p50_ms"] for r in rows]
    p95     = [r["p95_ms"] for r in rows]
    p99     = [r["p99_ms"] for r in rows]
    fig, ax = plt.subplots(figsize=(9, max(3, 0.5 * len(rows))))
    import numpy as np
    y = np.arange(len(rows))
    h = 0.27
    ax.barh(y - h, p50, h, label="p50", color="#10b981")
    ax.barh(y,     p95, h, label="p95", color="#6366f1")
    ax.barh(y + h, p99, h, label="p99", color="#a855f7")
    ax.set_yticks(y, labels=names)
    ax.set_xlabel("Per-frame latency, ms")
    ax.set_title("ProcessingPipeline frame latency")
    ax.legend()
    plt.tight_layout()
    lat_path = OUT_DIR / "latency.png"
    plt.savefig(lat_path, dpi=140)
    plt.close()
    print(f"  wrote {lat_path}")


def _print_table(rows: List[Dict]) -> None:
    if not rows:
        return
    cols   = ["config", "fps", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "delta_rss_mb", "frames"]
    widths = {c: max(len(c), max((len(str(r.get(c, ""))) for r in rows), default=0)) for c in cols}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print()
    print(header)
    print("-" * len(header))
    for r in rows:
        if r.get("error"):
            print(f"{r['config']}: ERROR {r['error']}")
            continue
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", default=str(REPO / "src" / "test_videos" / "film.mp4"),
                    help="Путь к тестовому видео.")
    ap.add_argument("--frames", type=int, default=300, help="Сколько кадров измерять.")
    ap.add_argument("--warmup", type=int, default=20, help="Прогревочных кадров перед измерением.")
    ap.add_argument("--configs", nargs="+", default=None,
                    help="Имена конфигов из DEFAULT_CONFIGS, которые надо прогнать. По умолчанию — все.")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--config", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.worker:
        cfg_dict = json.loads(args.config)
        cfg      = Config(**cfg_dict)
        try:
            res = run_worker(cfg, Path(args.video), args.frames, args.warmup)
        except Exception as e:
            res = {"config": cfg.name, "error": f"{type(e).__name__}: {e}"}
        print("RESULT:" + json.dumps(res))
        return

    video = Path(args.video)
    if not video.exists():
        print(f"Video not found: {video}", file=sys.stderr)
        sys.exit(1)

    if args.configs:
        chosen  = [c for c in DEFAULT_CONFIGS if c.name in set(args.configs)]
        unknown = set(args.configs) - {c.name for c in DEFAULT_CONFIGS}
        if unknown:
            print(f"Unknown configs: {sorted(unknown)}", file=sys.stderr)
            sys.exit(1)
    else:
        chosen = DEFAULT_CONFIGS

    print(f"Benchmarking {len(chosen)} configs against {video.name} "
          f"({args.frames} frames, {args.warmup} warmup)")

    results: List[Dict] = []
    for cfg in chosen:
        r = _spawn(cfg, video, args.frames, args.warmup)
        results.append(r)

    _print_table(results)
    _write_csv(results, OUT_DIR / "results.csv")
    _plot(results)


if __name__ == "__main__":
    main()
