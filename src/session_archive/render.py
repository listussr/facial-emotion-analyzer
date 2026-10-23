"""Рендер итоговой записи сессии: mp4 с рамками слитых эмоций и звуком, JSON с таймлайном и сводкой."""

import bisect
import logging
import av
import cv2
import numpy as np

from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import LABEL_SET
from src.video_annotating.video_annotator import draw_faces
from .spool import SessionSpool

MS = Fraction(1, 1000)

_log = logging.getLogger("archive.render")


@dataclass
class RenderResult:
    frames:       int
    duration_sec: float
    size_bytes:   int
    has_audio:    bool


class _Timeline:
    """Аналитика кадра по pts: точное совпадение или последняя запись не старше max_gap_ms."""

    def __init__(self, records: List[Dict[str, Any]], max_gap_ms: int = 300):
        by_pts          = {r["pts_ms"]: r for r in records if r.get("pts_ms") is not None}
        self._pts       = sorted(by_pts)
        self._records   = [by_pts[p] for p in self._pts]
        self.max_gap_ms = max_gap_ms

    def at(self, pts_ms: int) -> Optional[Dict[str, Any]]:
        i = bisect.bisect_right(self._pts, pts_ms) - 1
        if i < 0 or pts_ms - self._pts[i] > self.max_gap_ms:
            return None
        return self._records[i]


def _even(n: int) -> int:
    return max(2, n - n % 2)


def _estimate_fps(spool: SessionSpool) -> int:
    if spool.frames < 2 or spool.last_video_pts == spool.first_video_pts:
        return 20
    span = (spool.last_video_pts - spool.first_video_pts) / 1000.0
    return max(1, min(60, round((spool.frames - 1) / span)))


