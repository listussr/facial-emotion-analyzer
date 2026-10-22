"""SessionConfigPublisher с подменённым confluent_kafka.Producer."""

import msgpack
import pytest
import src.session_config.publisher as publisher_module

from src.session_config import PublishError
from src.session_config import SessionConfig
from src.session_config import SessionConfigPublisher
from src.session_config import VisionSettings


class FakeProducer:
    """Запоминает conf и отправленное; поведение доставки настраивается флагами класса."""
    flush_remaining = 0
    delivery_error  = None

    def __init__(self, conf):
        self.conf         = conf
        self.sent         = []
        self._pending     = []
        self.closed_flush = None

    def produce(self, topic, value=None, key=None, on_delivery=None, **kw):
        self.sent.append({"topic": topic, "key": key, "value": value})
        self._pending.append(on_delivery)

    def poll(self, timeout=None):
        return 0

    def flush(self, timeout=None):
        if FakeProducer.flush_remaining:
            return FakeProducer.flush_remaining
        for cb in self._pending:
            if cb is not None:
                cb(FakeProducer.delivery_error, None)
        self._pending.clear()
        return 0


@pytest.fixture(autouse=True)
def fake_kafka(monkeypatch):
    FakeProducer.flush_remaining = 0
    FakeProducer.delivery_error  = None
    monkeypatch.setattr(publisher_module, "Producer", FakeProducer)


def test_producer_conf_is_reliable():
    pub  = SessionConfigPublisher("broker:9092")
    conf = pub._producer.conf if hasattr(pub, "_producer") else pub.producer.conf
    assert conf["bootstrap.servers"] == "broker:9092"
    assert conf["acks"] == "all"
    assert conf["enable.idempotence"] is True
    assert conf["partitioner"] == "murmur2_random"


def _sent(pub):
    producer = pub._producer if hasattr(pub, "_producer") else pub.producer
    return producer.sent


def test_publish_writes_keyed_msgpack():
    pub = SessionConfigPublisher("b", topic="cfg-topic")
    cfg = SessionConfig(camera_id="cam_01", revision=5, vision=VisionSettings(model="convnext"))
    pub.publish(cfg)

    [msg] = _sent(pub)
    assert msg["topic"] == "cfg-topic"
    assert msg["key"] in (b"cam_01", "cam_01")
    assert SessionConfig.from_dict(msgpack.unpackb(msg["value"], raw=False)) == cfg


def test_delete_sends_tombstone():
    pub = SessionConfigPublisher("b")
    pub.delete("cam_01")
    [msg] = _sent(pub)
    assert msg["key"] in (b"cam_01", "cam_01")
    assert msg["value"] is None


def test_not_confirmed_in_time_raises():
    FakeProducer.flush_remaining = 1
    pub                          = SessionConfigPublisher("b")
    with pytest.raises(PublishError):
        pub.publish(SessionConfig(camera_id="c", revision=1), timeout=0.1)


def test_delivery_error_raises():
    FakeProducer.delivery_error = RuntimeError("broker said no")
    pub                         = SessionConfigPublisher("b")
    with pytest.raises(PublishError):
        pub.delete("c")


def test_context_manager_closes():
    with SessionConfigPublisher("b") as pub:
        pub.publish(SessionConfig(camera_id="c", revision=1))
    assert len(_sent(pub)) == 1
