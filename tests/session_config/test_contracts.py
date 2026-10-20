import time

import msgpack
import pytest

from src.session_config.contracts import (
    SCHEMA_VERSION, AudioSettings, FusionSettings, SessionConfig, VisionSettings, next_revision,
)


def full_config(**kw) -> SessionConfig:
    return SessionConfig(
        camera_id='cam_01', revision=10, updated_at=1000.5,
        vision=VisionSettings(tracker='deepsort', model='resnet-50', identification=False),
        audio=AudioSettings(vad_threshold=0.6, window_ms=1500),
        fusion=FusionSettings(face_weight=0.7, voice_weight=0.3),
        **kw,
    )


def test_roundtrip_msgpack():
    """Конфиг со всеми секциями → to_msgpack → from_msgpack == исходный."""
    cfg = full_config()
    assert SessionConfig.from_msgpack(cfg.to_msgpack()) == cfg

    minimal = SessionConfig(camera_id='c', revision=0)
    restored = SessionConfig.from_msgpack(minimal.to_msgpack())
    assert restored == minimal
    assert restored.vision is None and restored.audio is None and restored.fusion is None


def test_unknown_fields_and_sections_ignored():
    """Лишние поля верхнего уровня, лишняя секция и лишние поля внутри секции не мешают разбору."""
    data = full_config().to_dict()
    data['future_field'] = 42
    data['video2'] = {'x': 1}
    data['vision']['future_vision_field'] = 'x'

    cfg = SessionConfig.from_dict(data)
    assert cfg == full_config()


def test_missing_required_fields_raise():
    """Нет camera_id или revision → ValueError; не словарь → ValueError."""
    with pytest.raises(ValueError):
        SessionConfig.from_dict({'revision': 1})
    with pytest.raises(ValueError):
        SessionConfig.from_dict({'camera_id': 'c'})
    with pytest.raises(ValueError):
        SessionConfig.from_msgpack(msgpack.packb([1, 2, 3]))
    with pytest.raises(Exception):
        SessionConfig.from_msgpack(b'\xc1 not msgpack')


def test_section_to_dict_skips_none():
    """В to_dict секции попадают только заданные поля; незаданные секции не пишутся."""
    assert VisionSettings(model='convnext').to_dict() == {'model': 'convnext'}
    assert VisionSettings(identification=False).to_dict() == {'identification': False}
    assert AudioSettings().to_dict() == {}

    data = SessionConfig(camera_id='c', revision=0, audio=AudioSettings(hop_ms=500)).to_dict()
    assert data['revision'] == 0                 # «ложное» значение не теряется
    assert data['audio'] == {'hop_ms': 500}
    assert 'vision' not in data and 'fusion' not in data
    assert data['schema_version'] == SCHEMA_VERSION


def test_section_from_dict_edge_cases():
    """None, пустой словарь и не-словарь → None (секция не задана)."""
    assert VisionSettings.from_dict(None) is None
    assert VisionSettings.from_dict({}) is None
    assert VisionSettings.from_dict('garbage') is None
    assert VisionSettings.from_dict({'tracker': 'x', 'junk': 1}) == VisionSettings(tracker='x')


def test_merged_over_defaults():
    """Заданные поля секции перекрывают defaults, None — оставляют значение по умолчанию."""
    defaults = VisionSettings(tracker='bytetrack', model='resnet-18', device='cpu', identification=True)
    session = VisionSettings(model='resnet-50', identification=False)

    merged = session.merged_over(defaults)
    assert merged == VisionSettings(tracker='bytetrack', model='resnet-50', device='cpu', identification=False)
    assert defaults.model == 'resnet-18'     # исходник не изменён
    assert VisionSettings().merged_over(defaults) == defaults


def test_updated_rejects_unknown_field():
    """updated(unknown=1) → ValueError; updated(tracker='x') — копия с изменением, исходник не тронут."""
    v = VisionSettings(tracker='bytetrack')
    with pytest.raises(ValueError):
        v.updated(trakcer='deepsort')

    changed = v.updated(tracker='deepsort', model='convnext')
    assert changed == VisionSettings(tracker='deepsort', model='convnext')
    assert v.tracker == 'bytetrack'
    assert changed.updated(model=None).model is None    # unset


def test_section_access():
    """section(name) — данные секции; неизвестное имя → KeyError."""
    cfg = full_config()
    assert cfg.section('vision') is cfg.vision
    assert SessionConfig(camera_id='c', revision=1).section('audio') is None
    with pytest.raises(KeyError):
        cfg.section('video')


def test_with_section():
    """Копия с новой секцией, ревизией и updated_at; исходник и ClassVar не меняются."""
    cfg = full_config()
    before = time.time()
    new = cfg.with_section('audio', AudioSettings(hop_ms=250), revision=11)

    assert new.audio == AudioSettings(hop_ms=250)
    assert new.vision == cfg.vision and new.fusion == cfg.fusion
    assert new.revision == 11
    assert isinstance(new.updated_at, float) and new.updated_at >= before
    assert cfg.audio == AudioSettings(vad_threshold=0.6, window_ms=1500)
    assert SessionConfig._sections['audio'] is AudioSettings

    assert cfg.with_section('vision', None, revision=12).vision is None
    with pytest.raises(KeyError):
        cfg.with_section('video', None, revision=12)
    with pytest.raises(TypeError):
        cfg.with_section('audio', VisionSettings(), revision=12)


def test_is_newer_than():
    """None → True; больше ревизия → True; равная или меньше → False."""
    old = SessionConfig(camera_id='c', revision=1)
    new = SessionConfig(camera_id='c', revision=2)
    assert old.is_newer_than(None)
    assert new.is_newer_than(old)
    assert not old.is_newer_than(new)
    assert not old.is_newer_than(SessionConfig(camera_id='c', revision=1))


def test_next_revision_is_monotonic():
    """next_revision(cfg) > cfg.revision, в том числе когда revision «из будущего»."""
    first = next_revision(None)
    assert first > 0
    cfg = SessionConfig(camera_id='c', revision=first)
    assert next_revision(cfg) > first

    future = SessionConfig(camera_id='c', revision=10 ** 20)
    assert next_revision(future) == 10 ** 20 + 1
