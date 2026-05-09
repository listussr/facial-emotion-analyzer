from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool

from ..config import settings

_pool: SimpleConnectionPool | None = None
_log = logging.getLogger("web_server.db")


def init_pool(minconn: int = 1, maxconn: int = 6) -> None:
    """Создаём пул при старте FastAPI (вызывается из lifespan)."""
    global _pool
    if _pool is not None:
        return
    _pool = SimpleConnectionPool(
        minconn,
        maxconn,
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        database=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
    )
    _log.info(f"DB pool ready ({settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME})")


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        _log.info("DB pool closed")


@contextmanager
def get_cursor() -> Iterator[RealDictCursor]:
    """Контекст-менеджер: даёт RealDictCursor и возвращает соединение в пул."""
    if _pool is None:
        raise RuntimeError("DB pool not initialised; init_pool() not called")
    conn = _pool.getconn()
    try:
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                yield cur
    finally:
        _pool.putconn(conn)
