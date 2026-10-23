"""Автоочистка записей и загрузок."""

from src.web_server.services.retention import DAY_S
from src.web_server.services.retention import RetentionService


def make(recordings_days, uploads_days, old_recordings=(), failing=()):
    calls = {"find": [], "deleted": [], "uploads": []}

    def find(cutoff):
        calls["find"].append(cutoff)
        return list(old_recordings)

    def delete(session_id):
        if session_id in failing:
            raise RuntimeError("s3 down")
        calls["deleted"].append(session_id)
        return 2

    def uploads(cutoff):
        calls["uploads"].append(cutoff)
        return ["up_1"]

    svc = RetentionService(recordings_days, uploads_days, find_recordings=find, delete_recording=delete,
                           delete_uploads=uploads)
    return svc, calls


def test_disabled_by_default():
    svc, calls = make(0, 0, old_recordings=["s1"])
    assert not svc.enabled
    assert svc.run_once(now=100 * DAY_S) == {"recordings": [], "uploads": []}
    assert calls == {"find": [], "deleted": [], "uploads": []}
    svc.start()
    assert svc._thread is None


def test_removes_old_recordings_and_uploads():
    now = 100 * DAY_S
    svc, calls = make(30, 7, old_recordings=["s1", "s2", "s3"], failing={"s2"})
    out = svc.run_once(now=now)
    assert out == {"recordings": ["s1", "s3"], "uploads": ["up_1"]}
    assert calls["find"] == [now - 30 * DAY_S] and calls["uploads"] == [now - 7 * DAY_S]


def test_uploads_only():
    svc, calls = make(0, 1)
    assert svc.enabled
    assert svc.run_once(now=10 * DAY_S)["uploads"] == ["up_1"] and calls["find"] == []
