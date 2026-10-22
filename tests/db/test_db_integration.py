"""Интеграция с PostgreSQL на localhost:5433: миграция схемы и чтение старых записей."""

import csv
import io
import uuid
import numpy as np
import psycopg2
import pytest

from src.db_managing import FacesDBHandler
from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import LABEL_SET

DB = dict(host="localhost", port=5433, database="emotions", user="app_user", password="basic_app_password")


@pytest.fixture(scope="module")
def handler():
    try:
        psycopg2.connect(connect_timeout=3, **DB).close()
    except Exception:
        pytest.skip("PostgreSQL is not available on localhost:5433")
    h = FacesDBHandler(**DB)
    h.connect()
    yield h
    h.close()


@pytest.fixture
def person(handler):
    user_id = handler.insert(embedding=np.random.default_rng(0).normal(size=512).astype(np.float32),
                             image=np.zeros((8, 8, 3), np.uint8))
    camera_id = f"it_{uuid.uuid4().hex[:8]}"
    yield str(user_id), camera_id
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM face_embeddings WHERE user_id = %s", (str(user_id),))
        cur.execute("DELETE FROM session_audio_timeseries WHERE camera_id = %s", (camera_id,))


def columns(table):
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
        return {r[0] for r in cur.fetchall()}


def payload(camera_id, modality, label="happy"):
    return {"camera_id": camera_id, "track_id": 7, "modality": modality, "label_set": LABEL_SET,
            "labels": list(EMOTION_LABELS), "started_at": 1.0, "ended_at": 2.0,
            "samples": [{"t": 1.5, "label": label, "scores": [0, 0, 0, 0.8, 0.2, 0]}]}


def test_migration_applied_and_idempotent(handler):
    assert {"modality", "label_set"} <= columns("emotion_timeseries")
    assert {"camera_id", "label_set", "data"} <= columns("session_audio_timeseries")
    from src.db_managing import ensure_schema
    ensure_schema(handler._conn)
    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM emotion_timeseries WHERE data->'labels' IS NULL AND label_set <> 'legacy-8'")
        assert cur.fetchone()[0] == 0


def test_write_and_read_all_kinds(handler, person):
    from fastapi.testclient import TestClient
    from src.web_server.main import app
    from src.web_server.services import db as web_db

    user_id, camera_id = person
    handler.insert_emotion_timeseries(user_id, payload(camera_id, "face", "sad"))
    handler.insert_emotion_timeseries(user_id, payload(camera_id, "fused", "happy"))
    handler.insert_session_audio(camera_id, {
        "camera_id": camera_id, "label_set": LABEL_SET, "labels": list(EMOTION_LABELS),
        "windows": [{"window_seq": 0, "pts_start_ms": 0, "pts_end_ms": 2000, "label": "happy",
                     "smoothed_label": "happy", "scores": [0, 0, 0, 1, 0, 0]}]})
    with pytest.raises(ValueError):
        handler.insert_emotion_timeseries(user_id, payload(camera_id, "voice"))

    web_db.init_pool()
    client = TestClient(app)

    face = client.get(f"/api/history/users/{user_id}").json()
    assert face["modality"] == "face" and face["available_modalities"] == ["face", "fused"]
    assert face["label_counts"] == {"sad": 1}
    [track] = face["tracks"]
    assert track["labels"] == list(EMOTION_LABELS) and track["label_set"] == LABEL_SET

    fused = client.get(f"/api/history/sessions/{camera_id}", params={"modality": "fused"}).json()
    assert fused["label_counts"] == {"happy": 1} and fused["unique_users"] == 1

    assert client.get(f"/api/history/sessions/{camera_id}", params={"modality": "voice"}).status_code == 422

    voice = client.get(f"/api/history/sessions/{camera_id}/voice").json()
    assert voice["total_windows"] == 1 and voice["label_counts"] == {"happy": 1}

    text = client.get(f"/api/history/sessions/{camera_id}/export.csv").text
    rows = list(csv.DictReader(io.StringIO(text)))
    assert sorted(r["modality"] for r in rows) == ["face", "fused"]
    assert list(rows[0])[-6:] == list(EMOTION_LABELS)
    only_fused = list(csv.DictReader(io.StringIO(
        client.get(f"/api/history/sessions/{camera_id}/export.csv", params={"modality": "fused"}).text)))
    assert [r["label"] for r in only_fused] == ["happy"]


def test_legacy_records_still_readable(handler):
    """Старые 8-классовые записи читаются API, классы берутся из legacy-набора."""
    from fastapi.testclient import TestClient
    from src.web_server.main import app
    from src.web_server.services import db as web_db

    with psycopg2.connect(**DB) as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id::text FROM emotion_timeseries WHERE label_set = 'legacy-8' LIMIT 1")
        row = cur.fetchone()
    if row is None:
        pytest.skip("no legacy records in this database")
    web_db.init_pool()
    client = TestClient(app)
    hist   = client.get(f"/api/history/users/{row[0]}").json()
    assert hist["tracks"] and all(t["label_set"] == "legacy-8" and len(t["labels"]) == 8 for t in hist["tracks"])
    rows = list(csv.DictReader(io.StringIO(client.get(f"/api/history/users/{row[0]}/export.csv").text)))
    assert rows and "contempt" in rows[0] and "surprise" in rows[0]
