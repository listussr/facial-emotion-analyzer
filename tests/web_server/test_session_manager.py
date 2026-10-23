"""SessionManager и загрузки с подменёнными Kafka, сервисом приёма и хранилищем."""

import time
import pytest

from datetime import datetime
from datetime import timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.web_server.routes.uploads as uploads
import src.web_server.services.session_manager as sm

from src.ingest import SessionCommand
from src.ingest import SessionSpec
from src.ingest import SessionStatus
from src.service_health import ServiceBeat
from src.video_producing.probe import MediaProbe
from src.web_server.schemas import SessionConfig


class FakeStore:
    def __init__(self):
        self.statuses = {}

    def get(self, camera_id):
        return self.statuses.get(camera_id)

    def snapshot(self):
        return dict(self.statuses)

    def wait_for(self, camera_id, timeout, predicate=None):
        return self.statuses.get(camera_id)


class FakeIngest:
    """Отвечает на команду start статусом answer, как узел приёма."""

    def __init__(self, store, answer="live", error=None):
        self.store, self.answer, self.error = store, answer, error
        self.commands = []
        self.removed  = []

    def send(self, key, value, timeout=5.0):
        command = SessionCommand.from_msgpack(value)
        self.commands.append(command)
        if command.action == "start" and self.answer is not None:
            self.store.statuses[key] = SessionStatus(camera_id=key, state=self.answer, spec=command.spec,
                                                     error=self.error, stats={"modalities": ["video", "audio"]})

    def send_async(self, key, value):
        self.removed.append(key)

    def close(self, timeout=5.0):
        pass


class FakeConfigs:
    def __init__(self):
        self.log = []

    def publish(self, config, timeout=5.0):
        self.log.append(("publish", config))

    def delete(self, camera_id, timeout=5.0):
        self.log.append(("delete", camera_id))

    def close(self, timeout=5.0):
        pass


class FakeHealth:
    """service-health, где все сервисы готовы; set_state меняет состояние одного сервиса."""

    def __init__(self):
        self.ready = True
        self.beats = {s: ServiceBeat(service=s, instance="1", state="ready")
                      for s in ("ingest", "consumer", "audio-consumer", "fusion")}

    def set_state(self, service, state=None, age_s=0.0):
        if state is None:
            self.beats.pop(service, None)
        else:
            self.beats[service] = ServiceBeat(service=service, instance="1", state=state,
                                              updated_at=time.time() - age_s)

    def snapshot(self):
        return {b.key: b for b in self.beats.values()}

    def stop(self):
        pass


@pytest.fixture
def env():
    store   = FakeStore()
    ingest  = FakeIngest(store)
    configs = FakeConfigs()
    manager = sm.SessionManager(status_store=store, commands=ingest, configs=configs, status_publisher=ingest,
                                register_recording=lambda *a: None, health_store=FakeHealth(), require_ready=True)
    return manager, store, ingest, configs


def test_config_is_published_before_start_command(env):
    m, store, ingest, configs = env
    cfg = SessionConfig(tracker="deepsort", model="stub", voice_model="voice-x", modalities=["video", "audio"],
                        identification=False, fusion_method="weighted_logits", clip_frames=16)
    info = m.create_camera("Cam", "rtsp://x", 15, cfg)

    published = configs.log[0][1]
    assert configs.log[0][0] == "publish" and published.camera_id == info.id
    assert published.vision.tracker == "deepsort" and published.vision.identification is False
    assert published.audio.model == "voice-x" and published.vision.clip_frames == 16
    assert published.fusion.method == "weighted_logits"

    (command,) = ingest.commands
    spec = SessionSpec.from_dict(command.spec)
    assert command.action == "start" and spec.source == "rtsp://x" and spec.frame_rate == 15
    assert spec.max_width == 854 and spec.config["tracker"] == "deepsort"
    assert info.status == "live" and info.kind == "camera" and info.modalities == ["video", "audio"]


