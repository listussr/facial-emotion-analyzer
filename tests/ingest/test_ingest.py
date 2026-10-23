"""Сервис приёма с подменёнными продюсером, хранилищем и Kafka."""

import struct
import time
import pytest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import src.ingest.service as service_module

from src.ingest import IngestService
from src.ingest import SessionCommand
from src.ingest import SessionSpec
from src.ingest import SessionStatus
from src.ingest.app import create_app
from src.video_producing.probe import MediaProbe
from src.video_producing.sources import browser_source

KAFKA = {"bootstrap_servers": "k:9092", "commands_topic": "cmd", "status_topic": "st", "config_topic": "cfg"}


class FakePublisher:
    def __init__(self):
        self.sent    = []
        self.deleted = []

    def send_async(self, key, value):
        self.sent.append((key, SessionStatus.from_msgpack(value) if value else None))

    def delete(self, key, timeout=5.0):
        self.deleted.append(key)

    def close(self, timeout=5.0):
        pass


class AliveThread:
    def __init__(self):
        self.alive = True

    def is_alive(self):
        return self.alive


class FakeProducer:
    created = []

    def __init__(self, config):
        self.config      = config
        self.is_running  = False
        self.thread      = None
        self.frame_count = 0
        self.error_count = 0
        self.end_reason  = None
        self.stopped     = False
        FakeProducer.created.append(self)

    def start(self):
        self.is_running = True
        self.thread     = AliveThread()

    def stop(self):
        self.stopped, self.is_running, self.end_reason = True, False, "stopped"
        self.thread.alive = False

    def finish(self, reason="eof"):
        self.is_running, self.end_reason = False, reason
        self.thread.alive = False

    def get_stats(self):
        return {"current_fps": 20.0, "modalities": list(self.config.modalities)}


class FakeStorage:
    def __init__(self):
        self.downloads = []

    def download_file(self, key, path):
        self.downloads.append(key)
        path.write_bytes(b"video")


@pytest.fixture
def svc(tmp_path, monkeypatch):
    FakeProducer.created = []
    monkeypatch.setattr(service_module, "probe_source", lambda source: MediaProbe(has_video=True, has_audio=True))
    status, config = FakePublisher(), FakePublisher()
    s = IngestService(KAFKA, storage=FakeStorage(), cache_dir=tmp_path / "cache", status_publisher=status,
                      config_publisher=config, producer_factory=FakeProducer, workers=1, consume=False)
    s.status, s.config = status, config
    yield s
    s._executor.shutdown(wait=True)


def drain(s):
    s._executor.submit(lambda: None).result()


def last_state(s, camera_id):
    return [st.state for key, st in s.status.sent if key == camera_id and st][-1]


def spec(**kw):
    params = dict(camera_id="cam_1", kind="camera", name="Cam", source="rtsp://x", frame_rate=15)
    params.update(kw)
    return SessionSpec(**params)


def test_camera_session_lifecycle(svc):
    svc.handle_command(SessionCommand(action="start", camera_id="cam_1", spec=spec().to_dict()))
    assert svc.status.sent[0][1].state == "starting"
    drain(svc)
    (producer,) = FakeProducer.created
    assert producer.config.source == "rtsp://x" and producer.config.frame_rate == 15
    assert producer.config.stop_on_end is False and producer.config.max_width == 854

    svc.tick()
    assert last_state(svc, "cam_1") == "live"
    assert svc.status.sent[-1][1].stats["current_fps"] == 20.0

    svc.handle_command(SessionCommand(action="stop", camera_id="cam_1"))
    drain(svc)
    svc.tick()
    assert producer.stopped and last_state(svc, "cam_1") == "offline"
    assert svc.config.deleted == ["cam_1"] and not svc.has_session("cam_1")


def test_unreachable_source_reports_error(svc, monkeypatch):
    monkeypatch.setattr(service_module, "probe_source", lambda source: MediaProbe(has_video=False, has_audio=True))
    svc.start(spec(modalities=["video"]))
    drain(svc)
    status = svc.status.sent[-1][1]
    assert status.state == "error" and status.error and not FakeProducer.created
    svc.tick()
    assert svc.config.deleted == ["cam_1"]


