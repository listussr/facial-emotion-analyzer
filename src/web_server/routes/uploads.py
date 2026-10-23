"""Загрузка медиафайлов в объектное хранилище и запуск их обработки."""

import logging
import threading
import uuid

from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional
from urllib.parse import quote
from urllib.parse import unquote

from fastapi import APIRouter
from fastapi import File
from fastapi import HTTPException
from fastapi import UploadFile

from src.video_producing.probe import probe_source
from ..config import settings
from ..schemas import CreateUploadRequest
from ..schemas import SessionInfo
from ..schemas import UploadInfo
from ..services.recordings import storage
from ..services.session_manager import session_manager

router = APIRouter(prefix="/api/uploads", tags=["uploads"])
log    = logging.getLogger("uploads")

VIDEO_EXT   = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
AUDIO_EXT   = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".opus"}
ALLOWED_EXT = VIDEO_EXT | AUDIO_EXT
PREFIX      = "uploads/"

_infos: Dict[str, UploadInfo] = {}
_infos_lock = threading.Lock()


def _flag(value: Optional[bool]) -> str:
    return "" if value is None else ("1" if value else "0")


def _unflag(value: Optional[str]) -> Optional[bool]:
    return None if not value else value == "1"


def _probe(path: Path) -> Dict[str, Optional[object]]:
    try:
        probe = probe_source(str(path))
        return {"has_video": probe.has_video, "has_audio": probe.has_audio, "duration_sec": probe.duration_sec}
    except Exception as e:
        log.warning(f"Cannot probe {path.name}: {e}")
        return {"has_video": None, "has_audio": None, "duration_sec": None}


def _store(path: Path, upload_id: str, filename: str) -> UploadInfo:
    """
    Загрузка файла в хранилище с метаданными: исходное имя, дорожки и длительность.

    Args:
        path      (Path): Локальный файл.
        upload_id (str) : Идентификатор загрузки.
        filename  (str) : Исходное имя файла.

    Returns:
        UploadInfo: Описание загрузки.
    """
    probe = _probe(path)
    key   = f"{PREFIX}{upload_id}{path.suffix.lower()}"
    meta  = {"filename": quote(filename), "has_video": _flag(probe["has_video"]),
             "has_audio": _flag(probe["has_audio"]),
             "duration": "" if probe["duration_sec"] is None else f"{probe['duration_sec']:.3f}"}
    with path.open("rb") as fp:
        storage().upload_fileobj(fp, key, "application/octet-stream", metadata=meta)
    return UploadInfo(upload_id=upload_id, filename=filename, size=path.stat().st_size, saved_path=key,
                      uploaded_at=path.stat().st_mtime, **probe)


def _info_from_object(obj: Dict) -> UploadInfo:
    key = obj["Key"]
    with _infos_lock:
        cached = _infos.get(key)
    if cached is not None:
        return cached
    meta     = (storage().head(key) or {}).get("Metadata", {})
    duration = meta.get("duration")
    info     = UploadInfo(
        upload_id=Path(key).stem,
        filename=unquote(meta.get("filename") or Path(key).name),
        size=int(obj.get("Size") or 0),
        saved_path=key,
        uploaded_at=obj["LastModified"].timestamp() if obj.get("LastModified") else None,
        has_video=_unflag(meta.get("has_video")),
        has_audio=_unflag(meta.get("has_audio")),
        duration_sec=float(duration) if duration else None,
    )
    with _infos_lock:
        _infos[key] = info
    return info


def _find(upload_id: str) -> Optional[UploadInfo]:
    for obj in storage().list_keys(f"{PREFIX}{upload_id}."):
        if Path(obj["Key"]).suffix.lower() in ALLOWED_EXT:
            return _info_from_object(obj)
    return None