def render_recording(spool: SessionSpool, out_path: Path, crf: int = 26, preset: str = "veryfast",
                     threads: int = 2) -> RenderResult:
    """
    Сборка mp4 из спула: H.264 с переменной частотой кадров по pts и AAC.

    Args:
        spool    (SessionSpool)  : Спул завершённой сессии.
        out_path (Path)          : Путь итогового файла.
        crf      (int, optional) : Качество x264. Defaults to 26.
        preset   (str, optional) : Пресет x264. Defaults to "veryfast".
        threads  (int, optional) : Потоки кодека. Defaults to 2.

    Raises:
        ValueError: В спуле нет ни кадров, ни звука.

    Returns:
        RenderResult: Число кадров, длительность и размер файла.
    """
    has_video = spool.frames > 0
    has_audio = spool.audio_samples > 0 and spool.audio_rate is not None
    if not has_video and not has_audio:
        raise ValueError(f"Nothing to render for {spool.camera_id}")

    zero     = min(p for p in (spool.first_video_pts, spool.first_audio_pts) if p is not None)
    timeline = _Timeline(list(spool.iter_analytics()))
    frames   = 0
    end_ms   = 0

    with av.open(str(out_path), "w", format="mp4", options={"movflags": "+faststart"}) as out:
        vstream = astream = None
        if has_video:
            vstream                            = out.add_stream("libx264", rate=_estimate_fps(spool))
            vstream.pix_fmt                    = "yuv420p"
            vstream.options                    = {"crf": str(crf), "preset": preset}
            vstream.codec_context.thread_count = threads
            vstream.codec_context.time_base    = MS
        if has_audio:
            layout                  = "mono" if spool.audio_channels == 1 else "stereo"
            astream                 = out.add_stream("aac", rate=spool.audio_rate, layout=layout)
            astream.codec_context.bit_rate = 96000

        if vstream is not None:
            size = None
            for pts_ms, jpeg in spool.iter_frames():
                image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                if image is None:
                    continue
                if size is None:
                    size = (_even(image.shape[1]), _even(image.shape[0]))
                    vstream.width, vstream.height = size
                if (image.shape[1], image.shape[0]) != size:
                    image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
                record = timeline.at(pts_ms)
                if record is not None:
                    draw_faces(image, record["faces"], details=True)
                frame           = av.VideoFrame.from_ndarray(image, format="bgr24")
                frame.pts       = pts_ms - zero
                frame.time_base = MS
                out.mux(vstream.encode(frame))
                frames += 1
                end_ms  = max(end_ms, pts_ms - zero)
            out.mux(vstream.encode(None))

        if astream is not None:
            _encode_audio(out, astream, spool, lead_ms=spool.first_audio_pts - zero)
            end_ms = max(end_ms, spool.first_audio_pts - zero + spool.audio_samples * 1000 // spool.audio_rate)

    result = RenderResult(frames=frames, duration_sec=end_ms / 1000.0,
                          size_bytes=out_path.stat().st_size, has_audio=has_audio)
    _log.info(f"[{spool.camera_id}] rendered {out_path.name}: {frames} frames, "
              f"{result.duration_sec:.1f} s, {result.size_bytes / 1e6:.1f} MB, audio={has_audio}")
    return result


def _encode_audio(out, astream, spool: SessionSpool, lead_ms: int, block: int = 1024) -> None:
    rate, channels = spool.audio_rate, spool.audio_channels
    layout         = "mono" if channels == 1 else "stereo"
    frame_bytes    = 2 * channels
    pts            = 0

    def emit(pcm: bytes) -> None:
        nonlocal pts
        samples         = len(pcm) // frame_bytes
        arr             = np.frombuffer(pcm[:samples * frame_bytes], dtype=np.int16).reshape(1, -1)
        frame           = av.AudioFrame.from_ndarray(arr, format="s16", layout=layout)
        frame.sample_rate = rate
        frame.pts       = pts
        frame.time_base = Fraction(1, rate)
        pts            += samples
        out.mux(astream.encode(frame))

    lead = max(0, lead_ms) * rate // 1000
    while lead > 0:
        n     = min(block, lead)
        emit(b"\x00" * n * frame_bytes)
        lead -= n
    with open(spool.audio_path, "rb") as f:
        while True:
            pcm = f.read(block * frame_bytes)
            if len(pcm) < frame_bytes:
                break
            emit(pcm)
    out.mux(astream.encode(None))


def build_analytics_document(spool: SessionSpool, result: Optional[RenderResult] = None,
                             session: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Итоговый JSON сессии: таймлайн слитой аналитики и сводка по трекам и голосу.

    Args:
        spool   (SessionSpool)             : Спул сессии.
        result  (RenderResult, optional)   : Итог рендера. Defaults to None.
        session (Dict[str, Any], optional) : Описание сессии из stream_start. Defaults to None.

    Returns:
        Dict[str, Any]: Документ для сохранения в хранилище.
    """
    zero     = min((p for p in (spool.first_video_pts, spool.first_audio_pts) if p is not None), default=0)
    records  = sorted((r for r in spool.iter_analytics() if r.get("pts_ms") is not None), key=lambda r: r["pts_ms"])
    fused    = Counter()
    face     = Counter()
    voice    = Counter()
    tracks: Dict[Any, Counter] = defaultdict(Counter)
    timeline = []
    for r in records:
        for f in r["faces"]:
            if f.get("emotion"):
                fused[f["emotion"]] += 1
                tracks[f.get("track_id")][f["emotion"]] += 1
            if f.get("face_emotion"):
                face[f["face_emotion"]] += 1
        if r.get("voice") and r["voice"].get("label"):
            voice[r["voice"]["label"]] += 1
        timeline.append({**r, "t_ms": r["pts_ms"] - zero})

    return {
        "camera_id": spool.camera_id,
        "label_set": LABEL_SET,
        "labels": list(EMOTION_LABELS),
        "session": session or {},
        "truncated": spool.truncated,
        "recording": result.__dict__ if result is not None else None,
        "summary": {
            "frames_with_analytics": len(records),
            "fused_label_counts": dict(fused),
            "face_label_counts": dict(face),
            "voice_label_counts": dict(voice),
            "tracks": {str(t): {"label_counts": dict(c), "dominant": c.most_common(1)[0][0]}
                       for t, c in tracks.items()},
        },
        "timeline": timeline,
    }
