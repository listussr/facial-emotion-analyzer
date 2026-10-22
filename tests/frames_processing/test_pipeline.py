"""ProcessingPipeline с подменёнными Kafka, детектором, трекером и БД; модель эмоций — заглушка."""

import time
import numpy as np
import pytest
import src.frames_processing.processing_pipline as pp

from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import LABEL_SET
from src.emotion_labels import NUM_EMOTIONS
from src.session_config import SessionConfig
from src.session_config import SessionConfigStore
from src.session_config import VisionSettings


class FakeKafka:
    def __init__(self, *a, **kw):
        self.extra    = kw.get("extra_input_topics")
        self.produced = []

    def produce(self, result):
        self.produced.append(result)

    def close(self):
        pass


class FakeDetector:
    calls = 0

    def __init__(self, *a, **kw):
        pass

    def detect(self, frame):
        FakeDetector.calls += 1
        return [(10, 10, 40, 40, 0.99)]


class FakeTrack:
    def __init__(self, tid):
        self.track_id, self.hits, self.time_since_update = tid, 5, 0
        self.last_feature                                = None

    def is_confirmed(self):
        return True

    def to_ltrb(self):
        return (10, 10, 50, 50)


class FakeTracker:
    created = []

    def __init__(self, settings):
        self.type = settings.get("type")
        FakeTracker.created.append(self.type)

    def update(self, detections, frame):
        return [FakeTrack(1)]

    def predict(self):
        return [FakeTrack(1)]


class FakeIdentifier:
    def __init__(self, **kw):
        self.timeseries = []

    def identify(self, crop, appearance, on_resolved=None):
        return "person-1"

    def submit_emotion_timeseries(self, face_id, payload):
        self.timeseries.append((face_id, payload))


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeDetector.calls  = 0
    FakeTracker.created = []
    monkeypatch.setattr(pp, "KafkaIO", FakeKafka)
    monkeypatch.setattr(pp, "FaceDetector", FakeDetector)
    monkeypatch.setattr(pp, "get_tracker", lambda s: FakeTracker(s))
    monkeypatch.setattr(pp, "FaceIdentifier", FakeIdentifier)
    monkeypatch.setattr(pp, "fast_face_filter", lambda *a: True)


def make_pipeline(identifier=True, store=None, **kw):
    kw.setdefault("async_encode", False)
    p = pp.ProcessingPipeline(
        kafka_settings={"bootstrap_servers": "x", "input_topic": "raw", "output_topic": "out",
                        "events_topic": "session-events"},
        detector_settings={},
        analyzer_settings={"model": "stub", "device": "cpu"},
        tracker_settings={"type": "bytetrack"},
        identifier_settings={} if identifier else None,
        session_store=store,
        **kw,
    )
    FakeTracker.created = []
    return p


FRAME = np.full((100, 100, 3), 100, np.uint8)


def payload(i, cam="cam", **kw):
    return {"camera_id": cam, "frame_id": str(i), "seq": i, "pts_ms": i * 100,
            "capture_wall_ts": 1000.0 + i / 10, **kw}


def run_frames(p, n, cam="cam"):
    return [p._handle_frame(FRAME, payload(i, cam)) for i in range(n)]


def test_events_topic_is_subscribed():
    p = make_pipeline()
    assert p._kafka.extra == ["session-events"]


FIRST_WINDOW_FRAMES = 16


def test_result_has_timing_and_label_set():
    p = make_pipeline()
    r = run_frames(p, FIRST_WINDOW_FRAMES + 1)[-1]
    assert r["schema_version"] == 2
    assert (r["seq"], r["pts_ms"], r["capture_wall_ts"]) == (16, 1600, 1001.6)
    assert r["label_set"] == LABEL_SET and r["emotion_labels"] == list(EMOTION_LABELS)
    [face] = r["faces"]
    assert face["emotion"] in EMOTION_LABELS
    assert len(face["emotion_scores"]) == NUM_EMOTIONS
    assert face["face_id"] == "person-1"


def test_emotion_windows_are_published_once_per_hop():
    p       = make_pipeline()
    results = run_frames(p, 40)
    faces   = [r["faces"][0] for r in results]
    assert all(f["emotion"] is None for f in faces[:FIRST_WINDOW_FRAMES - 1])
    windows = [(i, f["emotion_window"]) for i, f in enumerate(faces) if "emotion_window" in f]
    assert [i for i, _ in windows] == [15, 25, 35]
    i, w = windows[0]
    assert w["seq"] == 1 and faces[i]["window_seq"] == 1 and faces[-1]["window_seq"] == 3
    assert w["model"] == "stub" and w["frames"] == 5
    assert w["end_pts_ms"] <= results[i]["pts_ms"] and w["start_pts_ms"] == 0
    assert len(w["logits"]) == len(w["probs"]) == NUM_EMOTIONS and len(w["emb"]) == 256
    assert faces[20]["emotion"] == faces[15]["emotion"]


