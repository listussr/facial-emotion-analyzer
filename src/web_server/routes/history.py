from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from ..services.db import get_cursor
from ..services.face_search import face_search

router = APIRouter(prefix="/api/history", tags=["history"])


class UserSummary(BaseModel):
    user_id: str
    first_seen: Optional[str]
    last_seen: Optional[str]
    tracks_count: int
    total_samples: int
    dominant_emotion: Optional[str]


class UsersList(BaseModel):
    users: List[UserSummary]


class SearchMatch(BaseModel):
    user_id: str
    similarity: float
    first_seen: Optional[str]
    last_seen: Optional[str]
    tracks_count: int
    total_samples: int
    dominant_emotion: Optional[str]


class SearchResponse(BaseModel):
    matches: List[SearchMatch]


class TrackHistory(BaseModel):
    track_id: Optional[int] = None
    camera_id: Optional[str] = None
    user_id: Optional[str] = None
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    samples: List[Dict[str, Any]]
    created_at: str


class UserHistory(BaseModel):
    user_id: str
    total_samples: int
    tracks_count: int
    label_counts: Dict[str, int]
    tracks: List[TrackHistory]


class SessionHistory(BaseModel):
    session_id: str
    total_samples: int
    tracks_count: int
    unique_users: int
    label_counts: Dict[str, int]
    tracks: List[TrackHistory]


def _label_counts_for_user(cur, user_id: str) -> Dict[str, int]:
    cur.execute(
        """
        SELECT s->>'label' AS label, COUNT(*) AS cnt
        FROM emotion_timeseries t,
             LATERAL jsonb_array_elements(t.data->'samples') AS s
        WHERE t.user_id = %s
        GROUP BY label
        ORDER BY cnt DESC
        """,
        (user_id,),
    )
    return {r["label"] or "unknown": int(r["cnt"]) for r in cur.fetchall()}


def _label_counts_for_session(cur, session_id: str) -> Dict[str, int]:
    cur.execute(
        """
        SELECT s->>'label' AS label, COUNT(*) AS cnt
        FROM emotion_timeseries t,
             LATERAL jsonb_array_elements(t.data->'samples') AS s
        WHERE t.data->>'camera_id' = %s
        GROUP BY label
        ORDER BY cnt DESC
        """,
        (session_id,),
    )
    return {r["label"] or "unknown": int(r["cnt"]) for r in cur.fetchall()}


def _row_to_track(row: Dict[str, Any]) -> TrackHistory:
    data = row["data"] or {}
    return TrackHistory(
        track_id=data.get("track_id"),
        camera_id=data.get("camera_id"),
        user_id=row.get("user_id"),
        started_at=data.get("started_at"),
        ended_at=data.get("ended_at"),
        samples=data.get("samples") or [],
        created_at=row["created_at"].isoformat(),
    )


@router.post("/search", response_model=SearchResponse)
async def search_by_face(
    file: UploadFile = File(...),
    top_k: int = Query(default=5, ge=1, le=50),
):
    """Поиск top-K похожих лиц по загруженной картинке."""
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=415, detail="Ожидается изображение")
    raw = await file.read()
    try:
        matches = face_search.search(raw, top_k=top_k)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return SearchResponse(matches=[SearchMatch(**m) for m in matches])


@router.get("/users", response_model=UsersList)
def list_users():
    """Сводка по всем лицам, у которых есть хоть одна запись таймсерии."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT
                fe.user_id::text AS user_id,
                MIN(fe.first_seen) AS first_seen,
                MAX(et.created_at) AS last_seen,
                COUNT(et.id) AS tracks_count,
                COALESCE(SUM(jsonb_array_length(et.data->'samples')), 0) AS total_samples
            FROM face_embeddings fe
            LEFT JOIN emotion_timeseries et ON et.user_id = fe.user_id
            GROUP BY fe.user_id
            ORDER BY last_seen DESC NULLS LAST
            """
        )
        rows = cur.fetchall()

        out: List[UserSummary] = []
        for r in rows:
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
            dom = cur.fetchone()
            out.append(
                UserSummary(
                    user_id=r["user_id"],
                    first_seen=r["first_seen"].isoformat() if r["first_seen"] else None,
                    last_seen=r["last_seen"].isoformat() if r["last_seen"] else None,
                    tracks_count=int(r["tracks_count"] or 0),
                    total_samples=int(r["total_samples"] or 0),
                    dominant_emotion=(dom or {}).get("label"),
                )
            )

    return UsersList(users=out)


@router.get("/users/{user_id}", response_model=UserHistory)
def user_history(user_id: str):
    """Полная история эмоций по одному пользователю — все треки."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT user_id::text AS user_id, data, created_at
            FROM emotion_timeseries
            WHERE user_id = %s
            ORDER BY created_at ASC
            """,
            (user_id,),
        )
        rows = cur.fetchall()
        if not rows:
            cur.execute(
                "SELECT 1 FROM face_embeddings WHERE user_id = %s",
                (user_id,),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="User not found")

        tracks = [_row_to_track(r) for r in rows]
        label_counts = _label_counts_for_user(cur, user_id)
        total_samples = sum(label_counts.values())

    return UserHistory(
        user_id=user_id,
        total_samples=total_samples,
        tracks_count=len(tracks),
        label_counts=label_counts,
        tracks=tracks,
    )


@router.get("/users/{user_id}/face")
def user_face(user_id: str):
    """JPEG, сохранённый при первом обнаружении этого лица."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT face_image FROM face_embeddings WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    if row is None or row["face_image"] is None:
        raise HTTPException(status_code=404, detail="Face image not found")
    return Response(
        content=bytes(row["face_image"]),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/sessions/{session_id}", response_model=SessionHistory)
def session_history(session_id: str):
    """Все треки эмоций для конкретной сессии (camera_id или upload_id)."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT user_id::text AS user_id, data, created_at
            FROM emotion_timeseries
            WHERE data->>'camera_id' = %s
            ORDER BY created_at ASC
            """,
            (session_id,),
        )
        rows = cur.fetchall()
        tracks = [_row_to_track(r) for r in rows]
        unique_users = len({r["user_id"] for r in rows})
        label_counts = _label_counts_for_session(cur, session_id)
        total_samples = sum(label_counts.values())

    return SessionHistory(
        session_id=session_id,
        total_samples=total_samples,
        tracks_count=len(tracks),
        unique_users=unique_users,
        label_counts=label_counts,
        tracks=tracks,
    )
