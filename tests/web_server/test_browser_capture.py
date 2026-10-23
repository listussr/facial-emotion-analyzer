"""Сессия камеры браузера: создание, приём медиа по WebSocket и остановка при отключении."""

import struct
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import src.web_server.routes.capture as capture
import src.web_server.services.session_manager as sm

from src.video_producing.sources import browser_source
from src.web_server.schemas import SessionConfig
from tests.web_server.test_session_manager import calls  # noqa: F401


def packet(kind: int, pts: int, data: bytes) -> bytes:
    return struct.pack(">BI", kind, pts) + data


@pytest.fixture
def manager(calls, monkeypatch):  # noqa: F811
    m = sm.SessionManager()
    monkeypatch.setattr(capture, "session_manager", m)
    return m


def test_create_browser_registers_source(manager, calls):  # noqa: F811
    info = manager.create_browser("Me", 15, SessionConfig(modalities=["video"]))
    src  = browser_source.get(info.id)
    try:
        assert info.id.startswith("browser_") and info.kind == "camera"
        assert src.has_video and not src.has_audio
        started = [c[1] for c in calls if c[0] == "start"][0]
        assert started.source == f"browser:{info.id}" and started.frame_rate == 15
    finally:
        manager.stop(info.id)
    assert browser_source.get(info.id) is None


def test_capture_ws_feeds_source_and_stops_session(manager, calls):  # noqa: F811
    info = manager.create_browser("Me", 15, SessionConfig())
    src  = browser_source.get(info.id)
    app  = FastAPI()
    app.include_router(capture.router)
    client = TestClient(app)

    with client.websocket_connect(f"/api/capture/{info.id}") as ws:
        ws.send_bytes(packet(1, 40, b"\xff\xd8jpeg"))
        ws.send_bytes(packet(2, 0, b"\x00\x00" * 1600))
        ws.send_bytes(b"\x01")
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/capture/{info.id}") as second:
                second.receive_text()

    assert src._queue.qsize() == 3
    assert manager.get(info.id) is None
    assert ("stop", info.id) in calls
    assert browser_source.get(info.id) is None


def test_capture_ws_rejects_unknown_session(manager):
    app = FastAPI()
    app.include_router(capture.router)
    with pytest.raises(WebSocketDisconnect):
        with TestClient(app).websocket_connect("/api/capture/nope") as ws:
            ws.receive_text()
