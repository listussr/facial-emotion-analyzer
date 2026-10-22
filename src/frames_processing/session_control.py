"""Настройки видеопайплайна по сессиям: секция vision конфига поверх значений по умолчанию."""

import logging
import threading

from dataclasses import replace
from typing import Dict
from typing import Optional
from typing import Set

from src.session_config import SessionConfig
from src.session_config import SessionConfigStore
from src.session_config import VisionSettings

KNOWN_TRACKERS     = ("bytetrack", "deepsort")
CLIP_FRAMES        = (8, 16)
MIN_EMOTION_HOP_MS = 200

_log = logging.getLogger("pipeline.session")


class SessionControl:
    """
    Итоговые настройки камер.

    Без конфига работают значения по умолчанию, у старых сообщений — поле session_config.
    Колбэк хранилища приходит из его потока, а settings() вызывает стадия B, поэтому состояние под блокировкой.
    """

    def __init__(self, defaults: VisionSettings, store: Optional[SessionConfigStore] = None,
                 first_frame_wait_s: float = 0.3):
        missing = [k for k, v in vars(defaults).items() if v is None]
        if missing:
            raise ValueError(f"vision defaults must be fully specified, missing: {missing}")
        self.defaults           = defaults
        self.store              = store
        self.first_frame_wait_s = first_frame_wait_s

        self._lock = threading.Lock()
        self._resolved: Dict[str, VisionSettings] = {}
        self._pending_reset: Set[str] = set()
        self._frame_index: Dict[str, int] = {}

    def on_config_change(self, camera_id: str, old: Optional[SessionConfig],
                         new: Optional[SessionConfig]) -> None:
        """Сброс вычисленных настроек камеры; при смене трекера камера помечается на его пересоздание."""
        with self._lock:
            prev = self._resolved.pop(camera_id, None)
        if prev is None:
            return
        now = self._resolve(camera_id, new, None)
        with self._lock:
            if now.tracker != prev.tracker:
                self._pending_reset.add(camera_id)
        _log.info(f"[{camera_id}] session config changed: {prev} -> {now}")

    def settings(self, camera_id: str, legacy: Optional[Dict] = None) -> VisionSettings:
        """
        Итоговые настройки камеры; на первом кадре ненадолго ждёт конфиг.

        Args:
            camera_id (str)           : Идентификатор камеры.
            legacy    (Dict, optional): Поле session_config сообщения v1. Defaults to None.

        Returns:
            VisionSettings: Настройки, закэшированные до изменения конфига.
        """
        with self._lock:
            cached = self._resolved.get(camera_id)
        if cached is not None:
            return cached

        cfg = None
        if self.store is not None:
            cfg = self.store.get(camera_id)
            if cfg is None and self.first_frame_wait_s > 0:
                cfg = self.store.wait_for(camera_id, self.first_frame_wait_s)
        resolved = self._resolve(camera_id, cfg, legacy)
        with self._lock:
            self._resolved[camera_id] = resolved
        source = "session-config" if cfg is not None else ("legacy" if legacy else "defaults")
        _log.info(f"[{camera_id}] vision settings from {source}: {resolved}")
        return resolved

    def take_reset(self, camera_id: str) -> bool:
        with self._lock:
            if camera_id in self._pending_reset:
                self._pending_reset.discard(camera_id)
                return True
            return False

    def next_frame_index(self, camera_id: str) -> int:
        with self._lock:
            n                            = self._frame_index.get(camera_id, 0) + 1
            self._frame_index[camera_id] = n
            return n

    def forget(self, camera_id: str) -> None:
        with self._lock:
            self._resolved.pop(camera_id, None)
            self._pending_reset.discard(camera_id)
            self._frame_index.pop(camera_id, None)

    def _resolve(self, camera_id: str, cfg: Optional[SessionConfig],
                 legacy: Optional[Dict]) -> VisionSettings:
        if cfg is not None and cfg.vision is not None:
            session = cfg.vision
        elif cfg is None and legacy:
            session = VisionSettings(
                tracker=legacy.get("tracker"), model=legacy.get("model"), device=legacy.get("device"),
            )
        else:
            session = VisionSettings()
        merged = session.merged_over(self.defaults)
        return self._validated(camera_id, merged)

    def _validated(self, camera_id: str, s: VisionSettings) -> VisionSettings:
        fixes = {}
        if s.tracker not in KNOWN_TRACKERS:
            fixes["tracker"] = self.defaults.tracker
        if s.device not in ("cpu", "cuda"):
            fixes["device"] = self.defaults.device
        for name, minimum in (("detection_frequency", 1), ("emotion_hop_ms", MIN_EMOTION_HOP_MS)):
            value = getattr(s, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                fixes[name] = getattr(self.defaults, name)
        if s.clip_frames not in CLIP_FRAMES:
            fixes["clip_frames"] = self.defaults.clip_frames
        if not isinstance(s.identification, bool):
            fixes["identification"] = self.defaults.identification
        if fixes:
            _log.warning(f"[{camera_id}] invalid session settings replaced by defaults: "
                         f"{ {k: getattr(s, k) for k in fixes} }")
            s = replace(s, **fixes)
        return s
