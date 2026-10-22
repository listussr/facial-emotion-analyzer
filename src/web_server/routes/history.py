"""История эмоций: пользователи, сессии, голос сессий, поиск по фото и выгрузка CSV."""

import csv
import io

from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional

from fastapi import APIRouter
from fastapi import File
from fastapi import HTTPException
from fastapi import Query
from fastapi import UploadFile
from fastapi.responses import Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.db_managing import csv_label_columns
from src.db_managing import label_set_of
from src.db_managing import labels_for
from src.db_managing import scores_by_label
from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import LABEL_SET
from ..services.db import get_cursor
from ..services.face_search import face_search

router = APIRouter(prefix="/api/history", tags=["history"])

Modality         = Literal["face", "fused"]
DEFAULT_MODALITY: Modality = "face"


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
    modality: str = "face"
    label_set: str = ""
    labels: List[str] = []
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    samples: List[Dict[str, Any]]
    created_at: str


class UserHistory(BaseModel):
    user_id: str
    modality: str
    available_modalities: List[str]
    total_samples: int
    tracks_count: int
    label_counts: Dict[str, int]
    tracks: List[TrackHistory]


class SessionHistory(BaseModel):
    session_id: str
    modality: str
    available_modalities: List[str]
    total_samples: int
    tracks_count: int
    unique_users: int
    label_counts: Dict[str, int]
    tracks: List[TrackHistory]


class VoiceHistory(BaseModel):
    session_id: str
    label_set: str
    labels: List[str]
    total_windows: int
    label_counts: Dict[str, int]
    windows: List[Dict[str, Any]]


def _label_counts(cur, where: str, params: tuple) -> Dict[str, int]:
    cur.execute(
        f"""
        SELECT s->>'label' AS label, COUNT(*) AS cnt
        FROM emotion_timeseries t,
             LATERAL jsonb_array_elements(t.data->'samples') AS s
        WHERE {where}
        GROUP BY label
        ORDER BY cnt DESC
        """,
        params,
    )
    return {r["label"] or "unknown": int(r["cnt"]) for r in cur.fetchall()}


def _modalities_for(cur, where: str, param: str) -> List[str]:
    cur.execute(f"SELECT DISTINCT modality FROM emotion_timeseries t WHERE {where} ORDER BY modality", (param,))
    return [r["modality"] for r in cur.fetchall()]


def _row_to_track(row: Dict[str, Any]) -> TrackHistory:
    data = row["data"] or {}
    return TrackHistory(
        track_id=data.get("track_id"),
        camera_id=data.get("camera_id"),
        user_id=row.get("user_id"),
        modality=row.get("modality") or data.get("modality") or "face",
        label_set=row.get("label_set") or label_set_of(data),
        labels=list(labels_for(data)),
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
    """Сводка по всем лицам по эмоциям лица; face и fused сразу посчитали бы каждый момент дважды."""
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
            LEFT JOIN emotion_timeseries et
                   ON et.user_id = fe.user_id AND et.modality = 'face'
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
                WHERE t.user_id = %s AND t.modality = 'face'
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
def user_history(user_id: str, modality: Modality = Query(default=DEFAULT_MODALITY)):
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT user_id::text AS user_id, modality, label_set, data, created_at
            FROM emotion_timeseries
            WHERE user_id = %s AND modality = %s
            ORDER BY created_at ASC
            """,
            (user_id, modality),
        )
        rows = cur.fetchall()
        if not rows:
            cur.execute(
                "SELECT 1 FROM face_embeddings WHERE user_id = %s",
                (user_id,),
            )
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="User not found")

        tracks        = [_row_to_track(r) for r in rows]
        label_counts  = _label_counts(cur, "t.user_id = %s AND t.modality = %s", (user_id, modality))
        total_samples = sum(label_counts.values())
        available     = _modalities_for(cur, "t.user_id = %s", user_id)

    return UserHistory(
        user_id=user_id,
        modality=modality,
        available_modalities=available,
        total_samples=total_samples,
        tracks_count=len(tracks),
        label_counts=label_counts,
        tracks=tracks,
    )


@router.get("/users/{user_id}/face")
def user_face(user_id: str):
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
def session_history(session_id: str, modality: Modality = Query(default=DEFAULT_MODALITY)):
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT user_id::text AS user_id, modality, label_set, data, created_at
            FROM emotion_timeseries
            WHERE data->>'camera_id' = %s AND modality = %s
            ORDER BY created_at ASC
            """,
            (session_id, modality),
        )
        rows         = cur.fetchall()
        tracks       = [_row_to_track(r) for r in rows]
        unique_users = len({r["user_id"] for r in rows})
        label_counts = _label_counts(cur, "t.data->>'camera_id' = %s AND t.modality = %s",
                                     (session_id, modality))
        total_samples = sum(label_counts.values())
        available     = _modalities_for(cur, "t.data->>'camera_id' = %s", session_id)

    return SessionHistory(
        session_id=session_id,
        modality=modality,
        available_modalities=available,
        total_samples=total_samples,
        tracks_count=len(tracks),
        unique_users=unique_users,
        label_counts=label_counts,
        tracks=tracks,
    )