def test_no_fusion_section_without_method(env):
    m, _, _, configs = env
    m.create_camera("Cam", "x.mp4", 10, SessionConfig())
    assert configs.log[0][1].fusion is None


def test_failed_start_removes_config(env):
    m, _, ingest, configs = env
    ingest.answer, ingest.error = "error", "no video in source"
    with pytest.raises(sm.StartError, match="no video"):
        m.create_camera("Cam", "a.wav", 10, SessionConfig(modalities=["video"]))
    assert configs.log[-1][0] == "delete"


def test_silent_ingest_times_out(env):
    m, _, ingest, configs = env
    ingest.answer = None
    with pytest.raises(sm.StartError, match="ingest"):
        m.create_browser("Me", 15, SessionConfig())
    assert [c.action for c in ingest.commands] == ["start", "stop"] and configs.log[-1][0] == "delete"


def test_start_waits_for_services_with_models(env):
    m, _, ingest, configs = env
    health = m._health
    health.set_state("audio-consumer", "starting")
    with pytest.raises(sm.StartError, match="анализ голоса: сервис загружает модели"):
        m.create_browser("Me", 15, SessionConfig(modalities=["video", "audio"]))
    assert ingest.commands == [] and configs.log == []
    m.create_browser("Video only", 15, SessionConfig(modalities=["video"]))

    health.set_state("consumer", "ready", age_s=60)
    with pytest.raises(sm.StartError, match="анализ лица: сервис не запущен"):
        m.create_camera("Cam", "rtsp://x", 10, SessionConfig(modalities=["video"]))

    health.ready = False
    m.create_camera("Cam", "rtsp://x", 10, SessionConfig(modalities=["video"]))
    health.ready, m.require_ready = True, False
    m.create_camera("Cam", "rtsp://x", 10, SessionConfig(modalities=["video", "audio"]))


def test_upload_and_browser_specs(env):
    m, _, ingest, _ = env
    ingest.answer = "running"
    info = m.create_upload("Film", "abc", "uploads/abc.mp4", 20, SessionConfig(), filename="Фильм.mp4", file_size=10)
    spec = SessionSpec.from_dict(ingest.commands[-1].spec)
    assert info.id.startswith("upload_") and info.kind == "upload" and info.filename == "Фильм.mp4"
    assert spec.upload_key == "uploads/abc.mp4" and spec.upload_id == "abc"
    assert m.running_upload_session("abc") == info.id and m.running_upload_session("zzz") is None

    ingest.answer = "live"
    browser = m.create_browser("Me", 15, SessionConfig(modalities=["video"]))
    assert browser.id.startswith("browser_") and browser.kind == "camera"


def test_stop_list_and_cleanup(env):
    m, store, ingest, _ = env
    info = m.create_camera("Cam", "rtsp://x", 10, SessionConfig())
    assert m.stop(info.id) is True and ingest.commands[-1].action == "stop"
    assert m.stop("missing") is False

    now  = time.time()
    spec = SessionSpec(camera_id="old", kind="camera", name="Old").to_dict()
    store.statuses["old"]  = SessionStatus(camera_id="old", state="offline", spec=spec,
                                           updated_at=now - 7200, ended_at=now - 7200)
    store.statuses["lost"] = SessionStatus(camera_id="lost", state="live", spec=dict(spec, camera_id="lost"),
                                           updated_at=now - 30)
    ids = {s.id: s.status for s in m.list()}
    assert "old" not in ids and ids["lost"] == "error" and ids[info.id] == "live"
    assert ingest.removed == ["old"]


