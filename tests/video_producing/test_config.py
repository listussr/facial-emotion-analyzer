import pytest

from src.video_producing.config import CameraConfig

BASE = dict(camera_id='cam', source='x.mp4', kafka_servers='localhost:9092', partition=0)


def test_defaults_are_video_only():
    c = CameraConfig(**BASE)
    assert c.modalities == ('video',)
    assert c.has_video and not c.has_audio
    assert c.video_topic == 'raw-video-frames'
    assert c.audio_samples_per_chunk == 1600


def test_legacy_topic_name_alias():
    c = CameraConfig(**BASE, topic_name='legacy-topic')
    assert c.video_topic == 'legacy-topic'
    assert c.topic_name == 'legacy-topic'

    c = CameraConfig(**BASE, video_topic='new-topic')
    assert c.topic_name == 'new-topic'


def test_modalities_list_is_normalized():
    c = CameraConfig(**BASE, modalities=['video', 'audio'])
    assert c.modalities == ('video', 'audio')
    assert c.has_audio


@pytest.mark.parametrize('bad', [
    dict(modalities=()),
    dict(modalities=('image',)),
    dict(modalities=('audio', 'audio')),
    dict(frame_rate=0),
    dict(audio_channels=3),
    dict(audio_sample_rate=0),
    dict(audio_chunk_ms=5),
    dict(audio_chunk_ms=33, audio_sample_rate=44100),
])
def test_invalid_config_raises(bad):
    with pytest.raises(ValueError):
        CameraConfig(**BASE, **bad)


def test_partition_by_key_by_default():
    c = CameraConfig(camera_id='cam', source='x.mp4', kafka_servers='k')
    assert c.partition is None
    assert c.effective_partition is None


def test_explicit_partition_and_legacy_modulo():
    assert CameraConfig(**BASE).effective_partition == 0
    assert CameraConfig(**{**BASE, 'partition': 7}).effective_partition == 7
    assert CameraConfig(**{**BASE, 'partition': 7}, total_partitions=5).effective_partition == 2


def test_new_defaults():
    c = CameraConfig(**BASE)
    assert c.strict_modalities is False
    assert c.events_topic == 'session-events'
    assert c.kafka_media_acks == '1'


@pytest.mark.parametrize('bad', [
    dict(partition=-1),
    dict(total_partitions=0),
    dict(kafka_media_acks='2'),
    dict(kafka_media_timeout_ms=5, kafka_media_linger_ms=5),
])
def test_invalid_kafka_config_raises(bad):
    with pytest.raises(ValueError):
        CameraConfig(**{**BASE, **bad})
