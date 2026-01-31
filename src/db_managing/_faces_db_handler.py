from typing import Union
import uuid
import numpy as np
import logging
from PIL import Image
import io

from ._abstract_handler import AbstractDBHandler

class FacesDBHandler(AbstractDBHandler):
    def insert(self, embedding: np.ndarray, image: np.ndarray) -> str:
        """
        Добавление лица в таблицу с лицами.
        
        :param embedding: Эмбеддинги лица.
        :type embedding: np.ndarray
        :param image: Массив с пикселями изображения.
        :type image: np.ndarray
        :return: Идентификатор лица.
        :rtype: str
        """
        try:
            query = """
                INSERT INTO face_embeddings (user_id, embedding, face_image)
                VALUES (%s, %s, %s)
                RETURNING id, user_id
            """
            user_id = str(uuid.uuid4())
            embedding_list = embedding.tolist()

            if image.dtype != 'uint8':
                image = image.astype('uint8')

            image_pil = Image.fromarray(image, 'RGB')
            buffer = io.BytesIO()
            image_pil.save(buffer, format='JPEG')
            
            self._cursor.execute(query, (user_id, embedding_list, buffer.getvalue()))
            result = self._cursor.fetchone()
            self._conn.commit()
            logging.info("Succesfully inserted into faces table in database")
            return result['user_id']
        except Exception as e:
            logging.error("Error in insertion into faces table in Postgres")
            raise

    def fetch(self, embedding: np.ndarray, threshold: float = 0.6) -> Union[str, None]:
        """
        Получение лица с наиболее похожими эмбеддингами.
        
        :param embedding: Эмбеддинги лица.
        :type embedding: np.ndarray
        :param threshold: Пороговое значение схожести лиц.
        :type threshold: float
        :return: Идентификатор лица из базы.
        :rtype: str | None
        """
        try:
            query = """
                SELECT user_id, embedding <=> (%s)::vector AS distance
                FROM face_embeddings
                ORDER BY distance ASC
            """          
            self._cursor.execute(query, (embedding.tolist(), ))
            result = self._cursor.fetchone()

            if result:
                similarity = 1 - result['distance']

                if similarity >= threshold:
                    logging.info(f"Fetched user from faces database. Similarity = {similarity}")
                    return result['user_id']
                else:
                    logging.info(f"No match found above threshold. Best similarity = {similarity}")
                    return None
            
            else:
                logging.info("No faces in database.")
                return None
        except Exception as e:
            logging.error(f"Error during fetching from faces database: {e}")
            raise
