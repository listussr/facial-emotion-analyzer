"""Объём записей и загрузок в хранилище и сроки их автоочистки."""

import logging

from fastapi import APIRouter
from fastapi import HTTPException

from ..config import settings
from ..schemas import StorageInfo
from ..schemas import StorageUsage
from ..services.db import get_cursor
from .uploads import stored_uploads

router = APIRouter(prefix="/api/storage", tags=["storage"])
log    = logging.getLogger("storage")


@router.get("", response_model=StorageInfo)
def storage_info():
    try:
        uploads = stored_uploads()
        with get_cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS size FROM session_recordings "
                        "WHERE status IS NOT NULL")
            rec = cur.fetchone()
    except Exception as e:
        log.warning(f"Cannot read storage usage: {e}")
        raise HTTPException(status_code=502, detail="Хранилище недоступно")
    return StorageInfo(
        recordings=StorageUsage(count=int(rec["n"]), size_bytes=int(rec["size"]),
                                retention_days=settings.RECORDINGS_RETENTION_DAYS),
        uploads=StorageUsage(count=len(uploads), size_bytes=sum(u.size for u in uploads),
                             retention_days=settings.UPLOADS_RETENTION_DAYS),
    )