def test_clip_frames_from_session_config():
    store = SessionConfigStore("unused:1")
    store._apply(b"cam", SessionConfig(camera_id="cam", revision=1, vision=VisionSettings(
        clip_frames=16, emotion_hop_ms=500)).to_msgpack())
    p       = make_pipeline(store=store)
    faces   = [r["faces"][0] for r in run_frames(p, 30)]
    windows = [f["emotion_window"] for f in faces if "emotion_window" in f]
    assert windows and windows[0]["frames"] >= 10
    assert len(windows) >= 3


def test_v1_message_still_works():
    p = make_pipeline()
    r = p._handle_frame(FRAME, {"camera_id": "cam", "frame_id": "0", "timestamp": 5.0,
                                "session_config": {"tracker": "deepsort"}})
    assert r["capture_wall_ts"] == 5.0 and r["pts_ms"] is None
    assert FakeTracker.created == ["deepsort"]


def test_identification_disabled_globally():
    p = make_pipeline(identifier=False)
    r = run_frames(p, FIRST_WINDOW_FRAMES + 1)[-1]
    assert r["faces"][0]["face_id"] == pp.ANONYMOUS_FACE_ID
    p._end_session("cam", "eof")


def test_stream_end_flushes_and_releases_camera():
    p = make_pipeline()
    run_frames(p, 30)
    assert ("cam", 1) in p._cached_faces and "cam" in p._session_trackers

    p._handle_event({"event": "stream_end", "camera_id": "cam", "reason": "eof"})
    assert not p._cached_faces and "cam" not in p._session_trackers
    [(face_id, ts)] = p._identifier.timeseries
    assert face_id == "person-1" and ts["label_set"] == LABEL_SET and ts["samples"]
    assert all(len(s["scores"]) == NUM_EMOTIONS for s in ts["samples"])


def test_stream_end_other_camera_untouched():
    p = make_pipeline()
    run_frames(p, 3, cam="a")
    run_frames(p, 3, cam="b")
    p._handle_event({"event": "stream_end", "camera_id": "a"})
    assert "b" in p._session_trackers and "a" not in p._session_trackers


def test_session_config_controls_settings():
    store = SessionConfigStore("unused:1")
    store._apply(b"cam", SessionConfig(camera_id="cam", revision=1, vision=VisionSettings(
        tracker="deepsort", detection_frequency=3, identification=False)).to_msgpack())
    p = make_pipeline(store=store)
    r = run_frames(p, 9)
    assert FakeTracker.created == ["deepsort"]
    assert FakeDetector.calls == 3
    assert r[-1]["faces"][0]["face_id"] == pp.ANONYMOUS_FACE_ID


def test_tracker_change_mid_session_restarts_tracks():
    store = SessionConfigStore("unused:1")
    p     = make_pipeline(store=store)
    run_frames(p, 30)
    store._apply(b"cam", SessionConfig(camera_id="cam", revision=1,
                                       vision=VisionSettings(tracker="deepsort")).to_msgpack())
    run_frames(p, 1)
    assert FakeTracker.created == ["bytetrack", "deepsort"]
    assert len(p._identifier.timeseries) == 1


def test_unloadable_model_falls_back_to_default():
    store = SessionConfigStore("unused:1")
    store._apply(b"cam", SessionConfig(camera_id="cam", revision=1,
                                       vision=VisionSettings(model="no-such-model")).to_msgpack())
    p = make_pipeline(store=store)
    r = run_frames(p, FIRST_WINDOW_FRAMES + 1)[-1]
    assert len(r["faces"][0]["emotion_scores"]) == NUM_EMOTIONS
    assert p._recognizer_key(VisionSettings(model="no-such-model", device="cpu")) in p._failed_recognizers


def test_stale_frames_are_dropped_in_stage_a():
    import queue
    import threading
    import msgpack

    p   = make_pipeline(max_frame_age_ms=500)
    now = time.time()

    class Msg:
        def __init__(self, topic, value):
            self._t, self._v = topic, value

        def topic(self):
            return self._t

        def value(self):
            return self._v

    import cv2
    jpeg = cv2.imencode(".jpg", FRAME)[1].tobytes()
    msgs = [
        Msg("raw", msgpack.packb({**payload(0), "capture_wall_ts": now - 5, "frame_data": jpeg})),
        Msg("raw", msgpack.packb({**payload(1), "capture_wall_ts": now, "frame_data": jpeg})),
        Msg("session-events", msgpack.packb({"event": "stream_end", "camera_id": "cam"})),
    ]
    stop = threading.Event()

    def consume_message(timeout=0.5):
        if msgs:
            return msgs.pop(0)
        stop.set()
        return None

    p._kafka.consume_message = consume_message
    q                        = queue.Queue(maxsize=10)
    p._stage_consume_decode(q, stop)
    items = [q.get_nowait() for _ in range(q.qsize())]
    kinds = [i[0] if i else None for i in items]
    assert kinds == ["frame", "event", None]
    assert items[0][2]["seq"] == 1
    assert p._staleness.dropped == 1


