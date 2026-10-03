import msgpack

from src.video_producing.contracts import SCHEMA_VERSION, AudioMessage, VideoMessage

ENV = dict(camera_id='cam', seq=3, pts_ms=300, modalities=('video', 'audio'),
           capture_wall_ts=100.3, session_start_wall_ts=100.0)


def make_video(**kw):
    return VideoMessage(
        **ENV, frame_id='3', timestamp=100.3, frame_data=b'\xff\xd8jpeg', quality=80,
        frame_rate=10, processed_width=640, processed_height=480,
        original_width=1280, original_height=960, **kw,
    )


def test_video_roundtrip_and_flat_v1_keys():
    msg = make_video()
    raw = msg.to_msgpack()
    data = msgpack.unpackb(raw, raw=False)

    # ключи, которые читают консьюмеры schema v1
    for key in ('camera_id', 'frame_id', 'frame_data', 'timestamp'):
        assert key in data
    # настройки обработки не передаются внутри медиапотока
    assert 'session_config' not in data
    assert data['schema_version'] == SCHEMA_VERSION
    assert data['stream'] == 'video'
    assert isinstance(data['frame_data'], bytes)
    assert VideoMessage.from_msgpack(raw) == msg


def test_unknown_fields_ignored():
    data = {**make_video().to_dict(), 'future_field': 42}
    assert VideoMessage.from_dict(data) == make_video()


def test_audio_duration_is_derived():
    msg = AudioMessage(**ENV, sample_rate=16000, channels=1, num_samples=1600,
                       audio_data=b'\0' * 3200, duration_ms=999)
    assert msg.stream == 'audio'
    assert msg.duration_ms == 100
    assert AudioMessage.from_msgpack(msg.to_msgpack()) == msg


def test_session_event_roundtrip():
    from src.video_producing.contracts import SessionEvent
    ev = SessionEvent(
        camera_id='cam', event='stream_end', wall_ts=110.0, session_start_wall_ts=100.0,
        requested_modalities=('video', 'audio'), modalities=('audio',), degraded=True,
        topics={'audio': 'raw-audio-chunks'}, audio={'sample_rate': 16000},
        reason='eof', last_pts_ms={'video': None, 'audio': 9900}, sent={'video': 0, 'audio': 100},
    )
    data = msgpack.unpackb(ev.to_msgpack(), raw=False)
    assert data['modalities'] == ['audio'] and data['requested_modalities'] == ['video', 'audio']
    assert data['schema_version'] == SCHEMA_VERSION
    assert SessionEvent.from_msgpack(ev.to_msgpack()) == ev
