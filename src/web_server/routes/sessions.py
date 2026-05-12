import asyncio
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

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
async def stop_session(session_id: str, bg: BackgroundTasks):
    """
    Снимаем сессию из реестра синхронно (мгновенно), а тяжёлый
    `producer.stop()` уносим в фон. Иначе запрос держит worker до 10–15 секунд
    и блокирует другие эндпоинты.
    """
    producer = session_manager.detach(session_id)
    if producer is None:
        raise HTTPException(status_code=404, detail="Session not found")
    bg.add_task(session_manager.shutdown_producer, producer, session_id)
    return {"ok": True, "stopped": session_id}


@router.delete("")
async def stop_all_sessions(bg: BackgroundTasks, kind: Optional[SessionKind] = Query(default=None)):
    """
    Останавливает все активные сессии (опционально — только определённого
    типа). Производит то же, что и DELETE на каждую — снимает их из реестра
    синхронно, останавливает продюсеров в фоне.
    """
    sessions = session_manager.list(kind=kind)
    stopped: List[str] = []
    for s in sessions:
        producer = session_manager.detach(s.id)
        if producer is not None:
            bg.add_task(session_manager.shutdown_producer, producer, s.id)
            stopped.append(s.id)
    return {"ok": True, "stopped": stopped, "count": len(stopped)}