@router.get("", response_model=List[UploadInfo])
def list_uploads():
    """Ранее загруженные файлы — для повторной обработки без новой загрузки."""
    try:
        return stored_uploads()
    except Exception as e:
        log.warning(f"Cannot list uploads: {e}")
        raise HTTPException(status_code=502, detail="Хранилище файлов недоступно")


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
    target    = settings.UPLOADS_DIR / f"{upload_id}{suffix}"
    size      = 0
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
        await file.close()

        probe = _probe(target)
        if not probe["has_video"] and not probe["has_audio"]:
            raise HTTPException(status_code=415, detail="В файле нет ни видео, ни звука (или он повреждён)")
        try:
            info = _store(target, upload_id, Path(file.filename).name)
        except Exception as e:
            log.warning(f"Cannot store upload {upload_id}: {e}")
            raise HTTPException(status_code=502, detail="Хранилище файлов недоступно")
    finally:
        target.unlink(missing_ok=True)

    log.info(f"Uploaded {file.filename} ({size} bytes) -> {info.saved_path}")
    return info


@router.post("/session", response_model=SessionInfo)
def start_upload_session(req: CreateUploadRequest):
    info = _find(req.upload_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    try:
        return session_manager.create_upload(
            name=req.name,
            upload_id=req.upload_id,
            upload_key=info.saved_path,
            frame_rate=req.frame_rate,
            config=req.config,
            filename=info.filename,
            file_size=info.size,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to start: {e}")


@router.delete("/{upload_id}")
def delete_upload(upload_id: str):
    info = _find(upload_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    session_id = session_manager.running_upload_session(upload_id)
    if session_id is not None:
        raise HTTPException(status_code=409, detail=f"Файл обрабатывается сессией {session_id} — сначала остановите её")
    _remove(info)
    return {"ok": True, "deleted": upload_id}


def _remove(info: UploadInfo) -> None:
    storage().delete(info.saved_path)
    with _infos_lock:
        _infos.pop(info.saved_path, None)


def stored_uploads() -> List[UploadInfo]:
    """Все загрузки в хранилище, новые первыми."""
    out = [_info_from_object(o) for o in storage().list_keys(PREFIX) if Path(o["Key"]).suffix.lower() in ALLOWED_EXT]
    out.sort(key=lambda u: -(u.uploaded_at or 0))
    return out


def delete_uploads_older_than(cutoff: float) -> List[str]:
    """
    Удаление загрузок, сделанных раньше cutoff; файлы, которые сейчас обрабатываются, остаются.

    Args:
        cutoff (float): Граница по времени загрузки, Unix-время.

    Returns:
        List[str]: Удалённые upload_id.
    """
    deleted = []
    for info in stored_uploads():
        if info.uploaded_at is None or info.uploaded_at >= cutoff:
            continue
        if session_manager.running_upload_session(info.upload_id) is not None:
            continue
        _remove(info)
        deleted.append(info.upload_id)
    return deleted


def migrate_local_uploads(directory: Optional[Path] = None) -> int:
    """
    Перенос файлов, загруженных до хранения в S3, из локального каталога в хранилище.

    Args:
        directory (Path, optional): Каталог загрузок. Defaults to settings.UPLOADS_DIR.

    Returns:
        int: Сколько файлов перенесено.
    """
    directory = directory or settings.UPLOADS_DIR
    moved     = 0
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_EXT:
            continue
        name_file = path.with_name(path.name + ".name")
        try:
            filename = name_file.read_text(encoding="utf-8").strip() if name_file.exists() else path.name
            _store(path, path.stem, filename or path.name)
        except Exception as e:
            log.warning(f"Upload {path.name} not migrated: {e}")
            continue
        path.unlink(missing_ok=True)
        name_file.unlink(missing_ok=True)
        moved += 1
    if moved:
        log.info(f"Migrated {moved} local uploads to object storage")
    return moved


def migrate_local_uploads_in_background() -> None:
    threading.Thread(target=migrate_local_uploads, name="uploads-migration", daemon=True).start()
