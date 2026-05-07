import asyncio
import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..services.session_manager import session_manager
from ..services.stream_dispatcher import stream_dispatcher

router = APIRouter(prefix="/api/stream", tags=["stream"])
log = logging.getLogger("stream")

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
