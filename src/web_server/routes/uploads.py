
import logging
import shutil
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..config import settings
from ..schemas import CreateUploadRequest, SessionInfo, UploadInfo
from ..services.session_manager import session_manager

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
log = logging.getLogger("uploads")

ALLOWED_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm"}


@router.get("", response_model=List[UploadInfo])
def list_uploads():
    """
    Список ранее загруженных файлов в `data/uploads/`. Используется UI,
    чтобы пользователь мог запустить обработку повторно без новой загрузки.
    """
    out: List[UploadInfo] = []
    for path in settings.UPLOADS_DIR.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in ALLOWED_EXT:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append(
            UploadInfo(
                upload_id=path.stem,
                filename=path.name,
                size=stat.st_size,
                saved_path=str(path),
                uploaded_at=stat.st_mtime,
            )
        )
    out.sort(key=lambda u: -(u.uploaded_at or 0))
    return out


@router.post("", response_model=UploadInfo)
async def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Empty filename")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported extension {suffix}. Allowed: {sorted(ALLOWED_EXT)}",
        )

    upload_id = uuid.uuid4().hex[:12]
    target = settings.UPLOADS_DIR / f"{upload_id}{suffix}"

    size = 0
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    try:
        with target.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large (>{settings.MAX_UPLOAD_SIZE_MB} MB)",
                    )
                out.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    log.info(f"Uploaded {file.filename} ({size} bytes) -> {target.name}")
    try:
        mtime = target.stat().st_mtime
    except OSError:
        mtime = None
    return UploadInfo(
        upload_id=upload_id,
        filename=file.filename,
        size=size,
        saved_path=str(target),
        uploaded_at=mtime,
    )


@router.post("/session", response_model=SessionInfo)
def start_upload_session(req: CreateUploadRequest):
    """Создаёт сессию обработки для уже загруженного файла."""
    candidates = list(settings.UPLOADS_DIR.glob(f"{req.upload_id}.*"))
    if not candidates:
        raise HTTPException(status_code=404, detail="Upload not found")
    upload_path = candidates[0]
    try:
        return session_manager.create_upload(
            name=req.name,
            upload_path=upload_path,
            frame_rate=req.frame_rate,
            config=req.config,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to start: {e}")


@router.delete("/{upload_id}")
def delete_upload(upload_id: str):
    candidates = list(settings.UPLOADS_DIR.glob(f"{upload_id}.*"))
    if not candidates:
        raise HTTPException(status_code=404, detail="Upload not found")
    for p in candidates:
        p.unlink(missing_ok=True)
    return {"ok": True, "deleted": upload_id}
