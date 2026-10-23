"""Работа с таблицами лиц, таймсерий эмоций и голоса сессий."""

import io
import json
import logging
import uuid
import numpy as np

from typing import Any
from typing import Dict
from typing import Union

from PIL import Image

from ._abstract_handler import AbstractDBHandler
from .schema import MODALITIES
from .schema import ensure_schema
from .schema import label_set_of
from .schema import upsert_recording_sql


class FacesDBHandler(AbstractDBHandler):
    def insert(self, embedding: np.ndarray, image: np.ndarray) -> str:
        """
        Добавление лица в таблицу лиц.

        Args:
            embedding (np.ndarray): Эмбеддинг лица.
            image     (np.ndarray): Изображение лица RGB.

        Returns:
            str: Идентификатор пользователя.
        """
        try:
            query = """
                INSERT INTO face_embeddings (user_id, embedding, face_image)
                VALUES (%s, %s, %s)
                RETURNING id, user_id
            """
            user_id        = str(uuid.uuid4())
            embedding_list = embedding.tolist()

            if image.dtype != "uint8":
                image = image.astype("uint8")

            image_pil = Image.fromarray(image, "RGB")
            buffer    = io.BytesIO()
            image_pil.save(buffer, format="JPEG")

            self._cursor.execute(query, (user_id, embedding_list, buffer.getvalue()))
            result = self._cursor.fetchone()
            self._conn.commit()
            logging.info("Succesfully inserted into faces table in database")
            return result["user_id"]
        except Exception as e:
            logging.error("Error in insertion into faces table in Postgres")
            raise

    def fetch(self, embedding: np.ndarray, threshold: float = 0.6) -> Union[str, None]:
        """
        Поиск пользователя с самым похожим эмбеддингом.

        Args:
            embedding (np.ndarray)     : Эмбеддинг лица.
            threshold (float, optional): Минимальная косинусная схожесть. Defaults to 0.6.

        Returns:
            Union[str, None]: Идентификатор пользователя или None.
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
                similarity = 1 - result["distance"]

                if similarity >= threshold:
                    logging.info(f"Fetched user from faces database. Similarity = {similarity}")
                    return result["user_id"]
                else:
                    logging.info(f"No match found above threshold. Best similarity = {similarity}")
                    return None

            else:
                logging.info("No faces in database.")
                return None
        except Exception as e:
            logging.error(f"Error during fetching from faces database: {e}")
            raise

    def connect(self):
        """Подключение и приведение схемы к текущей версии."""
        super().connect()
        ensure_schema(self._conn)

    def insert_emotion_timeseries(self, user_id: str, payload: Dict[str, Any]) -> None:
        """
        Запись таймсерии эмоций трека пользователя.

        Args:
            user_id (str)           : UUID пользователя.
            payload (Dict[str, Any]): Таймсерия: camera_id, track_id, modality, label_set, labels, samples.

        Raises:
            ValueError: Неизвестная модальность.
        """
        modality = payload.get("modality") or "face"
        if modality not in MODALITIES:
            raise ValueError(f"unknown modality {modality!r}, expected {MODALITIES}")
        self._execute_insert(
            """
            INSERT INTO emotion_timeseries (user_id, modality, label_set, data)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, modality, label_set_of(payload), json.dumps(payload, ensure_ascii=False)),
            f"{modality} timeseries for user {user_id}: {len(payload.get('samples', []))} samples",
        )

    def insert_session_audio(self, camera_id: str, payload: Dict[str, Any]) -> None:
        """
        Запись голосовой таймсерии сессии без привязки к человеку.

        Args:
            camera_id (str)           : Идентификатор сессии.
            payload   (Dict[str, Any]): Таймсерия: label_set, labels, windows.
        """
        self._execute_insert(
            """
            INSERT INTO session_audio_timeseries (camera_id, label_set, data)
            VALUES (%s, %s, %s)
            """,
            (camera_id, label_set_of(payload), json.dumps(payload, ensure_ascii=False)),
            f"voice timeseries for session {camera_id}: {len(payload.get('windows', []))} windows",
        )

    def upsert_recording(self, camera_id: str, **fields: Any) -> None:
        """
        Вставка или обновление записи сессии; не переданные поля не меняются.

        Args:
            camera_id (str): Идентификатор сессии.
            fields    (Any): Поля session_recordings; summary сериализуется в JSON.
        """
        if "summary" in fields and fields["summary"] is not None:
            fields["summary"] = json.dumps(fields["summary"], ensure_ascii=False)
        names = list(fields)
        self._execute_insert(upsert_recording_sql(names), (camera_id, *[fields[n] for n in names]),
                             f"recording {camera_id}: {fields.get('status')}")

    def _execute_insert(self, query: str, params: tuple, what: str) -> None:
        try:
            self._cursor.execute(query, params)
            self._conn.commit()
            logging.info(f"Inserted {what}")
        except Exception:
            logging.exception(f"Error inserting {what}")
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise
