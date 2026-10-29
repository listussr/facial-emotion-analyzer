"""
Разбор сессии из командной строки: отчёт по детекторам или полный разбор с моделью.

Запуск (из корня проекта):
    python -m src.agent path/to/series.json                         # только детекторы
    python -m src.agent --session upload_ab12cd --llm               # с моделью из agent.yaml
    python -m src.agent --session upload_ab12cd --llm --model qwen3:8b --base-url http://192.168.1.20:11434/v1
    python -m src.agent --session upload_ab12cd --llm --upload      # и в бакет агента (ключ S3_AGENT_*)

Данные сессии читаются с правами только на чтение (S3_READER_*, DB_READER_PASSWORD). Файлы разбора — в
<output_dir>/<run_id>/: report.html, findings.json, run.json и trace.jsonl (с моделью).
"""

import argparse
import asyncio
import logging
import os
import sys

from .config import load_config
from .runner import analyze
from .store import upload_run


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("series", nargs="?", help="путь к series.json")
    ap.add_argument("--session", help="id сессии: данные из хранилища и БД")
    ap.add_argument("--llm", action="store_true", help="разбирать моменты моделью")
    ap.add_argument("--model", help="модель (иначе из agent.yaml)")
    ap.add_argument("--base-url", help="адрес OpenAI-совместимого сервера модели")
    ap.add_argument("--profile", help="профиль сценария")
    ap.add_argument("--privacy", choices=("local", "external"), help="профиль приватности")
    ap.add_argument("-o", "--out", help="каталог разборов (иначе run.output_dir)")
    ap.add_argument("--upload", action="store_true", help="загрузить разбор в бакет агента")
    args = ap.parse_args()
    if not args.series and not args.session:
        ap.error("нужен путь к series.json или --session")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    config = load_config()
    for value, section, name in ((args.model, config.llm, "model"), (args.base_url, config.llm, "base_url"),
                                 (args.profile, config.run, "profile"), (args.privacy, config.run, "privacy")):
        if value:
            setattr(section, name, value)

    run, path = asyncio.run(analyze(config, session_id=args.session, series_path=args.series, use_llm=args.llm,
                                    out_dir=args.out))
    print(f"{run.session_id}: {run.status}, моментов {len(run.moments)}, вердиктов {len(run.findings)} → "
          f"{path / 'report.html'}")
    for note in run.notes:
        print(f"  ! {note}")
    if args.upload:
        from src.object_storage import S3Settings
        from src.object_storage import S3Storage

        storage = S3Storage(S3Settings(endpoint=os.getenv("S3_ENDPOINT") or None,
                                       access_key=os.getenv("S3_AGENT_ACCESS_KEY", "affectra-agent"),
                                       secret_key=os.getenv("S3_AGENT_SECRET_KEY", "affectra-agent-secret"),
                                       bucket=os.getenv("S3_AGENT_BUCKET", "affectra-agent-reports")))
        print(f"  загружено в бакет агента: {upload_run(path, storage)} файлов")
    return 0


if __name__ == "__main__":
    sys.exit(main())
