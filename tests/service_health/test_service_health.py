"""Сигналы живости сервисов и готовность к запуску сессий."""

import time

from src.service_health import Heartbeat
from src.service_health import ServiceBeat
from src.service_health import ServiceHealth
from src.service_health import not_ready_reasons
from src.service_health import summarize


class FakePublisher:
    def __init__(self):
        self.sent   = []
        self.closed = False

    def send_async(self, key, value):
        self.sent.append((key, ServiceBeat.from_msgpack(value)))

    def send(self, key, value, timeout=5.0):
        self.sent.append((key, None if value is None else ServiceBeat.from_msgpack(value)))

    def close(self, timeout=5.0):
        self.closed = True


def test_heartbeat_lifecycle(tmp_path):
    pub  = FakePublisher()
    file = tmp_path / "ready"
    hb   = Heartbeat("k:9092", "audio-consumer", interval_s=0.05, health_file=file, publisher=pub,
                     instance="node-1").start()
    time.sleep(0.12)
    assert pub.sent and all(b.state == "starting" for _, b in pub.sent) and not file.exists()
    hb.ready(model="crema-wavlm-int8")
    key, beat = pub.sent[-1]
    assert key == "audio-consumer@node-1" and beat.state == "ready" and beat.info == {"model": "crema-wavlm-int8"}
    assert file.exists()
    hb.stop()
    assert pub.sent[-1] == ("audio-consumer@node-1", None) and pub.closed and not file.exists()


def test_heartbeat_survives_kafka_errors():
    class Broken(FakePublisher):
        def send_async(self, key, value):
            raise RuntimeError("kafka down")

    hb = Heartbeat("k:9092", "consumer", publisher=Broken())
    hb.ready()
    assert hb.beat.state == "ready"


def test_summarize_and_reasons():
    now   = 1000.0
    beats = [
        ServiceBeat(service="consumer", instance="a", state="ready", updated_at=now - 1),
        ServiceBeat(service="consumer", instance="b", state="ready", updated_at=now - 100),
        ServiceBeat(service="audio-consumer", instance="a", state="starting", updated_at=now - 2),
        ServiceBeat(service="fusion", instance="a", state="ready", updated_at=now),
        ServiceBeat(service="ingest", instance="a", state="ready", updated_at=now),
    ]
    health = summarize(beats, now=now)
    assert health["consumer"] == ServiceHealth(ready=1, starting=0, stale=1)
    assert health["audio-consumer"] == ServiceHealth(ready=0, starting=1, stale=0)
    assert not_ready_reasons(health, ["video"]) == []
    assert not_ready_reasons(health, ["video", "audio"]) == ["анализ голоса: сервис загружает модели"]
    del health["fusion"]
    assert not_ready_reasons(health, ["audio"]) == ["анализ голоса: сервис загружает модели",
                                                    "слияние лица и голоса: сервис не запущен"]
