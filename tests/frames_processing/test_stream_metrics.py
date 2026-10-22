from src.frames_processing.stream_metrics import LatencyWindow
from src.frames_processing.stream_metrics import StalenessFilter
from src.frames_processing.stream_metrics import capture_ts


def test_capture_ts_v2_v1_and_missing():
    assert capture_ts({"capture_wall_ts": 10.5, "timestamp": 1.0}) == 10.5
    assert capture_ts({"timestamp": 7}) == 7.0
    assert capture_ts({}) is None
    assert capture_ts({"capture_wall_ts": "bad"}) is None


def test_staleness_filter():
    f = StalenessFilter(max_age_ms=1000)
    assert not f.is_stale({"capture_wall_ts": 99.5}, now=100.0)
    assert f.is_stale({"capture_wall_ts": 98.0}, now=100.0)
    assert not f.is_stale({}, now=100.0)
    assert f.dropped == 1


def test_staleness_disabled():
    for age in (None, 0, -5):
        f = StalenessFilter(max_age_ms=age)
        assert not f.is_stale({"capture_wall_ts": 0.0}, now=1e9)


def test_latency_window():
    w = LatencyWindow()
    assert w.summary_ms() == {"p50": 0.0, "p95": 0.0, "max": 0.0}
    for i in range(1, 101):
        w.add(i / 1000)
    s = w.summary_ms()
    assert round(s["p50"]) == 51 and round(s["p95"]) == 96 and round(s["max"]) == 100
    w.reset()
    assert len(w) == 0
