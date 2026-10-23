"""API записей сессий с подменёнными БД и хранилищем."""

import io
import pytest

from contextlib import contextmanager
from datetime import datetime
from datetime import timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.web_server.routes.recordings as rec

VIDEO = bytes(range(256)) * 4


class FakeBody(io.BytesIO):
    def iter_chunks(self, size):
        while chunk := self.read(size):
            yield chunk


class FakeStorage:
    def __init__(self):
        self.deleted = []

    def get(self, key, byte_range=None):
        data, content_range = VIDEO, None
        if byte_range:
            start, end    = byte_range.removeprefix("bytes=").split("-")
            end           = int(end) if end else len(VIDEO) - 1
            data          = VIDEO[int(start):end + 1]
            content_range = f"bytes {start}-{end}/{len(VIDEO)}"
        return {"Body": FakeBody(data), "ContentLength": len(data), "ContentRange": content_range}

    def delete_prefix(self, prefix):
        self.deleted.append(prefix)
        return 2


def row(camera_id, status="ready", key="sessions/x/recording.mp4"):
    return {"camera_id": camera_id, "name": "Up", "kind": "upload", "status": status, "frames": 10,
            "duration_sec": 1.0, "size_bytes": len(VIDEO), "has_audio": True, "truncated": False,
            "summary": {"fused_label_counts": {"happy": 3}}, "error": None,
            "started_at": datetime(2026, 1, 1, tzinfo=timezone.utc), "finished_at": None,
            "video_key": key, "analytics_key": key}


@pytest.fixture
def client(monkeypatch):
    rows    = {"ok": row("ok"), "busy": row("busy", status="rendering", key=None)}
    storage = FakeStorage()

    class Cursor:
        def execute(self, sql, params):
            self.sql, self.params = sql, params

        def fetchone(self):
            return rows.get(self.params[0])

        def fetchall(self):
            return list(rows.values())

    @contextmanager
    def get_cursor():
        cur = Cursor()
        yield cur
        if cur.sql.startswith("DELETE"):
            rows.pop(cur.params[0], None)

    monkeypatch.setattr(rec, "get_cursor", get_cursor)
    monkeypatch.setattr(rec, "storage", lambda: storage)
    app = FastAPI()
    app.include_router(rec.router)
    c = TestClient(app)
    c.storage = storage
    return c


def test_list_and_get(client):
    items = client.get("/api/recordings").json()
    assert {i["session_id"] for i in items} == {"ok", "busy"}
    info = client.get("/api/recordings/ok").json()
    assert info["status"] == "ready" and info["summary"]["fused_label_counts"] == {"happy": 3}
    assert client.get("/api/recordings/missing").status_code == 404


def test_video_full_and_range(client):
    r = client.get("/api/recordings/ok/video")
    assert r.status_code == 200 and r.content == VIDEO and r.headers["accept-ranges"] == "bytes"

    r = client.get("/api/recordings/ok/video", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == VIDEO[10:20]
    assert r.headers["content-range"] == f"bytes 10-19/{len(VIDEO)}"

    r = client.get("/api/recordings/ok/video?download=true")
    assert "attachment" in r.headers["content-disposition"]


def test_not_ready_recording(client):
    assert client.get("/api/recordings/busy/video").status_code == 409
    assert client.delete("/api/recordings/busy").status_code == 409


def test_delete(client):
    assert client.delete("/api/recordings/ok").json()["objects"] == 2
    assert client.storage.deleted == ["sessions/ok/"]
    assert client.get("/api/recordings/ok").status_code == 404
