import time
import uuid
from ._face_encoder import FaceEncoder
from ....db_managing import FacesDBHandler

import logging
import numpy as np
import hashlib

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

        self._faces_encoder = FaceEncoder()

        self._threashold = threshold

        self._cache_ttl = cache_ttl
        self._cache = {}

        self._cleanup_interaval = cleanup_interaval
        self._identify_calls = 0

        logging.info("Successfully initialized FaceIdentifier")

    def _cleanup(self):
        """
        #### Очистка кэша с истёкшим сроком жизни.
        """
        now = time.time()
        expired = [k for k, (_, exp) in self._cache.items() if exp <= now]
        for k in expired:
            del self._cache[k]

    def identify(self, face: np.ndarray, appearence: np.ndarray = None) -> str:
        """
        #### Идентификация по базе данных.

        В случае отсутствия похожего лица в базе - добавление в базу и возврат нового ID.

        Пока в случае идентификации с помощью сторонних эмбеддингов в БД ничего не идёт, 
        <br>а присваивается рандомный `uuid4`

        Исправление проблемы остаётся в `TODO`
        
        :param face: Изображение с идентифицируемым лицом.
        :type face: np.ndarray
        :param face: Набор эмбеддингов от `DeepSort` или иного энкодера.
        :type face: np.ndarray
        :return: ID пользователя из базы данных.
        :rtype: str
        """
        self._identify_calls += 1
        now = time.time()

        embedding = appearence
        is_deepsort = appearence is not None

        if embedding is None:
            embedding = self._faces_encoder.get_embedding(face)
            embedding = embedding.astype(np.float32, copy=False)
        
        embedding = np.round(embedding, 3)
        cache_key = hashlib.blake2b(
            embedding.tobytes(),
            digest_size=16,
        ).digest()

        cached = self._cache.get(cache_key)

        if cached and cached[1] > now:
            return cached[0]

        face_id = self._db_handler.fetch(
            embedding=embedding, 
            threshold=self._threashold
        )

        if face_id == None:
            """
            TODO - убрать костыль из идентификации
            нужно придумать, как можно оптимизировать идентификацию на хосте без кардинального изменения БД и мусорных стоолбцов
            """
            if is_deepsort:
                face_id = f"track-{uuid.uuid4()}"
            else:
                face_id = self._db_handler.insert(
                    embedding=embedding,
                    image=face
                )

        self._cache[cache_key] = (face_id, now + self._cache_ttl)

        if self._identify_calls % self._cleanup_interaval == 0:
            self._cleanup()
            self._identify_calls = 0

        return face_id

    def close(self):
        """
        #### Метод для безопасного отключения от базы данных.
        """
        self._db_handler.close()
        self._cache.clear()

    def __del__(self):
        """
        #### Деструктор для безопасного отключения от базы данных.
        """
        self.close()
