"""
Сервис слияния: vision-analytics и audio-analytics -> fused-analytics.

Выход — надмножество результата видео: в faces[].emotion итоговая оценка, исходные — в face_* и voice_*.
К последнему окну лица трека подбирается окно голоса с наибольшим перекрытием, пара сливается методом
сессии. Голос приписывается только единственному лицу в кадре; иначе он — только голос сцены.
"""

import json
import logging
import time
import msgpack

from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

from src.frames_processing.stream_metrics import LatencyWindow
from src.frames_processing.stream_metrics import StalenessFilter
from src.session_config import FusionSettings
from src.session_config import SessionConfigStore
from .fuse import FusedSmoother
from .fuse import FusionParams
from .fuse import METHODS
from .fuse import ModalityFusion
from .recorder import FusionRecorder
from .voice_state import VoiceEvidence
from .voice_state import VoiceState

_log = logging.getLogger("fusion")

FUSION_SCHEMA_VERSION = 2

MODE_SINGLE_FACE = "single_face"
MODE_MULTI_FACE  = "multi_face"
MODE_NO_FACE     = "no_face"


@dataclass
class _Track:
    """Состояние слияния трека: pair — (окно лица, окно голоса, метод) последнего слияния, seq — их счётчик."""
    smoother: FusedSmoother
    last_seen: float
    face_window: Optional[Dict[str, Any]] = None
    pair: Optional[Tuple] = None
    used: Optional[str] = None
    voice_window: Optional[Dict[str, Any]] = None
    seq: int = 0


@dataclass
class _Camera:
    voice: VoiceState
    last_seen: float
    ended_at: Optional[float] = None
    frames: int = 0
    attributed: int = 0
    tracks: Dict[Any, _Track] = field(default_factory=dict)


