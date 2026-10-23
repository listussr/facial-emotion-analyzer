"""Локальная копия статусов сессий из compacted-топика session-status."""

from typing import Optional

from src.session_config.store import CompactedTopicStore
from .contracts import SessionStatus


class SessionStatusStore(CompactedTopicStore):
    """Статусы сессий по camera_id; более старый статус не заменяет более новый."""

    kind = "session status"

    def _decode(self, value: bytes) -> SessionStatus:
        return SessionStatus.from_msgpack(value)

    def _is_newer(self, new: SessionStatus, old: Optional[SessionStatus]) -> bool:
        return old is None or new.updated_at >= old.updated_at
