"""Подготовка разбора (фаза 1): ряд, обзор, моменты детекторов — основа и для отчёта без модели."""

import uuid

from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import Optional

from src.emotion_moments import EmotionSeries
from src.emotion_moments import Profile
from src.emotion_moments import detect_moments
from src.emotion_moments import session_overview
from .findings import RunData


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def prepare_run(series_doc: Dict[str, Any], profile: Profile, session_id: Optional[str] = None,
                meta: Optional[Dict[str, Any]] = None, privacy: str = "local", top_k: int = 10) -> RunData:
    """
    Разбор без модели: моменты детекторов и обзор сессии.

    Args:
        series_doc (Dict[str, Any])          : Документ series.json.
        profile    (Profile)                 : Профиль сценария.
        session_id (str, optional)           : Id сессии; по умолчанию camera_id ряда. Defaults to None.
        meta       (Dict[str, Any], optional): Метаданные сессии (имя, время). Defaults to None.
        privacy    (str, optional)           : Профиль приватности. Defaults to "local".
        top_k      (int, optional)           : Сколько моментов разбирать подробно. Defaults to 10.

    Returns:
        RunData: Данные разбора без вердиктов модели.
    """
    series = EmotionSeries.from_document(series_doc)
    return RunData(
        run_id=new_run_id(), session_id=session_id or series.camera_id,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), series=series, profile=profile,
        overview=session_overview(series), moments=detect_moments(series, profile), privacy=privacy, meta=meta,
        top_k=top_k,
    )
