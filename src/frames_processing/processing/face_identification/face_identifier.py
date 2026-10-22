"""Идентификация лиц по эмбеддингам в БД с кэшем и фоновыми запросами."""

import hashlib
import logging
import threading
import time
import uuid
import numpy as np

from concurrent.futures import ThreadPoolExecutor

from ._face_encoder import FaceEncoder
from ....db_managing import FacesDBHandler

_PENDING_PREFIX = "pending-"


class FaceIdentifier(object):
    """
    Идентификация лица сравнением его эмбеддинга с эмбеддингами в БД.

    identify() сразу возвращает pending-id, настоящий UUID приходит из фонового запроса к БД.
    Все запросы — в одном потоке: psycopg2-соединение не делится между потоками.
    """

    def __init__(self, host: str = "localhost", port: int = 5433, database: str = "emotions",
                 user: str = "app_user", password: str = "basic_app_password", threshold: float = 0.6,
                 cache_ttl: int = 30, cleanup_interaval: int = 10):
        self._db_handler = FacesDBHandler(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )
        self._db_handler.connect()
        self._db_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="face-db")

        self._faces_encoder = FaceEncoder()

        self._threshold = threshold

        self._cache_ttl  = cache_ttl
        self._cache      = {}
        self._cache_lock = threading.Lock()

        self._cleanup_interaval = cleanup_interaval
        self._identify_calls    = 0

        logging.info("Successfully initialized FaceIdentifier")

    def _cache_get(self, key: bytes) -> str | None:
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry and entry[1] > time.time():
                return entry[0]
            return None

    def _cache_set(self, key: bytes, face_id: str) -> None:
        with self._cache_lock:
            self._cache[key] = (face_id, time.time() + self._cache_ttl)

    def _cache_update_pending(self, key: bytes, pending_id: str, real_id: str):
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry and entry[0] == pending_id:
                self._cache[key] = (real_id, time.time() + self._cache_ttl)

    def _cleanup(self):
        now = time.time()
        with self._cache_lock:
            expired = [k for k, (_, exp) in self._cache.items() if exp <= now]
            for k in expired:
                del self._cache[k]

    def _create_cache_key(self, embedding: np.ndarray) -> bytes:
        return hashlib.blake2b(
            np.round(embedding, 2).tobytes(),
            digest_size=16,
        ).digest()

    def _resolve_in_background(self, cache_key: bytes, pending_id: str, embedding: np.ndarray, face: np.ndarray, on_resolved=None) -> None:
        """
        Поиск лица в БД или добавление нового, замена pending-id в кэше.

        Args:
            cache_key   (bytes)             : Ключ записи в кэше.
            pending_id  (str)               : Временный идентификатор.
            embedding   (np.ndarray)        : Эмбеддинг лица.
            face        (np.ndarray)        : Изображение лица.
            on_resolved (Callable, optional): Колбэк с настоящим UUID для пайплайна. Defaults to None.
        """
        try:
            face_id = self._db_handler.fetch(embedding=embedding, threshold=self._threshold)

            if face_id is None:
                face_id = self._db_handler.insert(embedding=embedding, image=face)

            self._cache_update_pending(cache_key, pending_id, face_id)

            if on_resolved is not None:
                try:
                    on_resolved(face_id)
                except Exception:
                    logging.exception("on_resolved callback failed")

        except Exception as e:
            logging.error(f"FaceIdentifier: background DB lookup failed: {e}", exc_info=True)

    def resolve(self, face_id: str, face: np.ndarray, appearance: np.ndarray = None) -> str:
        """Настоящий идентификатор вместо pending-id, если он уже есть в кэше."""
        if not face_id.startswith(_PENDING_PREFIX):
            return face_id

        if appearance is not None:
            embedding = np.asarray(appearance, dtype=np.float32)
        else:
            embedding = self._faces_encoder.get_embedding(face).astype(np.float32, copy=False)

        cache_key = self._create_cache_key(embedding)
        cached_id = self._cache_get(cache_key)

        if cached_id is not None and not cached_id.startswith(_PENDING_PREFIX):
            return cached_id

        return face_id

    def identify(self, face: np.ndarray, appearence: np.ndarray = None, on_resolved=None) -> str:
        """
        Идентификация лица; неизвестное лицо добавляется в БД.

        Args:
            face        (np.ndarray)          : Изображение лица.
            appearence  (np.ndarray, optional): Готовый эмбеддинг, например от DeepSort. Defaults to None.
            on_resolved (Callable, optional)  : Колбэк с настоящим UUID. Defaults to None.

        Returns:
            str: UUID из кэша или pending-id до ответа БД.
        """
        self._identify_calls += 1

        if appearence is None:
            embedding = self._faces_encoder.get_embedding(face).astype(np.float32, copy=False)
        else:
            embedding = np.asarray(appearence, dtype=np.float32)

        cache_key = self._create_cache_key(embedding)

        cached_id = self._cache_get(cache_key)

        if cached_id is not None:
            if on_resolved is not None and not cached_id.startswith(_PENDING_PREFIX):
                try:
                    on_resolved(cached_id)
                except Exception:
                    logging.exception("on_resolved callback failed")
            return cached_id

        pending_id = f"{_PENDING_PREFIX}{uuid.uuid4().hex[:8]}"
        self._cache_set(cache_key, pending_id)

        self._db_executor.submit(
            self._resolve_in_background,
            cache_key,
            pending_id,
            embedding,
            face.copy(),
            on_resolved,
        )

        if self._identify_calls % self._cleanup_interaval == 0:
            self._cleanup()
            self._identify_calls = 0

        return pending_id

    def submit_emotion_timeseries(self, face_id: str, payload: dict) -> None:
        """
        Запись таймсерии эмоций в фоне; при pending-id пропускается — нужен UUID для внешнего ключа.

        Args:
            face_id (str) : UUID или pending-id.
            payload (dict): Таймсерия трека.
        """
        if not face_id or face_id.startswith(_PENDING_PREFIX):
            logging.debug(f"Skip emotion timeseries flush: face_id={face_id} not resolved")
            return
        if not payload.get("samples"):
            return
        self._db_executor.submit(self._write_emotions_bg, face_id, payload)

    def _write_emotions_bg(self, face_id: str, payload: dict) -> None:
        try:
            self._db_handler.insert_emotion_timeseries(face_id, payload)
        except Exception:
            logging.exception(f"Failed to write emotion timeseries for {face_id}")

    def close(self):
        self._db_executor.shutdown(wait=True)
        self._db_handler.close()
        self._cache.clear()

    def __del__(self):
        self.close()
