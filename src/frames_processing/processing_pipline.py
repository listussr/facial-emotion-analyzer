"""Видеопайплайн: детекция, трекинг, идентификация и эмоции лиц по окну кадров."""

import logging
import math
import queue
import threading
import time
import cv2
import msgpack
import numpy as np

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import LABEL_SET
from src.session_config import SessionConfigStore
from src.session_config import VisionSettings
from .kafka_io import KafkaIO
from .session_control import SessionControl
from .stream_metrics import LatencyWindow
from .stream_metrics import StalenessFilter
from .stream_metrics import capture_ts
from .processing import FaceDetector
from .processing import FaceIdentifier
from .processing import get_tracker
from .processing import represent_ltrb
from .processing import fast_face_filter
from .processing import BoxStabilizer
from .processing import ClipConfig
from .processing import TrackClip
from .processing import WindowSmoother
from .processing import crop_face
from .processing import load_clip_model
from .processing import DEFAULT_CLIP_MODEL

RESULT_SCHEMA_VERSION = 2
ANONYMOUS_FACE_ID     = "anonymous"
MIN_FACE_CROP_PX      = 20


class ProcessingPipeline:
    """
    Пайплайн обработки видеопотока.

    Кадр: детекция раз в detection_frequency кадров (между ними — прогноз трекера), трекинг,
    идентификация нового трека в фоне, кроп лица в окно трека и раз в emotion_hop_ms — эмоция по окну.
    Три стадии с ограниченными очередями: A — чтение и декодирование, B — обработка, C — публикация.
    """

    def __init__(self, kafka_settings: Dict, detector_settings: Dict, analyzer_settings: Dict,
                 tracker_settings: Dict, identifier_settings: Optional[Dict],
                 cache_cleanup: int = 10, detection_frequency: int = 1,
                 session_store: Optional[SessionConfigStore] = None,
                 max_frame_age_ms: Optional[int] = 1000,
                 clip_frames: int = 8, emotion_hop_ms: int = 1000, clip_window_ms: int = 3000,
                 window_smoothing: float = 0.5, undetermined_below: float = 0.4,
                 async_encode: bool = True, max_encode_inflight: int = 2):
        """
        Args:
            kafka_settings      (Dict)                        : Топики, группа и events_topic.
            detector_settings   (Dict)                        : backend и min_detection_confidence детектора.
            analyzer_settings   (Dict)                        : Модель эмоций по умолчанию: model, device, num_threads.
            tracker_settings    (Dict)                        : Настройки трекера по умолчанию.
            identifier_settings (Optional[Dict])              : Настройки идентификатора; None — без идентификации.
            cache_cleanup       (int, optional)               : Очистка кэша раз в N кадров. Defaults to 10.
            detection_frequency (int, optional)               : Детекция раз в N кадров. Defaults to 1.
            session_store       (SessionConfigStore, optional): Конфиги сессий. Defaults to None.
            max_frame_age_ms    (int, optional)               : Кадры старше отбрасываются. Defaults to 1000.
            clip_frames         (int, optional)               : Кадров на окно эмоций, 8 или 16. Defaults to 8.
            emotion_hop_ms      (int, optional)               : Шаг окна эмоций. Defaults to 1000.
            clip_window_ms      (int, optional)               : Длина окна эмоций. Defaults to 3000.
            window_smoothing    (float, optional)             : Вес прошлого в EMA по окнам. Defaults to 0.5.
            undetermined_below  (float, optional)             : Порог «не определено». Defaults to 0.4.
            async_encode        (bool, optional)              : Кодировать кропы в фоновом потоке. Defaults to True.
            max_encode_inflight (int, optional)               : Батчей в очереди энкодера, больше — кропы пропускаются. Defaults to 2.
        """
        self._default_tracker_settings: Dict = dict(tracker_settings or {})
        self._default_analyzer_settings: Dict = dict(analyzer_settings or {})
        self._events_topic: Optional[str] = (kafka_settings or {}).get("events_topic")

        self._init_kafka_io(kafka_settings)
        self._init_detector(detector_settings)
        self._init_analyzer(analyzer_settings)
        self._init_tracker(tracker_settings)
        self._init_identifier(identifier_settings)

        self._defaults = VisionSettings(
            tracker=self._default_tracker_settings.get("type", "bytetrack"),
            model=self._default_analyzer_settings.get("model", DEFAULT_CLIP_MODEL),
            device=self._default_analyzer_settings.get("device", "cpu"),
            detection_frequency=detection_frequency,
            clip_frames=clip_frames,
            emotion_hop_ms=emotion_hop_ms,
            identification=self._identifier is not None,
        )
        ClipConfig(n_frames=clip_frames, window_ms=clip_window_ms, hop_ms=emotion_hop_ms)
        self._clip_window_ms     = clip_window_ms
        self._window_smoothing   = window_smoothing
        self._undetermined_below = undetermined_below
        self._session_control    = SessionControl(self._defaults, store=session_store)
        if session_store is not None:
            session_store.set_on_change(self._session_control.on_config_change)

        self._session_trackers: Dict[str, "object"] = {}
        self._session_tracker_types: Dict[str, str] = {}
        self._recognizer_pool: Dict[Tuple[str, str, Optional[int]], "object"] = {}
        self._failed_recognizers: Dict[Tuple[str, str, Optional[int]], str] = {}
        self._recognizer_pool[self._recognizer_key(self._defaults)] = self._emotion_analyzer

        self._detection_frequency = detection_frequency
        self._frame_num           = 0
        self._cache_cleanup       = cache_cleanup
        self._cached_faces        = {}

        self._tracks_face_valid = {}

        self._staleness      = StalenessFilter(max_frame_age_ms)
        self._latency        = LatencyWindow()
        self._sessions_ended = 0

        self._identity_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="pipeline-identify"
        )

        self._encode_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="pipeline-encode"
        ) if async_encode else None
        self._encode_jobs: "deque" = deque()
        self._max_encode_inflight = max_encode_inflight
        self.encode_skipped       = 0

        logging.info(
            f"Initialized frames processing Pipeline: defaults={self._defaults}, "
            f"labels={LABEL_SET}, max_frame_age_ms={max_frame_age_ms}, "
            f"session_store={'on' if session_store else 'off'}, events_topic={self._events_topic}"
        )

    def _init_kafka_io(self, kafka_settings: Dict):
        bootstrap_servers = kafka_settings.get("bootstrap_servers", "")
        input_topic       = kafka_settings.get("input_topic", "")
        output_topic      = kafka_settings.get("output_topic", "")
        group_id          = kafka_settings.get("group_id", "emotion-pipeline-default")

        self._kafka = KafkaIO(
            bootstrap_servers,
            input_topic,
            output_topic,
            group_id,
            extra_input_topics=[self._events_topic] if self._events_topic else None,
        )
        logging.info("Initialized KafkaIO in pipeline.")

    def _init_detector(self, detector_settings: Dict):
        settings            = dict(detector_settings or {})
        backend             = settings.pop("backend", "yunet")
        min_confidence      = settings.pop("min_detection_confidence", 0.6)
        self._face_detector = FaceDetector(min_confidence, backend=backend, **settings)
        logging.info(f"Initialized FaceDetector in pipeline: backend={backend}, min_conf={min_confidence}")

    def _init_analyzer(self, analyzer_settings: Dict):
        settings = dict(analyzer_settings or {})
        settings.setdefault("model", DEFAULT_CLIP_MODEL)
        self._emotion_analyzer = load_clip_model(**settings)
        logging.info(f"Initialized clip emotion model in pipeline: {settings}")

    def _init_tracker(self, tracker_settings: Dict):
        self._tracker = get_tracker(tracker_settings)
        logging.info("Initialized tracker in pipeline")

    def _init_identifier(self, identifier_settings: Optional[Dict]):
        if identifier_settings is None:
            self._identifier = None
            logging.info("Face identification disabled (no identifier settings)")
            return
        self._identifier = FaceIdentifier(**identifier_settings)
        logging.info("Initialized identifier in pipeline")

    def _recognizer_key(self, settings: VisionSettings) -> Tuple[str, str, Optional[int]]:
        return (settings.model, settings.device, self._default_analyzer_settings.get("num_threads"))

    def _get_recognizer(self, settings: VisionSettings):
        """
        Модель эмоций сессии; сессии с одинаковыми model, device и threads делят экземпляр.

        Незагружаемая модель один раз логируется и заменяется моделью по умолчанию.

        Args:
            settings (VisionSettings): Настройки сессии.

        Returns:
            ClipEmotionModel: Модель эмоций по окну.
        """
        key   = self._recognizer_key(settings)
        recog = self._recognizer_pool.get(key)
        if recog is not None:
            return recog
        if key in self._failed_recognizers:
            return self._emotion_analyzer
        logging.info(f"Spawning clip emotion model for session: {key}")
        try:
            recog = load_clip_model(model=key[0], device=key[1], num_threads=key[2])
        except Exception as e:
            self._failed_recognizers[key] = str(e)
            logging.error(f"Cannot load emotion model {key}: {e}; using default {self._defaults.model}")
            return self._emotion_analyzer
        self._recognizer_pool[key] = recog
        return recog

    def _get_session_tracker(self, camera_id: str, settings: VisionSettings):
        """Трекер сессии; пересоздаётся при смене типа трекера или частоты детекции."""
        tracker_key = (settings.tracker, settings.detection_frequency)
        if self._session_control.take_reset(camera_id) or (
            camera_id in self._session_trackers
            and self._session_tracker_types.get(camera_id) != tracker_key
        ):
            logging.info(f"[{camera_id}] tracker changed to {tracker_key}; restarting tracks")
            self._end_tracks(camera_id)

        tracker = self._session_trackers.get(camera_id)
        if tracker is not None:
            return tracker
        per_session = self._tracker_settings_for(settings)
        logging.info(
            f"Spawning per-session tracker for {camera_id}: type={per_session.get('type')}, "
            f"detection every {settings.detection_frequency} frame(s)"
        )
        tracker                                = get_tracker(per_session)
        self._session_trackers[camera_id]      = tracker
        self._session_tracker_types[camera_id] = tracker_key
        return tracker

    def _tracker_settings_for(self, settings: VisionSettings) -> Dict:
        """
        Настройки трекера сессии с поправкой на частоту детекции.

        ByteTrack считает кадры по вызовам update, то есть по детекциям: при детекции раз в N кадров
        его частота — fps / N, а подтверждение трека — за ceil(K / N) детекций, иначе лица пропадают.

        Args:
            settings (VisionSettings): Настройки сессии.

        Returns:
            Dict: Настройки для get_tracker.
        """
        per_session         = dict(self._default_tracker_settings)
        per_session["type"] = settings.tracker
        n                   = max(1, int(settings.detection_frequency))
        if n > 1:
            if "frame_rate" in per_session:
                per_session["frame_rate"] = max(1, round(per_session["frame_rate"] / n))
            if "minimum_consecutive_frames" in per_session:
                per_session["minimum_consecutive_frames"] = max(
                    1, math.ceil(per_session["minimum_consecutive_frames"] / n))
        return per_session

    def _evict_session(self, camera_id: str) -> None:
        self._session_tracker_types.pop(camera_id, None)
        if self._session_trackers.pop(camera_id, None) is not None:
            logging.info(f"Released tracker for {camera_id}")

    def _end_tracks(self, camera_id: str) -> None:
        """Завершение треков камеры: таймсерии в БД, очистка кэша, освобождение трекера."""
        keys = [k for k in self._cached_faces if k[0] == camera_id]
        for key in keys:
            cached = self._cached_faces.pop(key, None)
            if cached is not None:
                self._flush_emotion_timeseries(cached, key)
        self._tracks_face_valid = {
            k: v for k, v in self._tracks_face_valid.items() if k[0] != camera_id
        }
        self._evict_session(camera_id)

    def _end_session(self, camera_id: str, reason: Optional[str]) -> None:
        self._end_tracks(camera_id)
        self._session_control.forget(camera_id)
        self._sessions_ended += 1
        logging.info(f"[{camera_id}] session ended (reason={reason})")

    _INACTIVITY_TIMEOUT = 5.0

    def _clean_cache(self, tracks: List, camera_id: str):
        """
        Удаление треков, пропавших из камеры или не обновлявшихся дольше _INACTIVITY_TIMEOUT.

        Перед удалением таймсерия трека уходит в БД; камера без треков освобождает свой трекер.

        Args:
            tracks    (List): Треки текущего кадра.
            camera_id (str) : Идентификатор камеры.
        """
        now              = time.time()
        active_track_ids = {t.track_id for t in tracks if t.is_confirmed()}

        evicted_keys: List[tuple] = []
        for key, cached in self._cached_faces.items():
            cam_id, tid       = key
            inactive_too_long = (now - cached.get("last_seen", now)) > self._INACTIVITY_TIMEOUT
            if (cam_id == camera_id and tid not in active_track_ids) or inactive_too_long:
                evicted_keys.append(key)

        for key in evicted_keys:
            cached = self._cached_faces.pop(key, None)
            if cached is not None:
                self._flush_emotion_timeseries(cached, key)

        evicted_set = set(evicted_keys)
        self._tracks_face_valid = {
            k: v for k, v in self._tracks_face_valid.items()
            if k not in evicted_set
            and not (k[0] == camera_id and k[1] not in active_track_ids)
        }

        if camera_id and not any(k[0] == camera_id for k in self._cached_faces):
            self._evict_session(camera_id)

    def _flush_emotion_timeseries(self, cached: Dict, key: tuple) -> None:
        """Таймсерия трека в БД; без идентификации или без отсчётов не пишется — она привязана к человеку."""
        samples = cached.get("emotion_samples") or []
        if not samples or self._identifier is None or cached.get("face_id") == ANONYMOUS_FACE_ID:
            return
        payload = {
            "camera_id": key[0],
            "track_id": key[1],
            "modality": "face",
            "started_at": cached.get("started_at"),
            "ended_at": time.time(),
            "label_set": LABEL_SET,
            "labels": list(EMOTION_LABELS),
            "samples": samples,
        }
        face_id = cached.get("face_id")
        try:
            self._identifier.submit_emotion_timeseries(face_id, payload)
        except Exception:
            logging.exception("Failed to submit emotion timeseries")

    def _new_cache_entry(self) -> Dict:
        now = time.time()
        return {
            "face_id": "pending-init",
            "emotion_label": None,
            "emotion_probs": None,
            "emotion_samples": [],
            "clip": None,
            "clip_model": None,
            "box": BoxStabilizer(),
            "smoother": WindowSmoother(self._window_smoothing, self._undetermined_below),
            "window_seq": 0,
            "started_at": now,
            "last_seen": now,
        }

    def _update_identity(self, track, face_crop: np.ndarray, identification: bool = True) -> Dict:
        """
        Запись кэша нового трека с запуском идентификации в фоне.

        Args:
            track                         : Трек лица.
            face_crop      (np.ndarray)   : Кроп лица.
            identification (bool, optional): False — face_id = anonymous. Defaults to True.

        Returns:
            Dict: Запись кэша; face_id пока pending-init.
        """
        cached = self._new_cache_entry()
        if not identification or self._identifier is None:
            cached["face_id"] = ANONYMOUS_FACE_ID
            return cached
        appearance = getattr(track, "last_feature", None)
        crop_copy  = face_crop.copy()
        self._identity_executor.submit(
            self._fill_identity_async, cached, crop_copy, appearance
        )
        return cached

    def _fill_identity_async(
        self,
        cached: Dict,
        face_crop: np.ndarray,
        appearance: np.ndarray,
    ) -> None:
        """Идентификация в фоне: pending-id сразу, UUID из БД — позже через on_resolved."""
        try:
            def _on_resolved(real_id: str) -> None:
                cached["face_id"] = real_id

            face_id = self._identifier.identify(
                face_crop, appearance, on_resolved=_on_resolved
            )
            cached["face_id"] = face_id
        except Exception:
            logging.exception("Identity worker failed; keeping pending id")

    def _clip_config(self, settings: VisionSettings) -> ClipConfig:
        return ClipConfig(n_frames=settings.clip_frames, window_ms=self._clip_window_ms,
                          hop_ms=settings.emotion_hop_ms)

    def _encode_faces(self, pending: List, model) -> None:
        """
        Батч-кодирование кропов лиц в признаки кадров, синхронно или в фоновом потоке.

        Если энкодер не успевает, кропы пропускаются без сдвига расписания — кадр для окна возьмётся позже.

        Args:
            pending (List): Тройки (cached, pts_ms, face_rgb).
            model         : Модель эмоций сессии.
        """
        if not pending:
            return
        batch   = np.stack([face for _, _, face in pending])
        targets = [(cached, pts_ms, cached["clip"]) for cached, pts_ms, _ in pending]
        if self._encode_executor is not None and len(self._encode_jobs) >= self._max_encode_inflight:
            self.encode_skipped += len(pending)
            return
        for _, pts_ms, clip in targets:
            clip.mark_sampled(pts_ms)
        if self._encode_executor is None:
            self._add_features(targets, model.encode(batch))
            return
        self._encode_jobs.append((self._encode_executor.submit(model.encode, batch), targets))

    @staticmethod
    def _add_features(targets: List, feats) -> None:
        for (cached, pts_ms, clip), feat in zip(targets, feats):
            if cached.get("clip") is clip:
                clip.add(pts_ms, feat, sampled=True)

    def _collect_encoded(self, wait: bool = False) -> None:
        """Готовые результаты фонового кодирования в порядке отправки."""
        while self._encode_jobs:
            future, targets = self._encode_jobs[0]
            if not wait and not future.done():
                return
            self._encode_jobs.popleft()
            try:
                self._add_features(targets, future.result())
            except Exception:
                logging.exception("Face encoding failed")

    def _update_track_emotion(self, cached: Dict, pts_ms: float, model) -> Optional[Dict]:
        """
        Эмоция трека по окну, если пора её считать.

        Сырой выход окна публикуется для слияния модальностей, сглаженные вероятности — для показа и истории.

        Args:
            cached (Dict) : Запись кэша трека.
            pts_ms (float): Время кадра.
            model         : Модель эмоций сессии.

        Returns:
            Optional[Dict]: Окно с логитами, вероятностями и эмбеддингом или None.
        """
        window = cached["clip"].window(pts_ms)
        if window is None:
            return None
        out                     = model.predict(window["feats"], window["times"])
        label, probs            = cached["smoother"].update(out["probs"])
        cached["emotion_label"] = label
        cached["emotion_probs"] = probs
        cached["window_seq"] += 1
        if label is not None:
            cached["emotion_samples"].append({
                "t": time.time(),
                "label": label,
                "scores": probs.tolist(),
            })
        return {
            "seq": cached["window_seq"],
            "start_pts_ms": int(window["start_ms"]),
            "end_pts_ms": int(window["end_ms"]),
            "frames": window["frames"],
            "model": getattr(model, "name", ""),
            "logits": np.asarray(out["logits"], np.float32).round(5).tolist(),
            "probs": np.asarray(out["probs"], np.float32).round(5).tolist(),
            "emb": np.asarray(out["emb"], np.float32).round(5).tolist(),
        }

    def _handle_frame(self, frame: np.ndarray, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """
        Обработка одного кадра.

        Между детекциями трекер предсказывает рамки; кропы для эмоций и идентификации берутся
        только с кадров с детекцией. Полоска у края кадра уже MIN_FACE_CROP_PX пропускается.

        Args:
            frame    (np.ndarray)    : Кадр BGR.
            metadata (Dict[str, Any]): Сообщение продюсера schema v1 или v2.

        Returns:
            Dict[str, Any]: Результат кадра для vision-analytics.
        """
        self._frame_num += 1
        camera_id = metadata["camera_id"]

        settings    = self._session_control.settings(camera_id, metadata.get("session_config"))
        frame_index = self._session_control.next_frame_index(camera_id)

        results = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "camera_id": camera_id,
            "frame_id": metadata["frame_id"],
            "seq": metadata.get("seq"),
            "pts_ms": metadata.get("pts_ms"),
            "capture_wall_ts": capture_ts(metadata),
            "label_set": LABEL_SET,
            "emotion_labels": list(EMOTION_LABELS),
            "faces": []
        }
        pts_ms      = self._frame_pts_ms(metadata)
        model       = self._get_recognizer(settings)
        clip_config = self._clip_config(settings)

        if (frame_index - 1) % settings.detection_frequency == 0:
            detections = self._face_detector.detect(frame)
        else:
            detections = None

        tracker = self._get_session_tracker(camera_id, settings)

        detected = detections is not None
        if detected:
            tracks = tracker.update(detections, frame)
        else:
            tracks = tracker.predict()

        gray_full        = None
        frame_h, frame_w = frame.shape[:2]

        pending_faces  = []
        result_entries = []
        clip_restarted = False

        for track in tracks:
            if not track.is_confirmed():
                continue

            if track.time_since_update >= settings.detection_frequency:
                continue
            fresh = detected and track.time_since_update == 0

            track_id  = track.track_id
            cache_key = (camera_id, track_id)

            ltrb = represent_ltrb(track)
            if ltrb is None:
                continue

            x, y, w, h = ltrb

            x1, x2 = max(x, 0), min(x + w, frame_w)
            y1, y2 = max(y, 0), min(y + h, frame_h)

            face_crop = frame[y1:y2, x1:x2]

            if min(face_crop.shape[:2]) < MIN_FACE_CROP_PX:
                continue

            face_valid = self._tracks_face_valid.get(cache_key)
            if face_valid is None:
                if not fresh or track.hits < 3:
                    continue
                if gray_full is None:
                    gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray_crop                          = gray_full[y1:y2, x1:x2]
                face_valid                         = fast_face_filter(gray_crop, w, h)
                self._tracks_face_valid[cache_key] = face_valid

            if not face_valid:
                continue

            cached = self._cached_faces.get(cache_key)
            if cached is None:
                if not fresh:
                    continue
                cached                        = self._update_identity(track, face_crop, identification=settings.identification)
                self._cached_faces[cache_key] = cached
            cached["last_seen"] = time.time()

            clip = cached["clip"]
            if clip is None or clip.config != clip_config or cached["clip_model"] is not model:
                clip_restarted = clip_restarted or clip is not None
                clip = cached["clip"] = TrackClip(clip_config)
                cached["clip_model"] = model
            if fresh:
                box = cached["box"].update((x, y, w, h))
                if clip.wants_frame(pts_ms):
                    face_rgb = crop_face(frame, box)
                    if face_rgb is not None:
                        pending_faces.append((cached, pts_ms, face_rgb))

            entry = {
                "bbox": [x1, y1, x2, y2],
                "track_id": track_id,
                "face_id": cached["face_id"],
                "emotion": None,
                "emotion_scores": [],
                "predicted": not fresh,
            }
            results["faces"].append(entry)
            result_entries.append((cached, entry))

        if clip_restarted:
            logging.info(f"[{camera_id}] clip settings or model changed; restarting emotion windows")
        self._encode_faces(pending_faces, model)
        self._collect_encoded()

        for cached, entry in result_entries:
            window           = self._update_track_emotion(cached, pts_ms, model)
            entry["emotion"] = cached["emotion_label"]
            entry["emotion_scores"] = (
                cached["emotion_probs"].tolist()
                if cached["emotion_probs"] is not None else []
            )
            entry["window_seq"] = cached["window_seq"]
            if window is not None:
                entry["emotion_window"] = window

        if self._frame_num % self._cache_cleanup == 0:
            self._clean_cache(tracks, camera_id)
            self._frame_num = 0

        return results

    @staticmethod
    def _frame_pts_ms(metadata: Dict[str, Any]) -> float:
        pts = metadata.get("pts_ms")
        if pts is not None:
            return float(pts)
        ts = capture_ts(metadata)
        return (ts if ts is not None else time.time()) * 1000.0

    def _handle_event(self, event: Dict[str, Any]) -> None:
        camera_id = event.get("camera_id")
        if not camera_id:
            return
        if event.get("event") == "stream_end":
            self._end_session(camera_id, event.get("reason"))
        elif event.get("event") == "stream_start":
            logging.info(
                f"[{camera_id}] stream_start: modalities={event.get('modalities')}, "
                f"degraded={event.get('degraded')}"
            )

    def _stage_consume_decode(
        self,
        in_q: "queue.Queue",
        stop: threading.Event,
    ) -> None:
        """
        Стадия A: чтение Kafka, отбрасывание устаревших кадров до декодирования, JPEG в кадр.

        В очередь уходят ("frame", frame, payload) и ("event", payload) в исходном порядке;
        полная очередь блокирует чтение.
        """
        log = logging.getLogger("pipeline.A")
        log.info("Stage A (consume+decode) started")
        while not stop.is_set():
            msg = self._kafka.consume_message(timeout=0.5)
            if msg is None:
                continue
            try:
                payload = msgpack.unpackb(msg.value(), raw=False)
                if self._events_topic and msg.topic() == self._events_topic:
                    item = ("event", payload)
                else:
                    if self._staleness.is_stale(payload, time.time()):
                        continue
                    jpeg_data = payload["frame_data"]
                    frame     = cv2.imdecode(np.frombuffer(jpeg_data, np.uint8), cv2.IMREAD_COLOR)
                    if frame is None:
                        logging.warning("Failed to decode frame")
                        continue
                    item = ("frame", frame, payload)
                while not stop.is_set():
                    try:
                        in_q.put(item, timeout=0.5)
                        break
                    except queue.Full:
                        continue
            except Exception:
                logging.exception("Stage A error")
        try:
            in_q.put_nowait(None)
        except queue.Full:
            pass
        log.info("Stage A stopped")

    def _stage_produce(
        self,
        out_q: "queue.Queue",
    ) -> None:
        log = logging.getLogger("pipeline.C")
        log.info("Stage C (produce) started")
        while True:
            item = out_q.get()
            if item is None:
                break
            try:
                self._kafka.produce(item)
            except Exception:
                logging.exception("Stage C error")
        log.info("Stage C stopped")

    def process(self):
        """Запуск пайплайна: стадии A и C в потоках, стадия B в текущем потоке."""
        logging.info("Processing pipeline started (threaded)")

        in_q: queue.Queue = queue.Queue(maxsize=4)
        out_q: queue.Queue = queue.Queue(maxsize=4)
        stop = threading.Event()

        consumer_thread = threading.Thread(
            target=self._stage_consume_decode,
            args=(in_q, stop),
            name="pipeline-consume",
            daemon=True,
        )
        producer_thread = threading.Thread(
            target=self._stage_produce,
            args=(out_q,),
            name="pipeline-produce",
            daemon=True,
        )
        consumer_thread.start()
        producer_thread.start()

        frame_count = 0

        WINDOW              = 100
        last_window_t       = None
        window_dropped      = 0
        IDLE_SWEEP_INTERVAL = 2.0
        last_idle_sweep     = time.time()

        try:
            while not stop.is_set():
                try:
                    item = in_q.get(timeout=0.5)
                except queue.Empty:
                    if time.time() - last_idle_sweep > IDLE_SWEEP_INTERVAL:
                        try:
                            self._clean_cache([], "")
                        except Exception:
                            logging.exception("Idle cache sweep failed")
                        last_idle_sweep = time.time()
                    continue
                if item is None:
                    break

                if item[0] == "event":
                    try:
                        self._handle_event(item[1])
                    except Exception:
                        logging.exception("Session event handling failed")
                    continue

                _, frame, payload = item
                if self._staleness.is_stale(payload, time.time()):
                    continue
                try:
                    result                      = self._handle_frame(frame, payload)
                    result["processed_wall_ts"] = time.time()
                    ts                          = result.get("capture_wall_ts")
                    if ts is not None:
                        self._latency.add(result["processed_wall_ts"] - ts)
                    while not stop.is_set():
                        try:
                            out_q.put(result, timeout=0.5)
                            break
                        except queue.Full:
                            continue

                    frame_count += 1
                    if last_window_t is None:
                        last_window_t = time.time()
                    if frame_count % WINDOW == 0:
                        now            = time.time()
                        dt             = now - last_window_t
                        fps            = WINDOW / dt if dt > 0 else 0
                        last_window_t  = now
                        lat            = self._latency.summary_ms()
                        dropped        = self._staleness.dropped - window_dropped
                        window_dropped = self._staleness.dropped
                        self._latency.reset()
                        logging.info(
                            f"Analyzed {frame_count} frames "
                            f"({fps:.1f} FPS over last {WINDOW}), "
                            f"latency p50={lat['p50']:.0f} p95={lat['p95']:.0f} max={lat['max']:.0f} ms, "
                            f"dropped stale={dropped} (total {self._staleness.dropped}), "
                            f"face crops skipped (encoder busy)={self.encode_skipped} "
                            f"[in_q={in_q.qsize()}/{in_q.maxsize}, "
                            f"out_q={out_q.qsize()}/{out_q.maxsize}]"
                        )
                except Exception:
                    logging.exception("Stage B error on frame")
        except KeyboardInterrupt:
            logging.info("Pipeline interrupted, shutting down")
        finally:
            stop.set()
            try:
                for key, cached in list(self._cached_faces.items()):
                    self._flush_emotion_timeseries(cached, key)
                self._cached_faces.clear()
            except Exception:
                logging.exception("Final emotion flush failed")
            try:
                out_q.put(None, timeout=1.0)
            except queue.Full:
                pass
            consumer_thread.join(timeout=2.0)
            producer_thread.join(timeout=2.0)

            try:
                if self._encode_executor is not None:
                    self._encode_executor.shutdown(wait=False, cancel_futures=True)
                self._identity_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                logging.exception("Identity executor shutdown failed")

            try:
                self._kafka.close()
            except Exception:
                logging.exception("KafkaIO close failed")
            logging.info("Pipeline stopped")
