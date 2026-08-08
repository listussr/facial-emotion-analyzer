from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from src.frames_processing.processing.face_detection import FaceDetector
from src.frames_processing.processing.face_identification._face_encoder import FaceEncoder

from .db import get_cursor


_log = logging.getLogger("face_search")


class FaceSearch:
    """Лениво инициализирует тяжёлые модели — детектор и энкодер."""

    def __init__(self) -> None:
        self._detector: Optional[FaceDetector] = None
        self._encoder: Optional[FaceEncoder] = None

    def _ensure_loaded(self) -> None:
        if self._detector is None:
            _log.info("Loading FaceDetector for search service")
            self._detector = FaceDetector(min_detection_confidence=0.5)
        if self._encoder is None:
            _log.info("Loading FaceEncoder for search service")
            self._encoder = FaceEncoder(warmup=True)

    def _decode_image(self, raw: bytes) -> np.ndarray:
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Не удалось декодировать изображение (поддерживаются JPEG/PNG).")
        return img  # BGR — пайплайн тоже хранит/ищет в BGR-эмбеддингах

    def _largest_face_crop(self, img: np.ndarray) -> np.ndarray:
        assert self._detector is not None
        detections = self._detector.detect(img, scale=1.0)
        if not detections:
            raise ValueError("На изображении не найдено лиц.")

        x, y, w, h, _ = max(detections, key=lambda d: d[2] * d[3])
        h_img, w_img = img.shape[:2]
        x1, y1 = max(0, int(x)), max(0, int(y))
        x2, y2 = min(w_img, int(x + w)), min(h_img, int(y + h))
        crop = img[y1:y2, x1:x2]
        if crop.size == 0:
            raise ValueError("Лицо обрезано вырожденно — попробуйте другую картинку.")
        return crop

    def search(self, raw_bytes: bytes, top_k: int) -> List[Dict[str, Any]]:
        """
        Возвращает топ-K похожих пользователей с агрегированными метриками.
        Список отсортирован по убыванию косинусной близости.
        """
        self._ensure_loaded()
        assert self._encoder is not None

        img = self._decode_image(raw_bytes)
        crop = self._largest_face_crop(img)

        crop_resized = cv2.resize(crop, (160, 160), interpolation=cv2.INTER_AREA)
        embedding = self._encoder.get_embedding(crop_resized.astype(np.float32))

        with get_cursor() as cur:
            cur.execute(
                """
                WITH search_results AS (
                    SELECT user_id, embedding <=> (%s)::vector AS distance
                    FROM face_embeddings
                    ORDER BY distance ASC
                    LIMIT %s
                )
                SELECT
                    sr.user_id::text AS user_id,
                    1 - sr.distance AS similarity,
                    fe.first_seen,
                    MAX(et.created_at) AS last_seen,
                    COUNT(et.id) AS tracks_count,
                    COALESCE(SUM(jsonb_array_length(et.data->'samples')), 0) AS total_samples
                FROM search_results sr
                JOIN face_embeddings fe ON fe.user_id = sr.user_id
                LEFT JOIN emotion_timeseries et ON et.user_id = sr.user_id
                GROUP BY sr.user_id, sr.distance, fe.first_seen
                ORDER BY sr.distance ASC
                """,
                (embedding.tolist(), int(top_k)),
            )
            rows = cur.fetchall()

        results: List[Dict[str, Any]] = []
        for r in rows:
            dom = None
            with get_cursor() as cur:
                cur.execute(
                    """
                    SELECT s->>'label' AS label
                    FROM emotion_timeseries t,
                         LATERAL jsonb_array_elements(t.data->'samples') AS s
                    WHERE t.user_id = %s
                    GROUP BY label
                    ORDER BY COUNT(*) DESC
                    LIMIT 1
                    """,
                    (r["user_id"],),
                )
                row = cur.fetchone()
                if row:
                    dom = row["label"]

            results.append(
                {
                    "user_id": r["user_id"],
                    "similarity": float(r["similarity"]),
                    "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
                    "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
                    "tracks_count": int(r["tracks_count"] or 0),
                    "total_samples": int(r["total_samples"] or 0),
                    "dominant_emotion": dom,
                }
            )
        return results


face_search = FaceSearch()
