import threading
import pytest

from src.frames_processing.session_control import SessionControl
from src.session_config import SessionConfig
from src.session_config import SessionConfigStore
from src.session_config import VisionSettings

DEFAULTS = VisionSettings(tracker="bytetrack", model="stub", device="cpu",
                          detection_frequency=1, clip_frames=8, emotion_hop_ms=1000,
                          identification=True)


def cfg(rev, **vision):
    return SessionConfig(camera_id="cam", revision=rev, updated_at=1.0,
                         vision=VisionSettings(**vision) if vision else None)


@pytest.fixture
def store():
    return SessionConfigStore("unused:1")


def control(store=None, wait=0.0):
    c = SessionControl(DEFAULTS, store=store, first_frame_wait_s=wait)
    if store is not None:
        store.set_on_change(c.on_config_change)
    return c


def test_defaults_must_be_complete():
    with pytest.raises(ValueError):
        SessionControl(VisionSettings(tracker="bytetrack"))


def test_no_store_gives_defaults():
    assert control().settings("cam") == DEFAULTS


def test_legacy_session_config_is_fallback():
    s = control().settings("cam", {"tracker": "deepsort", "model": None, "device": "cpu"})
    assert s.tracker == "deepsort" and s.model == "stub"


def test_store_config_overrides_defaults_and_legacy(store):
    store._apply(b"cam", cfg(1, tracker="deepsort", clip_frames=16).to_msgpack())
    s = control(store).settings("cam", {"tracker": "bytetrack"})
    assert s.tracker == "deepsort" and s.clip_frames == 16 and s.model == "stub"


def test_first_frame_waits_for_late_config(store):
    c = control(store, wait=1.0)
    threading.Timer(0.05, lambda: store._apply(b"cam", cfg(1, model="other").to_msgpack())).start()
    assert c.settings("cam").model == "other"


def test_settings_are_cached_until_change(store):
    c = control(store)
    store._apply(b"cam", cfg(1, model="a").to_msgpack())
    assert c.settings("cam").model == "a"
    store._apply(b"cam", cfg(2, model="b").to_msgpack())
    assert c.settings("cam").model == "b"
    assert c.take_reset("cam") is False


def test_tracker_change_requests_reset_once(store):
    c = control(store)
    store._apply(b"cam", cfg(1, tracker="bytetrack").to_msgpack())
    c.settings("cam")
    store._apply(b"cam", cfg(2, tracker="deepsort").to_msgpack())
    assert c.take_reset("cam") is True
    assert c.take_reset("cam") is False
    assert c.settings("cam").tracker == "deepsort"


def test_tombstone_returns_to_defaults(store):
    c = control(store)
    store._apply(b"cam", cfg(1, tracker="deepsort").to_msgpack())
    assert c.settings("cam").tracker == "deepsort"
    store._apply(b"cam", None)
    assert c.settings("cam") == DEFAULTS
    assert c.take_reset("cam") is True


def test_invalid_values_replaced_by_defaults(store):
    store._apply(b"cam", cfg(1, tracker="sort", device="tpu", detection_frequency=0,
                             clip_frames=12, emotion_hop_ms=50, identification="no").to_msgpack())
    assert control(store).settings("cam") == DEFAULTS


def test_per_camera_frame_index_and_forget():
    c = control()
    assert [c.next_frame_index("a") for _ in range(3)] == [1, 2, 3]
    assert c.next_frame_index("b") == 1
    c.forget("a")
    assert c.next_frame_index("a") == 1