@router.get("/sessions/{session_id}/voice", response_model=VoiceHistory)
def session_voice(session_id: str):
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT data FROM session_audio_timeseries
            WHERE camera_id = %s ORDER BY created_at ASC
            """,
            (session_id,),
        )
        rows = cur.fetchall()
    windows: List[Dict[str, Any]] = []
    for r in rows:
        windows.extend((r["data"] or {}).get("windows") or [])
    counts: Dict[str, int] = {}
    for w in windows:
        label         = w.get("smoothed_label") or w.get("label") or "unknown"
        counts[label] = counts.get(label, 0) + 1
    first = (rows[0]["data"] if rows else {}) or {}
    return VoiceHistory(
        session_id=session_id,
        label_set=first.get("label_set") or LABEL_SET,
        labels=list(first.get("labels") or EMOTION_LABELS),
        total_windows=len(windows),
        label_counts=dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        windows=windows,
    )


def _csv_iter_for_tracks(rows: List[Dict[str, Any]]):
    """CSV по отсчётам; колонки вероятностей — классы всех записей, пусто там, где модель класса не выдавала."""
    buf = io.StringIO()
    w   = csv.writer(buf, lineterminator="\n")

    label_columns = csv_label_columns([labels_for(r.get("data") or {}) for r in rows])
    header = [
        "user_id", "camera_id", "track_id", "modality", "label_set",
        "created_at", "sample_ts", "sample_iso",
        "label", "top_score",
        *label_columns,
    ]
    w.writerow(header)
    yield buf.getvalue()
    buf.seek(0); buf.truncate(0)

    for r in rows:
        data        = r.get("data") or {}
        labels      = labels_for(data)
        created_at  = r.get("created_at")
        created_iso = created_at.isoformat() if created_at else ""
        user_id     = r.get("user_id") or ""
        camera_id   = data.get("camera_id") or ""
        track_id    = data.get("track_id")
        modality    = r.get("modality") or data.get("modality") or "face"
        label_set   = r.get("label_set") or label_set_of(data)
        for s in (data.get("samples") or []):
            scores    = s.get("scores") or []
            by_label  = scores_by_label(labels, scores)
            top_score = max(scores) if scores else ""
            ts        = s.get("t")
            try:
                iso_ts = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
            except Exception:
                iso_ts = ""
            row = [
                user_id, camera_id, track_id, modality, label_set,
                created_iso, ts, iso_ts,
                s.get("label") or "", top_score,
                *[round(by_label[c], 4) if c in by_label else "" for c in label_columns],
            ]
            w.writerow(row)
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)


def _export_rows(where: str, key: str, modality: Optional[str]) -> List[Dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT user_id::text AS user_id, modality, label_set, data, created_at
            FROM emotion_timeseries
            WHERE {where} AND (%s::text IS NULL OR modality = %s)
            ORDER BY created_at ASC
            """,
            (key, modality, modality),
        )
        return cur.fetchall()


def _csv_response(rows: List[Dict[str, Any]], filename: str) -> StreamingResponse:
    return StreamingResponse(
        _csv_iter_for_tracks(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/users/{user_id}/export.csv")
def export_user_csv(user_id: str, modality: Optional[Modality] = Query(default=None)):
    rows = _export_rows("user_id = %s", user_id, modality)
    return _csv_response(rows, f"affectra_user_{user_id[:8]}{'_' + modality if modality else ''}.csv")


@router.get("/sessions/{session_id}/export.csv")
def export_session_csv(session_id: str, modality: Optional[Modality] = Query(default=None)):
    rows = _export_rows("data->>'camera_id' = %s", session_id, modality)
    return _csv_response(rows, f"affectra_session_{session_id}{'_' + modality if modality else ''}.csv")
