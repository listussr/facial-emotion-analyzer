"""Логика SessionConfigStore без Kafka: сообщения подаются напрямую в _apply."""

import threading
import time
import pytest

from src.session_config import SessionConfig
from src.session_config import SessionConfigStore
from src.session_config import VisionSettings


def cfg(revision: int, camera_id: str = "cam", model: str = "m") -> SessionConfig:
    return SessionConfig(camera_id=camera_id, revision=revision, updated_at=1000.0,
                         vision=VisionSettings(model=model))


def raw(c: SessionConfig) -> bytes:
    return c.to_msgpack()


@pytest.fixture
def events():
    return []


@pytest.fixture
def store(events):
    return SessionConfigStore("unused:1", on_change=lambda cam, old, new: events.append((cam, old, new)))


def test_not_ready_before_start(store):
    assert store.ready is False
    assert store.get("cam") is None
    assert store.snapshot() == {}


def test_first_config_is_applied(store, events):
    store._apply(b"cam", raw(cfg(1)))
    assert store.get("cam") == cfg(1)
    assert events == [("cam", None, cfg(1))]
    assert store.stats().applied == 1 and store.stats().configs == 1


def test_newer_revision_replaces(store, events):
    store._apply(b"cam", raw(cfg(1)))
    store._apply(b"cam", raw(cfg(2, model="new")))
    assert store.get("cam").revision == 2
    assert events[-1] == ("cam", cfg(1), cfg(2, model="new"))
    assert store.stats().applied == 2


def test_stale_revision_ignored(store, events):
    store._apply(b"cam", raw(cfg(2)))
    store._apply(b"cam", raw(cfg(1, model="old")))
    store._apply(b"cam", raw(cfg(2)))
    assert store.get("cam") == cfg(2)
    assert store.stats().stale_ignored == 2
    assert len(events) == 1


def test_tombstone_removes(store, events):
    store._apply(b"cam", raw(cfg(1)))
    store._apply(b"cam", None)
    assert store.get("cam") is None
    assert store.stats().tombstones == 1 and store.stats().configs == 0
    assert events[-1] == ("cam", cfg(1), None)


def test_tombstone_for_unknown_key_is_silent(store, events):
    store._apply(b"ghost", None)
    assert events == []


def test_decode_error_counted(store, events):
    store._apply(b"cam", b"\xc1 not msgpack")
    store._apply(b"cam", b"\x93\x01\x02\x03")
    store._apply(None, raw(cfg(1)))
    assert store.get("cam") is None
    assert store.stats().decode_errors == 3
    assert events == []


def test_cameras_are_independent(store):
    store._apply(b"a", raw(cfg(5, camera_id="a")))
    store._apply(b"b", raw(cfg(1, camera_id="b")))
    assert set(store.snapshot()) == {"a", "b"}
    assert store.get("b").revision == 1


def test_wait_for_returns_when_config_arrives(store):
    threading.Timer(0.05, lambda: store._apply(b"cam", raw(cfg(1)))).start()
    t   = time.monotonic()
    got = store.wait_for("cam", timeout=2.0)
    assert got == cfg(1)
    assert time.monotonic() - t < 1.0


def test_wait_for_returns_immediately_if_present(store):
    store._apply(b"cam", raw(cfg(1)))
    t = time.monotonic()
    assert store.wait_for("cam", timeout=5.0) == cfg(1)
    assert time.monotonic() - t < 0.1


def test_wait_for_times_out(store):
    t = time.monotonic()
    assert store.wait_for("cam", timeout=0.2) is None
    assert 0.15 <= time.monotonic() - t < 1.0


def test_on_change_exception_does_not_break_store():
    def boom(*_):
        raise RuntimeError("callback failed")

    store = SessionConfigStore("unused:1", on_change=boom)
    store._apply(b"cam", raw(cfg(1)))
    store._apply(b"cam", raw(cfg(2)))
    assert store.get("cam").revision == 2
    assert store.stats().callback_errors == 2


def test_on_change_can_read_store_without_deadlock():
    """Колбэк вызывается вне блокировки: чтение хранилища из колбэка не зависает."""
    seen = []

    def cb(cam, old, new):
        seen.append(store.get(cam))

    store = SessionConfigStore("unused:1", on_change=cb)
    done  = threading.Event()
    threading.Thread(target=lambda: (store._apply(b"cam", raw(cfg(1))), done.set()), daemon=True).start()
    assert done.wait(2.0), "deadlock: on_change called under the store lock"


def test_snapshot_is_a_copy(store):
    store._apply(b"cam", raw(cfg(1)))
    snap = store.snapshot()
    snap.clear()
    assert store.get("cam") == cfg(1)


def test_stats_is_a_copy(store):
    s         = store.stats()
    s.applied = 100
    assert store.stats().applied == 0


def test_stop_without_start_is_safe(store):
    store.stop()
    store.stop()