class FusionService:
    """
    Сервис слияния модальностей.

    Без kafka_settings handle_* вызываются напрямую. После stream_end камера держится end_grace_s:
    последние результаты могут прийти позже события.
    """

    def __init__(self, kafka_settings: Optional[Dict], defaults: FusionSettings,
                 session_store: Optional[SessionConfigStore] = None,
                 max_frame_age_ms: Optional[int] = 2000, end_grace_s: float = 5.0,
                 idle_timeout_s: float = 30.0, recorder: Optional[FusionRecorder] = None,
                 fusion: Optional[ModalityFusion] = None, track_timeout_s: float = 10.0,
                 smoothing: float = 0.5, undetermined_below: float = 0.4):
        missing = [k for k, v in vars(defaults).items() if v is None]
        if missing:
            raise ValueError(f"fusion defaults must be fully specified, missing: {missing}")
        self.fusion = fusion or ModalityFusion()
        if defaults.method not in METHODS:
            raise ValueError(f"unknown fusion method {defaults.method!r}; available: {METHODS}")
        self.defaults           = defaults
        self.store              = session_store
        self.recorder           = recorder
        self.end_grace_s        = end_grace_s
        self.idle_timeout_s     = idle_timeout_s
        self.track_timeout_s    = track_timeout_s
        self.smoothing          = smoothing
        self.undetermined_below = undetermined_below
        self.staleness          = StalenessFilter(max_frame_age_ms)
        self.latency            = LatencyWindow()
        self._cameras: Dict[str, _Camera] = {}
        self.frames_total  = 0
        self.fusions_total = 0

        self._kafka = None
        self._topics: Dict[str, str] = {}
        if kafka_settings is not None:
            from src.frames_processing.kafka_io import KafkaIO
            self._topics = {
                "vision": kafka_settings["vision_topic"],
                "audio": kafka_settings["audio_topic"],
                "events": kafka_settings.get("events_topic") or "",
            }
            self._kafka = KafkaIO(
                kafka_settings["bootstrap_servers"], self._topics["vision"],
                kafka_settings["output_topic"], kafka_settings.get("group_id", "fusion"),
                extra_input_topics=[self._topics["audio"], self._topics["events"]],
            )
        _log.info(f"Fusion service: defaults={defaults}, trained params={self.fusion.trained}, "
                  f"topics={self._topics or 'none'}")

    def params(self, camera_id: str) -> FusionParams:
        cfg     = self.store.get(camera_id) if self.store is not None else None
        session = cfg.fusion if cfg is not None and cfg.fusion is not None else FusionSettings()
        s       = session.merged_over(self.defaults)
        fixes: Dict[str, Any] = {}
        if s.method not in METHODS:
            fixes["method"] = self.defaults.method
        for name in ("voice_max_age_ms", "min_overlap_ms"):
            v = getattr(s, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                fixes[name] = getattr(self.defaults, name)
        if fixes:
            _log.warning(f"[{camera_id}] invalid fusion settings replaced by defaults: "
                         f"{ {k: getattr(s, k) for k in fixes} }")
            s = replace(s, **fixes)
        return FusionParams(s.method, int(s.voice_max_age_ms), int(s.min_overlap_ms))

    def _camera(self, camera_id: str, now: float) -> _Camera:
        cam = self._cameras.get(camera_id)
        if cam is None:
            cam                      = _Camera(voice=VoiceState(), last_seen=now)
            self._cameras[camera_id] = cam
        cam.last_seen = now
        return cam

    def handle_audio(self, window: Dict[str, Any], now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self._camera(window["camera_id"], now).voice.add(window)
        if self.recorder is not None:
            self.recorder.record_voice(window, now)

    def handle_event(self, event: Dict[str, Any], now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        cam = self._cameras.get(event.get("camera_id"))
        if event.get("event") == "stream_end" and cam is not None:
            cam.ended_at = now

    def sweep(self, now: Optional[float] = None) -> list:
        """
        Освобождение завершённых и неактивных камер и забывание пропавших треков.

        Args:
            now (float, optional): Текущее время. Defaults to None.

        Returns:
            list: Освобождённые камеры.
        """
        now = time.time() if now is None else now
        gone = [c for c, s in self._cameras.items()
                if (s.ended_at is not None and now - s.ended_at > self.end_grace_s)
                or now - s.last_seen > self.idle_timeout_s]
        if self.recorder is not None:
            self.recorder.flush_inactive(now)
        for c in gone:
            if self.recorder is not None:
                self.recorder.flush_camera(c, now)
            s = self._cameras.pop(c)
            _log.info(f"[{c}] fusion state released: {s.frames} frames, "
                      f"{s.attributed} with voice attributed, {s.voice.windows_seen} audio windows")
        for cam in self._cameras.values():
            for tid in [t for t, tr in cam.tracks.items() if now - tr.last_seen > self.track_timeout_s]:
                del cam.tracks[tid]
        return gone

    @staticmethod
    def _voice_dict(ev: Optional[VoiceEvidence]) -> Optional[Dict[str, Any]]:
        if ev is None:
            return None
        return {"label": ev.label, "scores": ev.scores, "age_ms": ev.age_ms,
                "speech_prob": ev.speech_prob, "speaking": ev.speaking, "window_seq": ev.window_seq}

    def _track(self, cam: _Camera, track_id: Any, now: float) -> _Track:
        tr = cam.tracks.get(track_id)
        if tr is None:
            tr = cam.tracks[track_id] = _Track(
                smoother=FusedSmoother(self.smoothing, self.undetermined_below), last_seen=now)
        tr.last_seen = now
        return tr

    def _fuse_track(self, cam: _Camera, tr: _Track, pts: Optional[int], attributed: bool,
                    params: FusionParams) -> None:
        """
        Подбор голоса к окну лица трека и слияние новой пары.

        Если окно лица уже слито с голосом, мелькнувшее второе лицо в кадре этот голос не снимает.
        Ключ сглаживания — окно лица, без него — окно голоса.
        """
        voice = None
        if attributed and pts is not None:
            if tr.face_window is not None:
                fw    = tr.face_window
                voice = cam.voice.best_window(fw["start_pts_ms"], fw["end_pts_ms"], params.min_overlap_ms)
            else:
                voice = cam.voice.latest_speech_window(int(pts), params.voice_max_age_ms)
        face_key = tr.face_window["seq"] if tr.face_window is not None else None
        if (not attributed and tr.pair is not None and face_key is not None
                and tr.pair[0] == face_key and tr.pair[2] == params.method):
            return
        voice_key = voice["window_seq"] if voice is not None else None
        pair      = (face_key, voice_key, params.method)
        if pair == tr.pair or (face_key is None and voice_key is None):
            return
        out = self.fusion.fuse(params.method, tr.face_window, voice)
        if out is None:
            return
        tr.smoother.update(("face", face_key) if face_key is not None else ("voice", voice_key), out["probs"])
        tr.pair, tr.used, tr.voice_window = pair, out["used"], voice
        tr.seq += 1
        self.fusions_total += 1

    def handle_vision(self, result: Dict[str, Any], now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        Слияние результата кадра; кадры без pts_ms (schema v1) сливаются без голоса.

        Args:
            result (Dict[str, Any])  : Результат vision-analytics.
            now    (float, optional) : Текущее время. Defaults to None.

        Returns:
            Optional[Dict[str, Any]]: Слитый результат или None для устаревшего кадра.
        """
        now = time.time() if now is None else now
        if self.staleness.is_stale(result, now):
            return None
        camera_id = result["camera_id"]
        cam       = self._camera(camera_id, now)
        params    = self.params(camera_id)

        pts      = result.get("pts_ms")
        evidence = cam.voice.evidence_at(int(pts)) if pts is not None else None
        faces    = result.get("faces") or []
        mode     = MODE_NO_FACE if not faces else (MODE_SINGLE_FACE if len(faces) == 1 else MODE_MULTI_FACE)

        out         = dict(result)
        fused_faces = []
        for face in faces:
            tr = self._track(cam, face.get("track_id"), now)
            if face.get("emotion_window"):
                tr.face_window = face["emotion_window"]
            attributed = mode == MODE_SINGLE_FACE
            self._fuse_track(cam, tr, pts, attributed, params)

            voice                 = tr.voice_window
            f                     = {k: v for k, v in face.items() if k != "emotion_window"}
            f["face_emotion"]     = face.get("emotion")
            f["face_scores"]      = face.get("emotion_scores")
            f["voice_attributed"] = voice is not None
            f["voice_emotion"]    = voice.get("label") if voice else None
            f["voice_scores"]     = voice.get("scores") if voice else None
            f["voice_age_ms"]     = max(0, int(pts - voice["pts_end_ms"])) if voice and pts is not None else None
            f["fusion_used"]      = tr.used
            f["fusion_seq"]       = tr.seq
            f["emotion"]          = tr.smoother.label
            f["emotion_scores"]   = tr.smoother.scores
            fused_faces.append(f)
            if voice is not None:
                cam.attributed += 1

        out["faces"] = fused_faces
        out["fusion"] = {
            "schema_version": FUSION_SCHEMA_VERSION,
            "mode": mode,
            "method": params.method,
            "params": {"method": params.method, "voice_max_age_ms": params.voice_max_age_ms,
                       "min_overlap_ms": params.min_overlap_ms},
            "audio_windows_seen": cam.voice.windows_seen,
        }
        out["scene_voice"]   = self._voice_dict(evidence)
        out["fused_wall_ts"] = time.time()

        if self.recorder is not None:
            self.recorder.record_frame(out, now)

        cam.frames += 1
        self.frames_total += 1
        ts = result.get("capture_wall_ts")
        if ts is not None:
            self.latency.add(out["fused_wall_ts"] - ts)
        return out

    def process(self, log_every_frames: int = 100) -> None:
        if self._kafka is None:
            raise RuntimeError("FusionService created without Kafka settings")
        _log.info("Fusion service started")
        last_sweep  = time.time()
        last_logged = 0
        try:
            while True:
                msg = self._kafka.consume_message(timeout=0.5)
                now = time.time()
                if now - last_sweep > 2.0:
                    self.sweep(now)
                    last_sweep = now
                if msg is None:
                    continue
                topic = msg.topic()
                try:
                    if topic == self._topics["events"]:
                        self.handle_event(msgpack.unpackb(msg.value(), raw=False), now)
                    elif topic == self._topics["audio"]:
                        self.handle_audio(json.loads(msg.value()), now)
                    else:
                        fused = self.handle_vision(json.loads(msg.value()), now)
                        if fused is not None:
                            self._kafka.produce(fused)
                except Exception:
                    _log.exception(f"Fusion failed on message from {topic}")
                    continue

                if self.frames_total - last_logged >= log_every_frames:
                    last_logged = self.frames_total
                    lat         = self.latency.summary_ms()
                    self.latency.reset()
                    attributed = sum(c.attributed for c in self._cameras.values())
                    _log.info(
                        f"Fusion: {self.frames_total} frames, {self.fusions_total} window fusions, "
                        f"cameras={len(self._cameras)}, latency capture->fused p50={lat['p50']:.0f} "
                        f"p95={lat['p95']:.0f} max={lat['max']:.0f} ms, faces with voice={attributed}, "
                        f"dropped stale={self.staleness.dropped}"
                    )
        except KeyboardInterrupt:
            _log.info("Fusion service interrupted")
        finally:
            if self.recorder is not None:
                self.recorder.close()
            self._kafka.close()
            _log.info("Fusion service stopped")
