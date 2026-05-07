from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from ..schemas import CreateCameraRequest, SessionInfo, SessionKind
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


@router.delete("/{session_id}")
def stop_session(session_id: str):
    if not session_manager.stop(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True, "stopped": session_id}
