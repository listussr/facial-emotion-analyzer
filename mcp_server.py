"""
MCP-сервер affectra-data для одного разбора: ручки только на чтение по stdio (запускает агент).

    python mcp_server.py --session upload_ab12cd --privacy local --profile interview
    python mcp_server.py --series path/to/series.json        # без хранилища и БД — для отладки

Хранилище — ключ только на чтение: S3_ENDPOINT, S3_BUCKET, S3_READER_ACCESS_KEY, S3_READER_SECRET_KEY.
БД — роль affectra_reader: DB_HOST, DB_PORT, DB_NAME, DB_READER_USER, DB_READER_PASSWORD (без пароля — без БД).
Логи идут в stderr: stdout занят протоколом MCP.
"""

import argparse
import json
import logging
import os
import sys

from src.affectra_mcp import PRIVACY_LEVELS
from src.affectra_mcp import AffectraTools
from src.affectra_mcp import RecordingSource
from src.affectra_mcp import SessionContext
from src.affectra_mcp import create_server
from src.emotion_moments import EmotionSeries
from src.emotion_moments import load_profile

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def make_source() -> RecordingSource:
    from src.object_storage import S3Settings
    from src.object_storage import S3Storage

    storage = S3Storage(S3Settings(
        endpoint=os.getenv("S3_ENDPOINT") or None,
        access_key=os.getenv("S3_READER_ACCESS_KEY", "affectra-reader"),
        secret_key=os.getenv("S3_READER_SECRET_KEY", "affectra-reader-secret"),
        bucket=os.getenv("S3_BUCKET", "affectra-recordings"),
    ))
    if not os.getenv("DB_READER_PASSWORD"):
        return RecordingSource(storage)

    import psycopg2

    def db_connect():
        return psycopg2.connect(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5433")),
                                dbname=os.getenv("DB_NAME", "emotions"),
                                user=os.getenv("DB_READER_USER", "affectra_reader"),
                                password=os.getenv("DB_READER_PASSWORD"), connect_timeout=5)
    return RecordingSource(storage, db_connect)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", help="id сессии")
    ap.add_argument("--series", help="путь к series.json вместо хранилища")
    ap.add_argument("--privacy", choices=PRIVACY_LEVELS, default="local")
    ap.add_argument("--profile", default="interview")
    args = ap.parse_args()
    if not args.session and not args.series:
        ap.error("нужен --session или --series")

    if args.series:
        with open(args.series, encoding="utf-8") as f:
            doc, meta = json.load(f), None
    else:
        doc, meta = make_source().load(args.session)
    ctx = SessionContext(series=EmotionSeries.from_document(doc), profile=load_profile(args.profile),
                         privacy=args.privacy, meta=meta)
    logging.getLogger("mcp_server").info(f"affectra-data: {doc.get('camera_id')} privacy={args.privacy} "
                                         f"profile={args.profile}")
    create_server(AffectraTools(ctx)).run("stdio")


if __name__ == "__main__":
    main()
