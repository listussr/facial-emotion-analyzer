import sys
import time
import wave
from pathlib import Path

import msgpack
import numpy as np
import pytest

pytest.importorskip('av')

import src.video_producing.producer as producer_module
from src.video_producing import CameraConfig, CameraProducer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from make_sync_test import make  # noqa: E402


class FakeProducer:
    """Подмена confluent_kafka.Producer: запоминает conf и всё отправленное."""

    def __init__(self, conf):
        self.conf = conf
        self.sent = []

    def produce(self, topic, value, key=None, partition=None, timestamp=None, headers=None, callback=None):
        self.sent.append({
            'topic': topic, 'key': key, 'partition': partition, 'timestamp': timestamp,
            'headers': dict(headers or []), 'value': msgpack.unpackb(value, raw=False),
        })

    def poll(self, timeout=None):
        return 0

    def flush(self, timeout=None):
        return 0


@pytest.fixture(autouse=True)
def fake_kafka(monkeypatch):
    monkeypatch.setattr(producer_module, 'Producer', FakeProducer)


@pytest.fixture(scope='module')
def av_file(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp('media') / 'sync.mp4'
    make(path, seconds=2, fps=30, width=160, height=120, audio_rate=48000)
    return path


@pytest.fixture(scope='module')
def wav_file(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp('media') / 'tone.wav'
    x = (np.sin(np.arange(16000) / 5) * 8000).astype(np.int16)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(x.tobytes())
    return path


def run(source, **kw) -> CameraProducer:
    cfg = CameraConfig(camera_id='cam', source=str(source), kafka_servers='x',
                       frame_rate=10, stop_on_end=True, realtime_pacing=False, **kw)
    p = CameraProducer(cfg)
    p.start()
    p.thread.join(20)
    assert not p.thread.is_alive()
    return p


def media(p):
    return p.producer.sent


def events(p):
    return [m['value'] for m in p.events_producer.sent]


def test_two_producers_with_different_guarantees():
    p = CameraProducer(CameraConfig(camera_id='cam', source='x.mp4', kafka_servers='x'))
    media_conf, events_conf = p.producer.conf, p.events_producer.conf
    assert media_conf['acks'] == '1' and media_conf['enable.idempotence'] is False
    assert media_conf['message.timeout.ms'] == 2000
    assert events_conf['acks'] == 'all' and events_conf['enable.idempotence'] is True
    assert media_conf['partitioner'] == events_conf['partitioner'] == 'murmur2_random'
    assert media_conf['reconnect.backoff.max.ms'] == events_conf['reconnect.backoff.max.ms'] == 1000


def test_start_and_end_events_wrap_media(av_file):
    p = run(av_file, modalities=('video', 'audio'))
    evs = events(p)
    assert [e['event'] for e in evs] == ['stream_start', 'stream_end']

    start, end = evs
    assert start['modalities'] == ['video', 'audio'] and not start['degraded']
    assert start['topics'] == {'video': 'raw-video-frames', 'audio': 'raw-audio-chunks'}
    assert start['video']['frame_rate'] == 10 and start['audio']['sample_rate'] == 16000

    assert end['reason'] == 'eof'
    assert end['sent'] == {'video': p.frame_count, 'audio': p.audio_chunks_sent}
    assert end['last_pts_ms'] == {'video': p.last_video_pts_ms, 'audio': p.last_audio_pts_ms}
    assert p.end_reason == 'eof'


def test_media_messages_keyed_by_camera_without_explicit_partition(av_file):
    p = run(av_file, modalities=('video', 'audio'))
    sent = media(p) + p.events_producer.sent
    assert all(m['key'] == b'cam' and m['partition'] is None for m in sent)

    video = [m for m in media(p) if m['topic'] == 'raw-video-frames']
    audio = [m for m in media(p) if m['topic'] == 'raw-audio-chunks']
    assert video and audio
    assert {m['headers']['stream'] for m in video} == {b'video'}
    assert {m['headers']['stream'] for m in audio} == {b'audio'}
    assert all(m['headers']['schema_version'] == b'2' for m in video + audio)
    # время сообщения в Kafka — время захвата
    assert all(m['timestamp'] == int(m['value']['capture_wall_ts'] * 1000) for m in video + audio)


def test_explicit_partition_is_used(av_file):
    p = run(av_file, modalities=('audio',), partition=3)
    assert {m['partition'] for m in media(p) + p.events_producer.sent} == {3}


def test_degraded_session_reports_in_events(wav_file):
    p = run(wav_file, modalities=('video', 'audio'))
    start = events(p)[0]
    assert start['degraded'] is True
    assert start['requested_modalities'] == ['video', 'audio']
    assert start['modalities'] == ['audio'] and start['video'] is None


def test_strict_modalities_does_not_start(wav_file):
    p = run(wav_file, modalities=('video', 'audio'), strict_modalities=True)
    assert events(p) == [] and media(p) == []
    assert p.end_reason == 'error' and not p.stream_started


def test_stop_reason_is_stopped(av_file):
    cfg = CameraConfig(camera_id='cam', source=str(av_file), kafka_servers='x',
                       modalities=('video', 'audio'), realtime_pacing=True)
    p = CameraProducer(cfg)
    p.start()
    time.sleep(0.5)
    p.stop()
    assert [e['event'] for e in events(p)] == ['stream_start', 'stream_end']
    assert events(p)[-1]['reason'] == 'stopped'


def test_local_queue_overflow_drops_media_without_gaps_in_seq(av_file, monkeypatch):
    calls = {'n': 0}
    original = FakeProducer.produce

    def flaky(self, topic, value, **kw):
        if topic == 'raw-video-frames':
            calls['n'] += 1
            if calls['n'] % 3 == 0:
                raise BufferError
        return original(self, topic, value, **kw)

    monkeypatch.setattr(FakeProducer, 'produce', flaky)
    p = run(av_file, modalities=('video',))
    video = [m['value'] for m in media(p)]
    assert p.dropped_local['video'] > 0
    assert [m['seq'] for m in video] == list(range(len(video)))
    assert p.get_stats()['kafka']['dropped_local']['video'] == p.dropped_local['video']
