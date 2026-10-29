"""Файлы разбора: run.json, findings.json, report.html, trace.jsonl — в локальный каталог и бакет агента."""

import json

from pathlib import Path
from typing import Any
from typing import Dict
from typing import Optional

from .findings import RunData
from .findings import VERDICT_RU
from .report import build_report
from .trace import Trace

CONTENT_TYPES = {".json": "application/json", ".html": "text/html; charset=utf-8", ".jsonl": "application/x-ndjson"}


def run_document(run: RunData, usage: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """run.json: что разобрано, чем, с каким итогом — для ленты разборов без чтения отчёта."""
    counts = {(v or "none"): n for v, n in run.counts().items()}
    return {
        "run_id": run.run_id, "session_id": run.session_id, "status": run.status, "created_at": run.created_at,
        "finished_at": run.finished_at, "profile": run.profile.name, "privacy": run.privacy, "model": run.model,
        "duration_s": round(run.series.duration_s, 1), "moments": len(run.moments), "verdicts": counts,
        "verdicts_ru": {VERDICT_RU[None if k == "none" else k]: n for k, n in counts.items()},
        "headline": run.headline, "notes": run.notes, "usage": usage or {},
        "session_name": (run.meta or {}).get("name") if run.privacy == "local" else None,
    }


def write_run(run: RunData, out_dir: Path, trace: Optional[Trace] = None) -> Path:
    """
    Файлы разбора в out_dir/<run_id>/.

    Args:
        run     (RunData)          : Разбор.
        out_dir (Path)             : Каталог разборов.
        trace   (Trace, optional)  : Журнал; без него trace.jsonl не пишется. Defaults to None.

    Returns:
        Path: Каталог разбора.
    """
    path = Path(out_dir) / run.run_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "report.html").write_text(build_report(run), encoding="utf-8")
    (path / "findings.json").write_text(json.dumps(run.findings_document(), ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    if trace is not None:
        trace.write(path / "trace.jsonl")
    (path / "run.json").write_text(json.dumps(run_document(run, trace.usage() if trace else None),
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def upload_run(path: Path, storage, prefix: str = "runs/") -> int:
    """
    Загрузка файлов разбора в бакет агента (ключ agent): runs/<run_id>/<файл>.

    Args:
        path    (Path)          : Каталог разбора.
        storage                 : S3Storage с ключом agent и бакетом отчётов.
        prefix  (str, optional) : Префикс ключей. Defaults to "runs/".

    Returns:
        int: Сколько файлов загружено.
    """
    n = 0
    for f in sorted(Path(path).iterdir()):
        if f.is_file():
            storage.upload_file(f, f"{prefix}{path.name}/{f.name}", CONTENT_TYPES.get(f.suffix, "application/octet-stream"))
            n += 1
    return n
