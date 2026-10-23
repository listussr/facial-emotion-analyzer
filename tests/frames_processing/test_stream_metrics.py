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


def test_staleness_warns_about_clock_skew(caplog):
    f = StalenessFilter(max_age_ms=1000, stale_run_warn=3, warn_interval_s=0.0)
    with caplog.at_level("WARNING", logger="staleness"):
        assert not f.is_stale({"capture_wall_ts": 102.0}, now=100.0)
        assert f.max_ahead_s == 2.0
        for _ in range(3):
            assert f.is_stale({"capture_wall_ts": 95.0}, now=100.0)
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2
    assert "2000 ms in the future" in messages[0] and "3 frames in a row" in messages[1]


def test_staleness_warnings_are_rate_limited(caplog):
    f = StalenessFilter(max_age_ms=1000, stale_run_warn=1)
    with caplog.at_level("WARNING", logger="staleness"):
        for _ in range(5):
            f.is_stale({"capture_wall_ts": 0.0}, now=100.0)
        f.is_stale({"capture_wall_ts": 99.9}, now=100.0)
    assert len(caplog.records) == 1 and f.dropped == 5 and f._stale_run == 0
