"""
Сборка series.json для записей, сделанных до его появления, по их analytics.json в хранилище.

Запуск (из корня проекта, хранилище по переменным S3_*):
    S3_ENDPOINT=http://localhost:8333 python scripts/build_series.py
    python scripts/build_series.py --force          # пересобрать и для записей, где series.json уже есть
"""

import argparse
import io
import json
import sys

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.object_storage import S3Settings  # noqa: E402
from src.object_storage import S3Storage  # noqa: E402
from src.session_archive import build_series_from_document  # noqa: E402
from src.session_archive import recording_keys  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="пересобрать существующие series.json")
    args = ap.parse_args()

    storage  = S3Storage(S3Settings.from_env())
    keys     = {o["Key"] for o in storage.list_keys("sessions/")}
    sessions = sorted(k.split("/")[1] for k in keys if k.endswith("/analytics.json"))
    built    = 0
    for camera_id in sessions:
        target = recording_keys(camera_id)
        if target["series"] in keys and not args.force:
            continue
        body     = storage.get(target["analytics"])["Body"].read()
        series   = build_series_from_document(json.loads(body))
        payload  = json.dumps(series, ensure_ascii=False).encode("utf-8")
        storage.upload_fileobj(io.BytesIO(payload), target["series"], "application/json")
        built += 1
        print(f"{camera_id}: {len(series['t'])} s, subject {series['subject']['people']}")
    print(f"series.json built for {built} of {len(sessions)} recordings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
