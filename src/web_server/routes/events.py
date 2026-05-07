import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..services.session_manager import session_manager
from ..services.events_dispatcher import events_dispatcher

router = APIRouter(prefix="/api/events", tags=["events"])
log = logging.getLogger("events")


@router.websocket("/{session_id}")
async def events_ws(websocket: WebSocket, session_id: str):
    if session_manager.get(session_id) is None:
        await websocket.close(code=1008, reason="Session not found")
        return

    await websocket.accept()
    queue = events_dispatcher.subscribe(session_id)
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"kind": "ping"})
                continue
            if payload is None:
                break
            await websocket.send_json({"kind": "analytics", "data": payload})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"WS error for {session_id}: {e}")
    finally:
        events_dispatcher.unsubscribe(session_id, queue)
