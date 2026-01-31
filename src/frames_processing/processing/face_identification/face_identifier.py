from ._face_encoder import FaceEncoder
from ....db_managing import FacesDBHandler

import logging
import numpy as np

class FaceIdentifier(object):
    def __init__(self, host: str = 'localhost', port: int = 5433, database: str = "emotions", 
                 user: str = "app_user", password: str = "basic_app_password", threshold: float = 0.6):
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

        logging.info("Successfully initialized FaceIdentifier")

    def identify(self, face: np.ndarray) -> str:
        """
        #### Идентификация по базе данных.

        В случае отсутствия похожего лица в базе - добавление в базу и возврат нового ID.
        
        :param face: Изображение с идентифицируемым лицом.
        :type face: np.ndarray
        :return: ID пользователя из базы данных.
        :rtype: str
        """
        embeddings = self._faces_encoder.get_embedding(face)
        face_id = self._db_handler.fetch(embedding=embeddings, threshold=self._threashold)

        if face_id == None:
            face_id = self._db_handler.insert(embedding=embeddings, image=face)
        
        return face_id


    def __del__(self):
        """
        #### Деструктор для безопасного отключения от базы данных.
        """
        self._db_handler.close()
