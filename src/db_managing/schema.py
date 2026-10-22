"""Схема БД эмоций и идемпотентные миграции, применяемые при подключении сервисов."""

import logging

from typing import Any
from typing import Dict
from typing import List
from typing import Sequence
from typing import Tuple

from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import LABEL_SET

MODALITIES: Tuple[str, ...] = ("face", "fused")

LEGACY_LABEL_SET = "legacy-8"
LEGACY_LABELS: Tuple[str, ...] = (
    "anger", "contempt", "disgust", "fear", "happy", "neutral", "sad", "surprise",
)

MIGRATIONS: List[Tuple[str, str]] = [
    ("001_timeseries_modality", f"""
        ALTER TABLE emotion_timeseries
            ADD COLUMN IF NOT EXISTS modality TEXT NOT NULL DEFAULT 'face';
        ALTER TABLE emotion_timeseries
            ADD COLUMN IF NOT EXISTS label_set TEXT NOT NULL DEFAULT '{LEGACY_LABEL_SET}';
        CREATE INDEX IF NOT EXISTS idx_emotion_user_modality
            ON emotion_timeseries(user_id, modality);
        CREATE INDEX IF NOT EXISTS idx_emotion_camera
            ON emotion_timeseries((data->>'camera_id'));
    """),
    ("002_session_audio_timeseries", """
        CREATE TABLE IF NOT EXISTS session_audio_timeseries (
            id SERIAL PRIMARY KEY,
            camera_id TEXT NOT NULL,
            label_set TEXT NOT NULL,
            data JSONB NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_session_audio_camera
            ON session_audio_timeseries(camera_id);
    """),
]


def ensure_schema(conn) -> None:
    """
    Применение всех миграций в одной транзакции.

    Args:
        conn: Соединение psycopg2.
    """
    with conn.cursor() as cur:
        for name, sql in MIGRATIONS:
            cur.execute(sql)
    conn.commit()
    logging.getLogger("db.schema").info(f"DB schema up to date ({len(MIGRATIONS)} migrations)")


def labels_for(data: Dict[str, Any]) -> Tuple[str, ...]:
    """
    Классы, к которым относятся scores записи; у старых записей — 8 классов прежних моделей.

    Args:
        data (Dict[str, Any]): Запись таймсерии.

    Returns:
        Tuple[str, ...]: Имена классов.
    """
    labels = data.get("labels")
    if isinstance(labels, (list, tuple)) and labels:
        return tuple(labels)
    return LEGACY_LABELS


def label_set_of(data: Dict[str, Any]) -> str:
    return data.get("label_set") or (LABEL_SET if data.get("labels") else LEGACY_LABEL_SET)


def scores_by_label(labels: Sequence[str], scores: Sequence[float]) -> Dict[str, float]:
    return {name: float(v) for name, v in zip(labels, scores or [])}


def csv_label_columns(label_sets_seen: Sequence[Sequence[str]]) -> List[str]:
    """
    Колонки CSV: текущие классы, затем классы старых записей, которых нет в текущих.

    Args:
        label_sets_seen (Sequence[Sequence[str]]): Наборы классов выгружаемых записей.

    Returns:
        List[str]: Имена колонок.
    """
    columns = list(EMOTION_LABELS)
    for labels in label_sets_seen:
        for name in labels:
            if name not in columns:
                columns.append(name)
    return columns
