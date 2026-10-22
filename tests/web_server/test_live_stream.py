import json
import struct

from fastapi.testclient import TestClient

from src.web_server.services.stream_dispatcher import annotated_jpeg
from src.web_server.services.stream_dispatcher import live_frame


def test_live_frame_format():
    payload = {"camera_id": "cam", "frame_data": b"\xff\xd8jpeg", "pts_ms": 1500, "seq": 30,
               "frame_id": "f30", "capture_wall_ts": 1000.5, "processed_width": 1280, "processed_height": 720}
    data   = live_frame(payload)
    (n,)   = struct.unpack(">I", data[:4])
    header = json.loads(data[4:4 + n])
    assert header == {"pts_ms": 1500, "seq": 30, "frame_id": "f30", "capture_wall_ts": 1000.5,
                      "width": 1280, "height": 720}
    assert data[4 + n:] == b"\xff\xd8jpeg"
    assert live_frame({"camera_id": "cam"}) is None
    assert annotated_jpeg({"annotated_frame": b"x"}) == b"x"


def test_live_ws_rejects_unknown_session(monkeypatch):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    from src.web_server.routes import streams
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(streams.live_router)
    monkeypatch.setattr(streams.session_manager, "get", lambda sid: None)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect("/api/live/nope") as ws:
                ws.receive_bytes()
        assert e.value.code == 1008