def test_session_info_marks_finished_upload_complete():
    spec   = SessionSpec(camera_id="upload_x", kind="upload", name="F", filename="f.mp4", created_at=0).to_dict()
    status = SessionStatus(camera_id="upload_x", state="done", spec=spec, frames_sent=10,
                           stats={"progress": 0.0, "video_duration_sec": 27.0})
    info = sm.session_info(status)
    assert info.status == "done" and info.progress == 1.0 and info.duration_sec == 27.0
    assert info.started_at == datetime.fromtimestamp(0, tz=timezone.utc)
    starting = sm.session_info(SessionStatus(camera_id="upload_x", state="starting", spec=spec))
    assert starting.status == "running"


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def upload_fileobj(self, fp, key, content_type, metadata=None):
        self.objects[key] = (fp.read(), metadata or {})

    def list_keys(self, prefix):
        return [{"Key": k, "Size": len(v[0]), "LastModified": datetime.now(timezone.utc)}
                for k, v in self.objects.items() if k.startswith(prefix)]

    def head(self, key):
        return {"Metadata": self.objects[key][1]} if key in self.objects else None

    def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture
def upload_client(env, tmp_path, monkeypatch):
    m, _, ingest, _ = env
    store = FakeStorage()
    monkeypatch.setattr(uploads, "storage", lambda: store)
    monkeypatch.setattr(uploads, "session_manager", m)
    monkeypatch.setattr(uploads, "probe_source",
                        lambda path: MediaProbe(has_video=True, has_audio=False, duration_sec=12.5))
    monkeypatch.setattr(uploads.settings, "UPLOADS_DIR", tmp_path)
    uploads._infos.clear()
    app = FastAPI()
    app.include_router(uploads.router)
    client = TestClient(app)
    client.store, client.ingest = store, ingest
    return client


def test_upload_goes_to_storage_with_metadata(upload_client, tmp_path):
    up = upload_client.post("/api/uploads", files={"file": ("Мой ролик.mp4", b"data")}).json()
    assert up["filename"] == "Мой ролик.mp4" and up["saved_path"] == f"uploads/{up['upload_id']}.mp4"
    assert list(tmp_path.iterdir()) == []

    (listed,) = upload_client.get("/api/uploads").json()
    assert listed["filename"] == "Мой ролик.mp4" and listed["has_audio"] is False and listed["duration_sec"] == 12.5

    upload_client.ingest.answer = "running"
    session = upload_client.post("/api/uploads/session", json={"upload_id": up["upload_id"], "name": "Ролик"}).json()
    assert session["filename"] == "Мой ролик.mp4"
    r = upload_client.delete(f"/api/uploads/{up['upload_id']}")
    assert r.status_code == 409 and session["id"] in r.json()["detail"]

    upload_client.ingest.store.statuses[session["id"]].state = "done"
    assert upload_client.delete(f"/api/uploads/{up['upload_id']}").status_code == 200
    assert upload_client.store.objects == {}
    assert upload_client.delete(f"/api/uploads/{up['upload_id']}").status_code == 404


def test_local_uploads_are_migrated(upload_client, tmp_path):
    (tmp_path / "abc123.mp4").write_bytes(b"x")
    (tmp_path / "abc123.mp4.name").write_text("Старый.mp4", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("skip")
    assert uploads.migrate_local_uploads(tmp_path) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.txt"]
    (listed,) = upload_client.get("/api/uploads").json()
    assert listed["upload_id"] == "abc123" and listed["filename"] == "Старый.mp4"


def test_old_uploads_are_deleted_except_running(upload_client):
    a = upload_client.post("/api/uploads", files={"file": ("a.mp4", b"a")}).json()
    b = upload_client.post("/api/uploads", files={"file": ("b.mp4", b"b")}).json()
    upload_client.ingest.answer = "running"
    upload_client.post("/api/uploads/session", json={"upload_id": a["upload_id"], "name": "A"})

    assert uploads.delete_uploads_older_than(time.time() - 3600) == []
    assert uploads.delete_uploads_older_than(time.time() + 3600) == [b["upload_id"]]
    assert list(upload_client.store.objects) == [a["saved_path"]]
