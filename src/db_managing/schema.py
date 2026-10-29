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

READER_ROLE = "affectra_reader"

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
    ("003_session_recordings", """
        CREATE TABLE IF NOT EXISTS session_recordings (
            camera_id TEXT PRIMARY KEY,
            name TEXT,
            kind TEXT,
            status TEXT,
            video_key TEXT,
            analytics_key TEXT,
            frames INTEGER,
            duration_sec REAL,
            size_bytes BIGINT,
            has_audio BOOLEAN,
            truncated BOOLEAN,
            summary JSONB,
            error TEXT,
            started_at TIMESTAMP WITH TIME ZONE,
            finished_at TIMESTAMP WITH TIME ZONE,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_session_recordings_started
            ON session_recordings(started_at DESC);
    """),
    # Только чтение для MCP-сервера агента: роль видит лишь представления схемы reader
    ("004_reader_views", f"""
        CREATE SCHEMA IF NOT EXISTS reader;
        CREATE OR REPLACE VIEW reader.sessions AS
            SELECT camera_id, name, kind, status, started_at, finished_at, duration_sec, frames, has_audio,
                   truncated, video_key, analytics_key, summary
            FROM session_recordings WHERE status IS NOT NULL;
        DO $$
        BEGIN
            CREATE ROLE {READER_ROLE} NOLOGIN;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        ALTER ROLE {READER_ROLE} SET default_transaction_read_only = on;
        GRANT USAGE ON SCHEMA reader TO {READER_ROLE};
        GRANT SELECT ON ALL TABLES IN SCHEMA reader TO {READER_ROLE};
    """),
]

RECORDING_STATUSES: Tuple[str, ...] = ("recording", "rendering", "ready", "failed")
RECORDING_FIELDS: Tuple[str, ...]   = ("name", "kind", "status", "video_key", "analytics_key", "frames",
                                       "duration_sec", "size_bytes", "has_audio", "truncated", "summary",
                                       "error", "started_at", "finished_at")


def upsert_recording_sql(fields: Sequence[str]) -> str:
    """
    Запрос вставки или обновления записи сессии только по переданным полям.

    Args:
        fields (Sequence[str]): Поля из RECORDING_FIELDS.

    Raises:
        ValueError: Неизвестное поле.

    Returns:
        str: SQL с параметрами camera_id и значениями полей по порядку.
    """
    unknown = [f for f in fields if f not in RECORDING_FIELDS]
    if unknown:
        raise ValueError(f"Unknown recording fields: {unknown}")
    columns = ", ".join(("camera_id", *fields, "updated_at"))
    values  = ", ".join(["%s"] * (len(fields) + 1) + ["NOW()"])
    updates = ", ".join([f"{f} = EXCLUDED.{f}" for f in fields] + ["updated_at = NOW()"])
    return f"INSERT INTO session_recordings ({columns}) VALUES ({values}) ON CONFLICT (camera_id) DO UPDATE SET {updates}"


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


def enable_reader_login(conn, password: str) -> None:
    """
    Вход для роли только на чтение (affectra_reader) с заданным паролем.

    Args:
        conn           : Соединение psycopg2 с правом менять роли.
        password (str) : Пароль роли.
    """
    with conn.cursor() as cur:
        cur.execute(f"ALTER ROLE {READER_ROLE} LOGIN PASSWORD %s", (password,))
    conn.commit()


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
