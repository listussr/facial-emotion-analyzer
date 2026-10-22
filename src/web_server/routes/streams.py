"""Видео сессии: MJPEG размеченных кадров и WebSocket исходных кадров для отрисовки в браузере."""

import asyncio
import logging

from typing import AsyncIterator

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request
from fastapi import WebSocket
from fastapi import WebSocketDisconnect
from fastapi.responses import StreamingResponse

from ..services.session_manager import session_manager
from ..services.stream_dispatcher import live_dispatcher
from ..services.stream_dispatcher import stream_dispatcher

router = APIRouter(prefix="/api/stream", tags=["stream"])
log    = logging.getLogger("stream")

BOUNDARY = "frame"


@router.get("/{session_id}")
async def stream(session_id: str, request: Request):
    if session_manager.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")

    queue = stream_dispatcher.subscribe(session_id)

    async def generator() -> AsyncIterator[bytes]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    jpeg = await asyncio.wait_for(queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    yield b"--" + BOUNDARY.encode() + b"\r\nContent-Length: 0\r\n\r\n"
                    continue
                if jpeg is None:
                    break
                yield (
                    b"--" + BOUNDARY.encode() + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                    + jpeg + b"\r\n"
                )
        finally:
            stream_dispatcher.unsubscribe(session_id, queue)

    return StreamingResponse(
        generator(),
        media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


live_router = APIRouter(prefix="/api/live", tags=["stream"])


@live_router.websocket("/{session_id}")
async def live_ws(websocket: WebSocket, session_id: str):
    """
    Кадры сессии без разметки: [uint32 BE длина заголовка][JSON-заголовок с pts_ms][JPEG].

    Рамки браузер берёт из /api/events и рисует сам, сопоставляя по pts_ms.
    """
    if session_manager.get(session_id) is None:
        await websocket.close(code=1008, reason="Session not found")
        return

    await websocket.accept()
    queue = live_dispatcher.subscribe(session_id)
    try:
        while True:
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"kind": "ping"})
                continue
            if frame is None:
                break
            await websocket.send_bytes(frame)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning(f"live WS error for {session_id}: {e}")
    finally:
        live_dispatcher.unsubscribe(session_id, queue)
