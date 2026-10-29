"""Секундный ряд эмоций субъекта сессии (series.json) по покадровой слитой аналитике."""

import numpy as np

from collections import defaultdict
from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Tuple

from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import LABEL_SET
from src.emotion_labels import NUM_EMOTIONS

SERIES_VERSION = 1


def _identity(face_id: Any) -> Optional[str]:
    """Постоянный id узнанного человека; временные pending-* до узнавания — не личность."""
    if not face_id or str(face_id).startswith("pending"):
        return None
    return str(face_id)


def people_of_tracks(frames: Iterable[Tuple[int, List[Dict[str, Any]]]]) -> Dict[Any, str]:
    """
    Человек каждого трека: face:<id>, если трек узнан (берётся последний постоянный id), иначе track:<номер>.

    Args:
        frames (Iterable[Tuple[int, List[Dict]]]): Кадры: время в мс и лица.

    Returns:
        Dict[Any, str]: track_id -> ключ человека.
    """
    identity: Dict[Any, Optional[str]] = {}
    for _, faces in frames:
        for face in faces:
            track = face.get("track_id")
            if track is None:
                continue
            identity.setdefault(track, None)
            if _identity(face.get("face_id")):
                identity[track] = _identity(face.get("face_id"))
    return {track: f"face:{pid}" if pid else f"track:{track}" for track, pid in identity.items()}


def _probs(scores: Any) -> Optional[np.ndarray]:
    if not scores or len(scores) != NUM_EMOTIONS:
        return None
    p = np.asarray(scores, np.float64)
    s = float(p.sum())
    return p / s if s > 0 else None


def choose_subject(frames: List[Tuple[int, List[Dict[str, Any]]]], max_overlap: float = 0.1) -> List[str]:
    """
    Люди, составляющие субъекта сессии «одна камера — один человек».

    Основа — человек с наибольшим числом кадров. К нему добавляются люди, почти не пересекающиеся с ним по
    времени (вышел из кадра и вернулся с новым треком), кроме узнанных как другой человек.

    Args:
        frames      (List[Tuple[int, List[Dict]]]): Кадры: время в мс и лица.
        max_overlap (float, optional)              : Допустимая доля общих кадров. Defaults to 0.1.

    Returns:
        List[str]: Ключи людей субъекта (face:<id> или track:<номер>); пустой список, если лиц не было.
    """
    person = people_of_tracks(frames)
    seen: Dict[str, set] = defaultdict(set)
    for t, faces in frames:
        for face in faces:
            if face.get("track_id") in person:
                seen[person[face["track_id"]]].add(t)
    if not seen:
        return []
    order   = sorted(seen, key=lambda k: -len(seen[k]))
    subject = [order[0]]
    covered = set(seen[order[0]])
    for key in order[1:]:
        other_person = key.startswith("face:") and any(s.startswith("face:") for s in subject)
        if not other_person and len(seen[key] & covered) <= max_overlap * len(seen[key]):
            subject.append(key)
            covered |= seen[key]
    return subject


def build_series(records: Iterable[Dict[str, Any]], camera_id: str = "", step_ms: int = 1000) -> Dict[str, Any]:
    """
    Секундный ряд по записям timeline из analytics.json.

    В каждой секунде усредняются вероятности субъекта: итоговые (fused), лица и голоса. face_present — доля
    кадров секунды с лицом субъекта, predicted — доля из них, где рамку предсказал трекер, others — доля кадров
    с другими людьми, speech — доля кадров с речью. Секунда без лица субъекта даёт null в fused и face.

    Args:
        records   (Iterable[Dict[str, Any]]): Записи с t_ms (или pts_ms), faces и voice.
        camera_id (str, optional)           : Идентификатор сессии. Defaults to "".
        step_ms   (int, optional)           : Шаг ряда, мс. Defaults to 1000.

    Returns:
        Dict[str, Any]: Документ series.json.
    """
    frames   = []
    relative = True
    for r in records:
        if "t_ms" not in r:
            relative = False
        t = r.get("t_ms", r.get("pts_ms"))
        if t is not None:
            frames.append((int(t), r.get("faces") or [], r.get("voice")))
    frames.sort(key=lambda x: x[0])
    zero    = 0 if relative or not frames else frames[0][0]
    pairs   = [(t, faces) for t, faces, _ in frames]
    person  = people_of_tracks(pairs)
    subject = set(choose_subject(pairs))

    n       = ((frames[-1][0] - zero) // step_ms + 1) if frames else 0
    acc     = {name: np.zeros((n, NUM_EMOTIONS)) for name in ("fused", "face", "voice")}
    cnt     = {name: np.zeros(n) for name in ("fused", "face", "voice")}
    total   = np.zeros(n)
    present = np.zeros(n)
    pred    = np.zeros(n)
    others  = np.zeros(n)
    speech  = np.zeros(n)
    tracks  = set()
    for t, faces, voice in frames:
        i = (t - zero) // step_ms
        if i < 0 or i >= n:
            continue
        total[i] += 1
        mine = [f for f in faces if person.get(f.get("track_id")) in subject]
        if len(faces) > len(mine):
            others[i] += 1
        if voice and (voice.get("speaking") if "speaking" in voice else voice.get("label") is not None):
            speech[i] += 1
        if not mine:
            continue
        face = mine[0]
        tracks.add(face.get("track_id"))
        present[i] += 1
        pred[i]    += 1 if face.get("predicted") else 0
        for name, field in (("fused", "emotion_scores"), ("face", "face_scores"), ("voice", "voice_scores")):
            p = _probs(face.get(field))
            if p is not None:
                acc[name][i] += p
                cnt[name][i] += 1

    def mean(name: str) -> List[Optional[List[float]]]:
        return [[round(float(x), 3) for x in acc[name][i] / cnt[name][i]] if cnt[name][i] else None
                for i in range(n)]

    def share(values: np.ndarray, base: np.ndarray) -> List[float]:
        return [round(float(v / b), 3) if b else 0.0 for v, b in zip(values, base)]

    return {
        "version":      SERIES_VERSION,
        "camera_id":    camera_id,
        "label_set":    LABEL_SET,
        "labels":       list(EMOTION_LABELS),
        "step_s":       step_ms / 1000.0,
        "duration_s":   round(n * step_ms / 1000.0, 3),
        "subject":      {"people": sorted(subject), "tracks": sorted(t for t in tracks if t is not None)},
        "t":            [round(i * step_ms / 1000.0, 3) for i in range(n)],
        "face_present": share(present, total),
        "predicted":    share(pred, present),
        "others":       share(others, total),
        "speech":       share(speech, total),
        "fused":        mean("fused"),
        "face":         mean("face"),
        "voice":        mean("voice"),
    }


def build_series_from_document(document: Dict[str, Any]) -> Dict[str, Any]:
    """
    series.json по документу analytics.json.

    Args:
        document (Dict[str, Any]): Документ analytics.json.

    Returns:
        Dict[str, Any]: Документ series.json.
    """
    return build_series(document.get("timeline") or [], camera_id=document.get("camera_id", ""))
