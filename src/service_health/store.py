"""Копия service-health и готовность сервисов, нужных сессии."""

import time

from dataclasses import dataclass
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional

from src.session_config.store import CompactedTopicStore
from .heartbeat import ServiceBeat

SERVICE_TITLES = {
    "ingest":         "приём медиа",
    "consumer":       "анализ лица",
    "audio-consumer": "анализ голоса",
    "fusion":         "слияние лица и голоса",
    "visualizer":     "разметка видео",
    "archiver":       "итоговые записи",
}

REQUIRED_BY_MODALITY = {
    "video": ("ingest", "consumer", "fusion"),
    "audio": ("ingest", "audio-consumer", "fusion"),
}


class ServiceHealthStore(CompactedTopicStore):
    """Сигналы экземпляров сервисов по ключу service@instance."""

    kind = "service heartbeat"

    def _decode(self, value: bytes) -> ServiceBeat:
        return ServiceBeat.from_msgpack(value)

    def _is_newer(self, new: ServiceBeat, old: Optional[ServiceBeat]) -> bool:
        return old is None or new.updated_at >= old.updated_at


@dataclass
class ServiceHealth:
    """Экземпляры сервиса по состояниям; stale — сигнал старше порога (экземпляр упал или недоступен)."""
    ready:    int = 0
    starting: int = 0
    stale:    int = 0


def summarize(beats: Iterable[ServiceBeat], now: Optional[float] = None,
              stale_after_s: float = 20.0) -> Dict[str, ServiceHealth]:
    """
    Состояние сервисов по последним сигналам экземпляров.

    Args:
        beats         (Iterable[ServiceBeat]): Сигналы из service-health.
        now           (float, optional)      : Текущее время. Defaults to None.
        stale_after_s (float, optional)      : Порог устаревания сигнала, с. Defaults to 20.0.

    Returns:
        Dict[str, ServiceHealth]: Сервис -> число экземпляров по состояниям.
    """
    now = time.time() if now is None else now
    out: Dict[str, ServiceHealth] = {}
    for beat in beats:
        health = out.setdefault(beat.service, ServiceHealth())
        if now - beat.updated_at > stale_after_s:
            health.stale += 1
        elif beat.state == "ready":
            health.ready += 1
        else:
            health.starting += 1
    return out


def not_ready_reasons(health: Dict[str, ServiceHealth], modalities: Iterable[str]) -> List[str]:
    """
    Причины, по которым сессию с такими модальностями запускать рано.

    Args:
        health     (Dict[str, ServiceHealth]): Результат summarize.
        modalities (Iterable[str])           : Модальности сессии.

    Returns:
        List[str]: Пустой список — все нужные сервисы готовы.
    """
    required: List[str] = []
    for modality in modalities:
        required += [s for s in REQUIRED_BY_MODALITY.get(modality, ()) if s not in required]
    reasons = []
    for service in required:
        h     = health.get(service, ServiceHealth())
        title = SERVICE_TITLES.get(service, service)
        if h.ready:
            continue
        if h.starting:
            reasons.append(f"{title}: сервис загружает модели")
        else:
            reasons.append(f"{title}: сервис не запущен")
    return reasons
