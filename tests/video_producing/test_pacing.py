import time

from src.video_producing.pacing import FrameDecimator
from src.video_producing.pacing import RealtimePacer


def test_decimator_30_to_10_fps():
    d        = FrameDecimator(10)
    pts      = [round(i * 1000 / 30) for i in range(90)]
    accepted = [p for p in pts if d.accept(p)]
    assert len(accepted) == 30
    assert accepted[:4] == [0, 100, 200, 300]


def test_decimator_passes_through_when_source_is_slower():
    d   = FrameDecimator(30)
    pts = [i * 100 for i in range(20)]
    assert all(d.accept(p) for p in pts)


def test_decimator_jumps_after_gap():
    d = FrameDecimator(10)
    assert d.accept(0)
    assert d.accept(5000)
    assert not d.accept(5050)
    assert d.accept(5100)


def test_disabled_pacer_does_not_sleep():
    p = RealtimePacer(enabled=False)
    p.start()
    t = time.monotonic()
    p.wait_until(10_000)
    assert time.monotonic() - t < 0.05


def test_pacer_waits_and_respects_stop_flag():
    p = RealtimePacer(enabled=True)
    p.start()
    t = time.monotonic()
    p.wait_until(150)
    assert 0.13 <= time.monotonic() - t < 0.5

    t = time.monotonic()
    p.wait_until(60_000, stop_flag=lambda: True)
    assert time.monotonic() - t < 0.05


def test_release_orderer_does_not_block_video_behind_audio():
    from src.video_producing.pacing import ReleaseOrderer
    o   = ReleaseOrderer()
    out = []
    for release, start, name in [(100, 0, "a0"), (0, 0, "v0"), (33, 33, "v33"), (66, 66, "v66"),
                                 (200, 100, "a100"), (100, 100, "v100"), (133, 133, "v133")]:
        out += [n for _, n in o.push(release, start, name)]
    out += [n for _, n in o.drain()]
    assert out == ["v0", "v33", "v66", "a0", "v100", "v133", "a100"]


def test_release_orderer_keeps_order_for_equal_release():
    from src.video_producing.pacing import ReleaseOrderer
    o     = ReleaseOrderer()
    ready = o.push(10, 5, "x") + o.push(10, 5, "y") + o.push(50, 10, "z")
    assert [n for _, n in ready] == ["x", "y"] and len(o) == 1


def test_pacer_resyncs_after_source_stall():
    p = RealtimePacer(enabled=True, max_lag_ms=300)
    p.start()
    time.sleep(0.6)
    t = time.monotonic()
    p.wait_until(0)
    assert time.monotonic() - t < 0.05
    assert p.resyncs == 1 and 550 <= p.shift_ms <= 800
    t = time.monotonic()
    p.wait_until(150)
    assert 0.1 <= time.monotonic() - t < 0.4


def test_pacer_small_lag_is_not_resynced():
    p = RealtimePacer(enabled=True, max_lag_ms=300)
    p.start()
    time.sleep(0.1)
    p.wait_until(0)
    assert p.resyncs == 0 and p.shift_ms == 0
