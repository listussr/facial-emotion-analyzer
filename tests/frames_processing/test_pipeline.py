"""
ProcessingPipeline с подменёнными тяжёлыми зависимостями: Kafka, MediaPipe,
трекер, БД. Модель эмоций — настоящая заглушка на 6 классов.
"""
import time

import numpy as np
import pytest

import src.frames_processing.processing_pipline as pp
from src.emotion_labels import EMOTION_LABELS, LABEL_SET, NUM_EMOTIONS
from src.session_config import SessionConfig, SessionConfigStore, VisionSettings


class FakeKafka:
    def __init__(self, *a, **kw):
        self.extra = kw.get('extra_input_topics')
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
        self.last_feature = None

    def is_confirmed(self):
        return True

    def to_ltrb(self):
        return (10, 10, 50, 50)


class FakeTracker:
    created = []

    def __init__(self, settings):
        self.type = settings.get('type')
        FakeTracker.created.append(self.type)

    def update(self, detections, frame):
        return [FakeTrack(1)]

    def predict(self):
        return [FakeTrack(1)]


class FakeIdentifier:
    def __init__(self, **kw):
        self.timeseries = []

    def identify(self, crop, appearance, on_resolved=None):
        return 'person-1'

    def submit_emotion_timeseries(self, face_id, payload):
        self.timeseries.append((face_id, payload))


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    FakeDetector.calls = 0
    FakeTracker.created = []
    monkeypatch.setattr(pp, 'KafkaIO', FakeKafka)
    monkeypatch.setattr(pp, 'FaceDetector', FakeDetector)
    monkeypatch.setattr(pp, 'get_tracker', lambda s: FakeTracker(s))
    monkeypatch.setattr(pp, 'FaceIdentifier', FakeIdentifier)
    monkeypatch.setattr(pp, 'fast_face_filter', lambda *a: True)


def make_pipeline(identifier=True, store=None, **kw):
    p = pp.ProcessingPipeline(
        kafka_settings={'bootstrap_servers': 'x', 'input_topic': 'raw', 'output_topic': 'out',
                        'events_topic': 'session-events'},
        detector_settings={},
        analyzer_settings={'model': 'stub', 'device': 'cpu'},
        tracker_settings={'type': 'bytetrack'},
        identifier_settings={} if identifier else None,
        session_store=store,
        **kw,
    )
    FakeTracker.created = []      # трекер по умолчанию из конструктора не считаем
    return p


FRAME = np.full((100, 100, 3), 100, np.uint8)


def payload(i, cam='cam', **kw):
    return {'camera_id': cam, 'frame_id': str(i), 'seq': i, 'pts_ms': i * 100,
            'capture_wall_ts': 1000.0 + i / 10, **kw}


def run_frames(p, n, cam='cam'):
    return [p._handle_frame(FRAME, payload(i, cam)) for i in range(n)]


def test_events_topic_is_subscribed():
    p = make_pipeline()
    assert p._kafka.extra == ['session-events']


def test_result_has_timing_and_label_set():
    p = make_pipeline()
    r = run_frames(p, 6)[-1]
    assert r['schema_version'] == 2
    assert (r['seq'], r['pts_ms'], r['capture_wall_ts']) == (5, 500, 1000.5)
    assert r['label_set'] == LABEL_SET and r['emotion_labels'] == list(EMOTION_LABELS)
    [face] = r['faces']
    assert face['emotion'] in EMOTION_LABELS
    assert len(face['emotion_scores']) == NUM_EMOTIONS
    assert face['face_id'] == 'person-1'


def test_v1_message_still_works():
    p = make_pipeline()
    r = p._handle_frame(FRAME, {'camera_id': 'cam', 'frame_id': '0', 'timestamp': 5.0,
                                'session_config': {'tracker': 'deepsort'}})
    assert r['capture_wall_ts'] == 5.0 and r['pts_ms'] is None
    assert FakeTracker.created == ['deepsort']        # legacy session_config учтён


