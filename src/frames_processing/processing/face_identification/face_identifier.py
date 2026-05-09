import time
import uuid
import threading
import logging
import numpy as np
import hashlib
from concurrent.futures import ThreadPoolExecutor

from ._face_encoder import FaceEncoder
from ....db_managing import FacesDBHandler

_PENDING_PREFIX = "pending-"

class FaceIdentifier(object):
    def __init__(self, host: str = 'localhost', port: int = 5433, database: str = "emotions", 
                 user: str = "app_user", password: str = "basic_app_password", threshold: float = 0.6, 
                 cache_ttl: int = 30, cleanup_interaval: int = 10):
        """
        #### Идентифицирует лицо путём сравления эмбеддингов лица и эмбеддингов в базе данных.
        
        :param host: Хост для подключения к базе данных.
        :type host: str
        :param port: Порт для подкючения к базе данных.
        :type port: int
        :param database: Название базы данных.
        :type database: str
        :param user: Имя пользователя в базе данных.
        :type user: str
        :param password: Пароль от базы данных.
        :type password: str
        :param threshold: Порог идентификации лица.
        :type threshold: float
        :param cache_ttl: Время жизни кэша с лицами (в секундах).
        :type cache_ttl: int
        :param cleanup_interaval: Интервал очистки кэша (в количестве вызовов метода `identify`).
        :type cleanup_interaval: int
        """
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

        self._cache_ttl = cache_ttl
        self._cache = {}
        self._cache_lock = threading.Lock()

        self._cleanup_interaval = cleanup_interaval
        self._identify_calls = 0

        logging.info("Successfully initialized FaceIdentifier")

    def _cache_get(self, key: bytes) -> str | None:
        """
        Получение идентификатора из кэша первого уровня.
        ---

        Args:
            key (bytes): Эмбеддингни лица.

        Returns:
            str | None: Идентификатор лица при наличии в кэше.
        """
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry and entry[1] > time.time():
                return entry[0]
            return None

    def _cache_set(self, key: bytes, face_id: str) -> None:
        """
        Добавление идентификатора в кэш.
        ---

        Args:
            key (bytes): Эмбеддинги лица.
            face_id (str): Идентификатор лица.
        """
        with self._cache_lock:
            self._cache[key] = (face_id, time.time() + self._cache_ttl)

    def _cache_update_pending(self, key: bytes, pending_id: str, real_id: str):
        """
        Заменяет pending-ID на реальный.
        ---
        """
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry and entry[0] == pending_id:
                self._cache[key] = (real_id, time.time() + self._cache_ttl)

    def _cleanup(self):
        """
        Очистка кэша с истёкшим сроком жизни.
        ---
        """
        now = time.time()
        with self._cache_lock:
            expired = [k for k, (_, exp) in self._cache.items() if exp <= now]
            for k in expired:
                del self._cache[k]

    def _create_cache_key(self, embedding: np.ndarray) -> bytes:
        """
        Создание ключа для кэша из эмбеддингов.
        ---

        Args:
            embedding (np.ndarray): Эмбеддинги лица.

        Returns:
            bytes: Ключ для кэша идентификаторов.
        """
        return hashlib.blake2b(
            np.round(embedding, 2).tobytes(),
            digest_size=16,
        ).digest()

    def _resolve_in_background(self, cache_key: bytes, pending_id: str, embedding: np.ndarray, face: np.ndarray, on_resolved=None) -> None:
        """
        Фоновые обращения к базе данных для асинхронной работы сервиса.
        ---

        Args:
            cache_key (bytes): Ключ щаписи в кэше.
            pending_id (str): Временный идентификатор.
            embedding (np.ndarray): Эмбеддинги лица.
            face (np.ndarray): Изображение лица.
            on_resolved (Callable[[str], None], optional): Колбэк с настоящим
                user_id из БД. Нужен, чтобы внешний код (пайплайн) узнал
                реальный UUID — внутренний кэш FaceIdentifier'а сам по себе
                до пайплайна не доходит.
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
        """
        Проверка на временный face_id.
        ---
        """
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
        #### Идентификация по базе данных.

        В случае отсутствия похожего лица в базе - добавление в базу и возврат нового ID.

        :param face: Изображение с идентифицируемым лицом.
        :type face: np.ndarray
        :param appearence: Эмбеддинги от DeepSort или иного энкодера (опционально).
        :type appearence: np.ndarray
        :param on_resolved: Опциональный колбэк, который будет вызван с настоящим
            user_id, когда фоновый запрос к БД отработает. Возвращаемое значение
            функции — pending-id, который можно показывать сразу.
        :type on_resolved: Callable[[str], None]
        :return: ID пользователя из базы данных (pending-... до резолва).
        :rtype: str
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
        Запланировать запись таймсерии эмоций в БД. Выполняется в том же
        однопоточном executor, что и идентификация — psycopg2-соединение
        не делится между потоками.

        Если face_id ещё не разрешён (pending-...), запись пропускается:
        пользователь UUID нужен как FK на face_embeddings.

        :param face_id: Идентификатор лица (UUID или pending-).
        :type face_id: str
        :param payload: Тело JSONB-записи (см. FacesDBHandler.insert_emotion_timeseries).
        :type payload: dict
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
        """
        #### Метод для безопасного отключения от базы данных.
        """
        self._db_executor.shutdown(wait=True)
        self._db_handler.close()
        self._cache.clear()

    def __del__(self):
        """
        #### Деструктор для безопасного отключения от базы данных.
        """
        self.close()
