import time

from src.video_producing.pacing import FrameDecimator, RealtimePacer


def test_decimator_30_to_10_fps():
    d = FrameDecimator(10)
    pts = [round(i * 1000 / 30) for i in range(90)]  # 3 с при 30 FPS
    accepted = [p for p in pts if d.accept(p)]
    assert len(accepted) == 30
    assert accepted[:4] == [0, 100, 200, 300]


def test_decimator_passes_through_when_source_is_slower():
    d = FrameDecimator(30)
    pts = [i * 100 for i in range(20)]  # источник 10 FPS
    assert all(d.accept(p) for p in pts)


def test_decimator_jumps_after_gap():
    d = FrameDecimator(10)
    assert d.accept(0)
    assert d.accept(5000)           # большой разрыв — без пачки догоняющих кадров
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