def test_identification_disabled_globally():
    p = make_pipeline(identifier=False)
    r = run_frames(p, 6)[-1]
    assert r['faces'][0]['face_id'] == pp.ANONYMOUS_FACE_ID
    p._end_session('cam', 'eof')                        # без БД — без ошибок


def test_stream_end_flushes_and_releases_camera():
    p = make_pipeline()
    run_frames(p, 11)
    assert ('cam', 1) in p._cached_faces and 'cam' in p._session_trackers

    p._handle_event({'event': 'stream_end', 'camera_id': 'cam', 'reason': 'eof'})
    assert not p._cached_faces and 'cam' not in p._session_trackers
    [(face_id, ts)] = p._identifier.timeseries
    assert face_id == 'person-1' and ts['label_set'] == LABEL_SET and ts['samples']
    assert all(len(s['scores']) == NUM_EMOTIONS for s in ts['samples'])


def test_stream_end_other_camera_untouched():
    p = make_pipeline()
    run_frames(p, 3, cam='a')
    run_frames(p, 3, cam='b')
    p._handle_event({'event': 'stream_end', 'camera_id': 'a'})
    assert 'b' in p._session_trackers and 'a' not in p._session_trackers


def test_session_config_controls_settings():
    store = SessionConfigStore('unused:1')
    store._apply(b'cam', SessionConfig(camera_id='cam', revision=1, vision=VisionSettings(
        tracker='deepsort', detection_frequency=3, identification=False)).to_msgpack())
    p = make_pipeline(store=store)
    r = run_frames(p, 9)
    assert FakeTracker.created == ['deepsort']
    assert FakeDetector.calls == 3                      # кадры 1, 4, 7
    assert r[-1]['faces'][0]['face_id'] == pp.ANONYMOUS_FACE_ID


def test_tracker_change_mid_session_restarts_tracks():
    store = SessionConfigStore('unused:1')
    p = make_pipeline(store=store)
    run_frames(p, 11)
    store._apply(b'cam', SessionConfig(camera_id='cam', revision=1,
                                       vision=VisionSettings(tracker='deepsort')).to_msgpack())
    run_frames(p, 1)
    assert FakeTracker.created == ['bytetrack', 'deepsort']
    assert len(p._identifier.timeseries) == 1           # старые треки сброшены в БД


def test_unloadable_model_falls_back_to_default():
    store = SessionConfigStore('unused:1')
    store._apply(b'cam', SessionConfig(camera_id='cam', revision=1,
                                       vision=VisionSettings(model='no-such-model')).to_msgpack())
    p = make_pipeline(store=store)
    r = run_frames(p, 6)[-1]
    assert len(r['faces'][0]['emotion_scores']) == NUM_EMOTIONS
    assert p._recognizer_key(VisionSettings(model='no-such-model', device='cpu')) in p._failed_recognizers


def test_stale_frames_are_dropped_in_stage_a():
    import queue
    import threading
    import msgpack

    p = make_pipeline(max_frame_age_ms=500)
    now = time.time()

    class Msg:
        def __init__(self, topic, value):
            self._t, self._v = topic, value

        def topic(self):
            return self._t

        def value(self):
            return self._v

    import cv2
    jpeg = cv2.imencode('.jpg', FRAME)[1].tobytes()
    msgs = [
        Msg('raw', msgpack.packb({**payload(0), 'capture_wall_ts': now - 5, 'frame_data': jpeg})),
        Msg('raw', msgpack.packb({**payload(1), 'capture_wall_ts': now, 'frame_data': jpeg})),
        Msg('session-events', msgpack.packb({'event': 'stream_end', 'camera_id': 'cam'})),
    ]
    stop = threading.Event()

    def consume_message(timeout=0.5):
        if msgs:
            return msgs.pop(0)
        stop.set()
        return None

    p._kafka.consume_message = consume_message
    q = queue.Queue(maxsize=10)
    p._stage_consume_decode(q, stop)
    items = [q.get_nowait() for _ in range(q.qsize())]
    kinds = [i[0] if i else None for i in items]
    assert kinds == ['frame', 'event', None]
    assert items[0][2]['seq'] == 1
    assert p._staleness.dropped == 1
