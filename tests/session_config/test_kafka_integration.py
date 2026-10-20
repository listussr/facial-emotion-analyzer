"""
Интеграционные тесты с настоящим Kafka (localhost:9092).

Каждый тест работает со своим уникальным топиком, чтобы не зависеть от
состояния брокера. Без брокера — пропускаются.
"""
import threading
import uuid

import pytest

from src.session_config import (
    SessionConfig, SessionConfigPublisher, SessionConfigStore, TopicSettings, VisionSettings,
    check_topic, ensure_topic,
)

BOOTSTRAP = 'localhost:9092'


def _broker_available() -> bool:
    try:
        from confluent_kafka.admin import AdminClient
        AdminClient({'bootstrap.servers': BOOTSTRAP, 'socket.timeout.ms': 2000}).list_topics(timeout=3)
        return True
    except Exception:
        return False


@pytest.fixture(scope='module')
def kafka():
    if not _broker_available():
        pytest.skip(f'Kafka is not available at {BOOTSTRAP}')
    return BOOTSTRAP


def _topic_names(kafka):
    from confluent_kafka.admin import AdminClient
    return set(AdminClient({'bootstrap.servers': kafka}).list_topics(timeout=5).topics)


def _delete_topics(kafka, names):
    from confluent_kafka.admin import AdminClient
    names = [n for n in names if n in _topic_names(kafka)]
    if names:
        admin = AdminClient({'bootstrap.servers': kafka})   # ссылка нужна, пока ждём futures
        for f in admin.delete_topics(names).values():
            f.result(timeout=10)


@pytest.fixture
def topic(kafka):
    """Уникальный топик с настройками session-config; удаляется после теста."""
    settings = TopicSettings(name=f'session-config-test-{uuid.uuid4().hex[:8]}')
    ensure_topic(kafka, settings)
    yield settings
    _delete_topics(kafka, [settings.name])


def cfg(camera_id: str, revision: int, model: str = 'm') -> SessionConfig:
    return SessionConfig(camera_id=camera_id, revision=revision, updated_at=1000.0,
                         vision=VisionSettings(model=model))


def test_ensure_topic_is_idempotent_and_compacted(kafka):
    """Первый вызов → True, второй → False; cleanup.policy=compact, check_topic пуст."""
    settings = TopicSettings(name=f'session-config-test-{uuid.uuid4().hex[:8]}')
    try:
        assert ensure_topic(kafka, settings) is True
        assert ensure_topic(kafka, settings) is False
        assert check_topic(kafka, settings) == []
    finally:
        _delete_topics(kafka, [settings.name])


def test_check_topic_reports_mismatch(kafka, topic):
    expected = TopicSettings(name=topic.name, segment_ms=topic.segment_ms + 1)
    diffs = check_topic(kafka, expected)
    assert any('segment.ms' in d for d in diffs)


def test_check_topic_missing_raises_and_does_not_create(kafka):
    """Проверка отсутствующего топика не создаёт его (auto.create.topics на брокере)."""
    name = f'missing-{uuid.uuid4().hex[:8]}'
    with pytest.raises(RuntimeError):
        check_topic(kafka, TopicSettings(name=name))
    assert name not in _topic_names(kafka)


def test_store_without_topic_does_not_create_it(kafka):
    """Читатель, запущенный раньше init-topic, ждёт топик, а не создаёт его с политикой delete."""
    from src.session_config import StoreNotReadyError
    name = f'missing-{uuid.uuid4().hex[:8]}'
    store = SessionConfigStore(kafka, topic=name)
    try:
        with pytest.raises(StoreNotReadyError):
            store.start(ready_timeout=2.0)
    finally:
        store.stop()
    assert name not in _topic_names(kafka)


def test_publish_then_store_sees(kafka, topic):
    """publish → запущенный store видит конфиг (через wait_for)."""
    with SessionConfigStore(kafka, topic=topic.name) as store, \
            SessionConfigPublisher(kafka, topic=topic.name) as pub:
        assert store.ready
        pub.publish(cfg('cam', 1))
        assert store.wait_for('cam', timeout=10) == cfg('cam', 1)


def test_restarted_store_restores_snapshot(kafka, topic):
    """Несколько publish → новый store после start() сразу содержит все последние ревизии."""
    with SessionConfigPublisher(kafka, topic=topic.name) as pub:
        pub.publish(cfg('a', 1))
        pub.publish(cfg('a', 2, model='latest'))
        pub.publish(cfg('b', 1))
        pub.publish(cfg('c', 1))
        pub.delete('c')

    with SessionConfigStore(kafka, topic=topic.name) as store:
        assert store.snapshot() == {'a': cfg('a', 2, model='latest'), 'b': cfg('b', 1)}


def test_tombstone_removes(kafka, topic):
    """publish → delete → store.get() is None, on_change вызван с new=None."""
    removed = threading.Event()

    def on_change(cam, old, new):
        if new is None:
            removed.set()

    with SessionConfigStore(kafka, topic=topic.name, on_change=on_change) as store, \
            SessionConfigPublisher(kafka, topic=topic.name) as pub:
        pub.publish(cfg('cam', 1))
        assert store.wait_for('cam', timeout=10)
        pub.delete('cam')
        assert removed.wait(10)
        assert store.get('cam') is None


def test_two_stores_see_same_configs(kafka, topic):
    """Два store одновременно видят одинаковый snapshot — разделения на группы нет."""
    with SessionConfigPublisher(kafka, topic=topic.name) as pub:
        for i in range(5):
            pub.publish(cfg(f'cam{i}', 1))

    with SessionConfigStore(kafka, topic=topic.name) as s1, \
            SessionConfigStore(kafka, topic=topic.name) as s2:
        assert len(s1.snapshot()) == 5
        assert s1.snapshot() == s2.snapshot()


def test_publish_before_store_start(kafka, topic):
    """publish до запуска store → конфиг доступен сразу после start(), без wait_for."""
    with SessionConfigPublisher(kafka, topic=topic.name) as pub:
        pub.publish(cfg('early', 7))
    with SessionConfigStore(kafka, topic=topic.name) as store:
        assert store.get('early') == cfg('early', 7)


def test_empty_topic_becomes_ready(kafka, topic):
    """Пустой топик не мешает готовности."""
    with SessionConfigStore(kafka, topic=topic.name) as store:
        assert store.ready and store.snapshot() == {}