def test_upload_is_downloaded_and_cleaned(svc):
    svc.start(spec(camera_id="upload_1", kind="upload", upload_key="uploads/abc.mp4", upload_id="abc"))
    drain(svc)
    (producer,) = FakeProducer.created
    local = svc.cache_dir / "upload_1.mp4"
    assert svc.storage.downloads == ["uploads/abc.mp4"] and producer.config.source == str(local)
    assert producer.config.stop_on_end is True and local.exists()

    svc.tick()
    assert last_state(svc, "upload_1") == "running"
    producer.finish()
    svc.tick()
    assert last_state(svc, "upload_1") == "done" and not local.exists()
    assert svc.status.sent[-1][1].ended_at is not None


def test_stale_and_duplicate_commands(svc):
    old = SessionCommand(action="start", camera_id="cam_1", spec=spec().to_dict(), issued_at=time.time() - 120)
    svc.handle_command(old)
    assert not svc.status.sent
    svc.start(spec())
    svc.start(spec())
    drain(svc)
    assert len(FakeProducer.created) == 1


def test_browser_capture_ws(svc):
    svc.start(spec(camera_id="browser_1", kind="browser", modalities=["video", "audio"]))
    drain(svc)
    source = browser_source.get("browser_1")
    assert source is not None and FakeProducer.created[0].config.source == "browser:browser_1"

    client = TestClient(create_app(svc, run_loop=False))
    with client.websocket_connect("/api/capture/browser_1") as ws:
        ws.send_bytes(struct.pack(">BI", 1, 40) + b"\xff\xd8jpeg")
        ws.send_bytes(struct.pack(">BI", 2, 0) + b"\x00\x00" * 1600)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/capture/browser_1") as second:
                second.receive_text()
    assert source._queue.qsize() == 3
    drain(svc)
    assert FakeProducer.created[0].stopped and browser_source.get("browser_1") is None

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/capture/nope") as ws:
            ws.receive_text()
    assert client.get("/health").json()["ingest_id"] == svc.ingest_id


class FakeStatusStore:
    def __init__(self, statuses):
        self.statuses = statuses

    def wait_for(self, key, timeout, predicate=None):
        status = self.statuses.get(key)
        return status if status is None or predicate is None or predicate(status) else None

    def stop(self):
        pass


class FakeUpstream:
    def __init__(self, url, relay_from):
        self.url, self.relay_from, self.sent = url, relay_from, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def send(self, data):
        self.sent.append(data)


def test_browser_ws_is_relayed_to_owner_node(svc):
    owner = SessionStatus(camera_id="browser_2", state="live", spec={}, ingest_id="ingest-other",
                          ingest_address="10.0.0.7:8001")
    gone  = SessionStatus(camera_id="browser_3", state="offline", spec={}, ingest_id="ingest-other",
                          ingest_address="10.0.0.7:8001")
    upstreams = []

    def connect(url, relay_from):
        upstreams.append(FakeUpstream(url, relay_from))
        return upstreams[-1]

    client = TestClient(create_app(svc, run_loop=False, status_store=FakeStatusStore(
        {"browser_2": owner, "browser_3": gone}), connect=connect))
    frame = struct.pack(">BI", 1, 40) + b"\xff\xd8jpeg"
    with client.websocket_connect("/api/capture/browser_2") as ws:
        ws.send_bytes(frame)
        ws.send_bytes(frame)
    assert len(upstreams) == 1
    up = upstreams[0]
    assert up.url == "ws://10.0.0.7:8001/api/capture/browser_2" and up.relay_from == svc.ingest_id
    assert up.sent == [frame, frame] and up.closed

    for session_id, headers in (("browser_3", {}), ("browser_2", {"x-affectra-relay": "ingest-x"})):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/api/capture/{session_id}", headers=headers) as ws:
                ws.receive_text()
    assert len(upstreams) == 1


def test_status_carries_node_address(svc):
    svc.address = "10.0.0.5:8001"
    svc.start(spec(camera_id="cam_addr"))
    drain(svc)
    assert [st.ingest_address for key, st in svc.status.sent if key == "cam_addr"][-1] == "10.0.0.5:8001"
