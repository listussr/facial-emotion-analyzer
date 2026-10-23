"""Маршруты управления сессиями."""

from typing import List
from typing import Optional

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query

from ..schemas import CreateBrowserRequest
from ..schemas import CreateCameraRequest
from ..schemas import SessionInfo
from ..schemas import SessionKind
from ..services.session_manager import session_manager

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=List[SessionInfo])
def list_sessions(kind: Optional[SessionKind] = Query(default=None)):
    return session_manager.list(kind=kind)


@router.get("/{session_id}", response_model=SessionInfo)
def get_session(session_id: str):
    info = session_manager.get(session_id)
    if info is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return info


@router.post("/camera", response_model=SessionInfo)
def create_camera(req: CreateCameraRequest):
    try:
        return session_manager.create_camera(
            name=req.name,
            source=req.source,
            frame_rate=req.frame_rate,
            config=req.config,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to start camera: {e}")


@router.post("/browser", response_model=SessionInfo)
def create_browser(req: CreateBrowserRequest):
    try:
        return session_manager.create_browser(name=req.name, frame_rate=req.frame_rate, config=req.config)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to start browser session: {e}")


@router.delete("/{session_id}")
def stop_session(session_id: str):
    """Команда остановки узлу приёма; сессия остаётся в списке с итоговым статусом."""
    if not session_manager.stop(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True, "stopped": session_id}


@router.delete("")
def stop_all_sessions(kind: Optional[SessionKind] = Query(default=None)):
    """Остановка всех идущих сессий или только сессий типа kind."""
    stopped = session_manager.stop_all(kind)
    return {"ok": True, "stopped": stopped, "count": len(stopped)}
