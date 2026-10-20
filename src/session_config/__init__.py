"""
Канал управления сессиями: конфиг обработки каждой сессии в compacted-топике
`session-config` (ключ — camera_id).

Пакет не зависит от продюсера и консьюмеров: его импортируют все сервисы,
а он — никого.
"""
from .contracts import (
    SCHEMA_VERSION, AudioSettings, FusionSettings, SessionConfig, VisionSettings,
    next_revision,
)
from .publisher import PublishError, SessionConfigPublisher
from .store import SessionConfigStore, StoreNotReadyError, StoreStats
from .topic import DEFAULT_TOPIC, TopicSettings, check_topic, ensure_topic

__all__ = [
    'SCHEMA_VERSION', 'SessionConfig', 'VisionSettings', 'AudioSettings', 'FusionSettings',
    'next_revision',
    'SessionConfigPublisher', 'PublishError',
    'SessionConfigStore', 'StoreNotReadyError', 'StoreStats',
    'DEFAULT_TOPIC', 'TopicSettings', 'ensure_topic', 'check_topic',
]