def test_sliver_crop_at_frame_edge_is_skipped(monkeypatch):
    class EdgeTrack(FakeTrack):
        def to_ltrb(self):
            return (97, 10, 140, 50)

    class EdgeTracker(FakeTracker):
        def update(self, detections, frame):
            return [EdgeTrack(1)]

    monkeypatch.setattr(pp, "get_tracker", lambda s: EdgeTracker(s))
    p = make_pipeline()
    r = run_frames(p, 5)[-1]
    assert r["faces"] == [] and not p._cached_faces


def test_sparse_detection_outputs_predicted_boxes_and_crops_only_fresh():
    class SparseTracker(FakeTracker):
        def update(self, detections, frame):
            t = FakeTrack(1)
            return [t]

        def predict(self):
            t                   = FakeTrack(1)
            t.time_since_update = 1
            return [t]

    import src.frames_processing.processing_pipline as module
    store = SessionConfigStore("unused:1")
    store._apply(b"cam", SessionConfig(camera_id="cam", revision=1, vision=VisionSettings(
        detection_frequency=2)).to_msgpack())
    p                  = make_pipeline(store=store)
    module_get         = module.get_tracker
    module.get_tracker = lambda s: SparseTracker(s)
    try:
        crops            = []
        orig             = module.crop_face
        module.crop_face = lambda frame, box: crops.append(1) or orig(frame, box)
        results          = run_frames(p, 40)
    finally:
        module.get_tracker = module_get
        module.crop_face   = orig
    assert FakeDetector.calls == 20
    assert all(len(r["faces"]) == 1 for r in results)
    assert [r["faces"][0]["predicted"] for r in results[:4]] == [False, True, False, True]
    assert any("emotion_window" in r["faces"][0] for r in results)
    assert crops and len(crops) <= 20


def test_no_detections_hides_previous_faces():
    class EmptyAfter(FakeTracker):
        n = 0

        def update(self, detections, frame):
            return [FakeTrack(1)] if detections else []

    class NoFaceDetector(FakeDetector):
        def detect(self, frame):
            FakeDetector.calls += 1
            return [] if FakeDetector.calls > 5 else [(10, 10, 40, 40, 0.99)]

    import src.frames_processing.processing_pipline as module
    saved               = module.get_tracker, module.FaceDetector
    module.get_tracker  = lambda s: EmptyAfter(s)
    module.FaceDetector = NoFaceDetector
    try:
        p       = make_pipeline()
        results = run_frames(p, 8)
    finally:
        module.get_tracker, module.FaceDetector = saved
    assert results[4]["faces"] and results[6]["faces"] == []


def test_tracker_settings_scaled_by_detection_frequency():
    p = pp.ProcessingPipeline(
        kafka_settings={"bootstrap_servers": "x"}, detector_settings={},
        analyzer_settings={"model": "stub"}, identifier_settings=None,
        tracker_settings={"type": "bytetrack", "frame_rate": 20, "minimum_consecutive_frames": 3},
    )
    s1 = p._tracker_settings_for(VisionSettings(tracker="bytetrack", detection_frequency=1))
    s2 = p._tracker_settings_for(VisionSettings(tracker="bytetrack", detection_frequency=2))
    assert (s1["frame_rate"], s1["minimum_consecutive_frames"]) == (20, 3)
    assert (s2["frame_rate"], s2["minimum_consecutive_frames"]) == (10, 2)


def test_detection_frequency_change_recreates_tracker():
    store = SessionConfigStore("unused:1")
    p     = make_pipeline(store=store)
    run_frames(p, 3)
    store._apply(b"cam", SessionConfig(camera_id="cam", revision=1,
                                       vision=VisionSettings(detection_frequency=2)).to_msgpack())
    run_frames(p, 1)
    assert FakeTracker.created == ["bytetrack", "bytetrack"]


def test_async_encoding_gives_same_windows_eventually():
    sync = make_pipeline()
    run_frames(sync, 40)
    p       = make_pipeline(async_encode=True)
    results = []
    for i in range(40):
        results.append(p._handle_frame(FRAME, payload(i)))
        p._collect_encoded(wait=True)
    windows = [r["faces"][0]["emotion_window"] for r in results if "emotion_window" in r["faces"][0]]
    assert len(windows) == 3 and windows[0]["frames"] == 5
    assert p.encode_skipped == 0


def test_busy_encoder_skips_crops_instead_of_queueing():
    import threading
    gate         = threading.Event()
    p            = make_pipeline(async_encode=True, max_encode_inflight=1)
    model        = p._emotion_analyzer
    orig         = model.encode
    model.encode = lambda batch: (gate.wait(5), orig(batch))[1]
    try:
        run_frames(p, 20)
        assert p.encode_skipped > 0 and len(p._encode_jobs) == 1
    finally:
        gate.set()
        p._collect_encoded(wait=True)
        model.encode = orig
    cached = p._cached_faces[("cam", 1)]
    assert len(cached["clip"]) == 1
