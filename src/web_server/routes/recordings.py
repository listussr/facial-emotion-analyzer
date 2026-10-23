"""Итоговые записи сессий: список, статус, просмотр и скачивание видео и аналитики из S3."""

import logging

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from fastapi import APIRouter
from fastapi import Header
from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import StreamingResponse

from ..schemas import RecordingInfo
from ..services.db import get_cursor
from ..services.recordings import storage

router = APIRouter(prefix="/api/recordings", tags=["recordings"])
log    = logging.getLogger("recordings")

_COLUMNS = ("camera_id, name, kind, status, frames, duration_sec, size_bytes, has_audio, truncated, summary, "
            "error, started_at, finished_at, video_key, analytics_key")


def _to_info(row: Dict[str, Any]) -> RecordingInfo:
    return RecordingInfo(
        session_id=row["camera_id"],
        name=row["name"],
        kind=row["kind"],
        status=row["status"],
        frames=row["frames"],
        duration_sec=row["duration_sec"],
        size_bytes=row["size_bytes"],
        has_audio=row["has_audio"],
        truncated=bool(row["truncated"]),
        summary=row["summary"],
        error=row["error"],
        started_at=row["started_at"].isoformat() if row["started_at"] else None,
        finished_at=row["finished_at"].isoformat() if row["finished_at"] else None,
    )


def _row(session_id: str) -> Dict[str, Any]:
    with get_cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM session_recordings WHERE camera_id = %s AND status IS NOT NULL",
                    (session_id,))
        row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Запись сессии не найдена")
    return row


@router.get("", response_model=List[RecordingInfo])
def list_recordings(limit: int = Query(default=100, ge=1, le=1000)):
    with get_cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM session_recordings WHERE status IS NOT NULL "
                    f"ORDER BY started_at DESC NULLS LAST LIMIT %s", (limit,))
        rows = cur.fetchall()
    return [_to_info(r) for r in rows]


@router.get("/{session_id}", response_model=RecordingInfo)
def get_recording(session_id: str):
    return _to_info(_row(session_id))


def _stream(key: Optional[str], media_type: str, filename: str, download: bool, byte_range: Optional[str]):
    if not key:
        raise HTTPException(status_code=409, detail="Запись ещё не готова")
    try:
        obj = storage().get(key, byte_range)
    except Exception as e:
        log.warning(f"Cannot read {key}: {e}")
        raise HTTPException(status_code=502, detail="Хранилище записей недоступно")
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(obj["ContentLength"])}
    if obj.get("ContentRange"):
        headers["Content-Range"] = obj["ContentRange"]
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    body = obj["Body"]
    return StreamingResponse(body.iter_chunks(256 * 1024), status_code=206 if obj.get("ContentRange") else 200,
                             media_type=media_type, headers=headers)


@router.get("/{session_id}/video")
def recording_video(session_id: str, download: bool = False, range: Optional[str] = Header(default=None)):
    row = _row(session_id)
    return _stream(row["video_key"], "video/mp4", f"{session_id}.mp4", download, range)


@router.get("/{session_id}/analytics")
def recording_analytics(session_id: str, download: bool = False):
    row = _row(session_id)
    return _stream(row["analytics_key"], "application/json", f"{session_id}.json", download, None)


@router.delete("/{session_id}")
def delete_recording(session_id: str):
    row = _row(session_id)
    if row["status"] in ("recording", "rendering"):
        raise HTTPException(status_code=409, detail="Запись ещё создаётся — удалите её после завершения")
    try:
        deleted = storage().delete_prefix(f"sessions/{session_id}/")
    except Exception as e:
        log.warning(f"Cannot delete objects of {session_id}: {e}")
        raise HTTPException(status_code=502, detail="Хранилище записей недоступно")
    with get_cursor() as cur:
        cur.execute("DELETE FROM session_recordings WHERE camera_id = %s", (session_id,))
    return {"ok": True, "deleted": session_id, "objects": deleted}
