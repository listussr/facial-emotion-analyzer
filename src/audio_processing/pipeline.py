"""Аудиопайплайн: чанки -> окна -> VAD -> модель эмоций -> сглаживание -> audio-analytics."""

import logging
import threading
import time
import msgpack
import numpy as np

from dataclasses import dataclass
from dataclasses import replace
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple

from src.emotion_labels import label_of
from src.frames_processing.stream_metrics import LatencyWindow
from src.frames_processing.stream_metrics import StalenessFilter
from src.session_config import AudioSettings
from src.session_config import SessionConfig
from src.session_config import SessionConfigStore
from .buffer import CameraAudioBuffer
from .contracts import AudioAnalytics
from .recognizer import VoiceEmotionRecognizer
from .smoothing import VoiceSmoother
from .vad import EnergyVAD

_log = logging.getLogger("audio.pipeline")

WINDOW_MS_RANGE = (200, 10_000)
HOP_MS_MIN      = 50


def _rounded(values) -> List[float]:
    return [round(float(v), 5) for v in values]


@dataclass
class _Session:
    settings: AudioSettings
    buffer: CameraAudioBuffer
    smoother: VoiceSmoother
    window_seq: int = 0
    last_seen: float = 0.0
    windows: int = 0
    speech_windows: int = 0


class AudioPipeline:
    """
    Аудиопайплайн камер.

    Аудиопоток лёгкий, поэтому обработка в одном потоке. Без kafka_settings handle_chunk и handle_event
    вызываются напрямую. Окно без речи по VAD или по предобработке модели публикуется с is_speech=False.
    """

    def __init__(self, kafka_settings: Optional[Dict], defaults: AudioSettings,
                 session_store: Optional[SessionConfigStore] = None,
                 max_chunk_age_ms: Optional[int] = 2000, num_threads: Optional[int] = None,
                 vad_threshold_dbfs: float = -45.0, sample_rate: int = 16000,
                 session_idle_timeout_s: float = 30.0):
        missing = [k for k, v in vars(defaults).items() if v is None]
        if missing:
            raise ValueError(f"audio defaults must be fully specified, missing: {missing}")
        self.defaults               = self._validated("defaults", defaults, defaults)
        self.store                  = session_store
        self.sample_rate            = sample_rate
        self.num_threads            = num_threads
        self.session_idle_timeout_s = session_idle_timeout_s
        self.vad                    = EnergyVAD(sample_rate=sample_rate, threshold_dbfs=vad_threshold_dbfs)

        self._sessions: Dict[str, _Session] = {}
        self._dirty: Set[str] = set()
        self._dirty_lock = threading.Lock()
        self._recognizers: Dict[Tuple[str, str], Any] = {}
        self._failed: Dict[Tuple[str, str], str] = {}
        self._default_recognizer = self._recognizer(self.defaults)

        self.staleness      = StalenessFilter(max_chunk_age_ms)
        self.latency        = LatencyWindow()
        self._infer_s       = 0.0
        self._audio_s       = 0.0
        self.windows_total  = 0
        self.sessions_ended = 0

        if session_store is not None:
            session_store.set_on_change(self._on_config_change)

        self._kafka        = None
        self._events_topic = None
        if kafka_settings is not None:
            from src.frames_processing.kafka_io import KafkaIO
            self._events_topic = kafka_settings.get("events_topic")
            self._kafka = KafkaIO(
                kafka_settings["bootstrap_servers"], kafka_settings["input_topic"],
                kafka_settings["output_topic"], kafka_settings.get("group_id", "audio-pipeline"),
                extra_input_topics=[self._events_topic] if self._events_topic else None,
            )
        _log.info(f"Audio pipeline: defaults={self.defaults}, events_topic={self._events_topic}, "
                  f"max_chunk_age_ms={max_chunk_age_ms}, session_store={'on' if session_store else 'off'}")

    def _on_config_change(self, camera_id: str, old: Optional[SessionConfig],
                          new: Optional[SessionConfig]) -> None:
        with self._dirty_lock:
            self._dirty.add(camera_id)

    def _resolve(self, camera_id: str) -> AudioSettings:
        cfg     = self.store.get(camera_id) if self.store is not None else None
        session = cfg.audio if cfg is not None and cfg.audio is not None else AudioSettings()
        return self._validated(camera_id, session.merged_over(self.defaults), self.defaults)

    @staticmethod
    def _validated(camera_id: str, s: AudioSettings, defaults: AudioSettings) -> AudioSettings:
        fixes: Dict[str, Any] = {}
        if not isinstance(s.window_ms, int) or not WINDOW_MS_RANGE[0] <= s.window_ms <= WINDOW_MS_RANGE[1]:
            fixes["window_ms"] = defaults.window_ms
        window = fixes.get("window_ms", s.window_ms)
        if not isinstance(s.hop_ms, int) or not HOP_MS_MIN <= s.hop_ms <= window:
            fixes["hop_ms"] = min(defaults.hop_ms, window)
        if not isinstance(s.vad_threshold, (int, float)) or not 0.0 <= s.vad_threshold <= 1.0:
            fixes["vad_threshold"] = defaults.vad_threshold
        if s.device not in ("cpu", "cuda"):
            fixes["device"] = defaults.device
        if fixes:
            _log.warning(f"[{camera_id}] invalid audio settings replaced by defaults: "
                         f"{ {k: getattr(s, k) for k in fixes} }")
            s = replace(s, **fixes)
        return s

    def _recognizer(self, s: AudioSettings):
        key = (s.model, s.device)
        rec = self._recognizers.get(key)
        if rec is not None:
            return rec
        if key in self._failed:
            return self._default_recognizer
        try:
            rec = VoiceEmotionRecognizer(s.model, s.device, self.num_threads, self.sample_rate)
        except Exception as e:
            if not self._recognizers:
                raise
            self._failed[key] = str(e)
            _log.error(f"Cannot load voice model {key}: {e}; using default {self.defaults.model}")
            return self._default_recognizer
        self._recognizers[key] = rec
        return rec

    def _session(self, camera_id: str) -> _Session:
        with self._dirty_lock:
            dirty = camera_id in self._dirty
            self._dirty.discard(camera_id)
        sess = self._sessions.get(camera_id)
        if sess is not None and not dirty:
            return sess

        settings = self._resolve(camera_id)
        if sess is None:
            sess = _Session(settings=settings, smoother=VoiceSmoother(),
                            buffer=CameraAudioBuffer(camera_id, self.sample_rate,
                                                     settings.window_ms, settings.hop_ms))
            self._sessions[camera_id] = sess
            _log.info(f"[{camera_id}] audio session started: {settings}")
        elif settings != sess.settings:
            if (settings.window_ms, settings.hop_ms) != (sess.settings.window_ms, sess.settings.hop_ms):
                sess.buffer = CameraAudioBuffer(camera_id, self.sample_rate,
                                                settings.window_ms, settings.hop_ms)
            _log.info(f"[{camera_id}] audio settings changed: {sess.settings} -> {settings}")
            sess.settings = settings
        return sess

    def handle_chunk(self, payload: Dict[str, Any], now: Optional[float] = None) -> List[AudioAnalytics]:
        """
        Обработка аудиочанка продюсера.

        Args:
            payload (Dict[str, Any]): Сообщение AudioMessage.
            now     (float, optional): Текущее время для проверки устаревания. Defaults to None.

        Returns:
            List[AudioAnalytics]: Результаты окон, завершённых этим чанком.
        """
        now = time.time() if now is None else now
        if self.staleness.is_stale(payload, now):
            return []
        camera_id      = payload["camera_id"]
        sess           = self._session(camera_id)
        sess.last_seen = now

        windows = sess.buffer.push(
            payload["audio_data"], int(payload["pts_ms"]), float(payload["capture_wall_ts"]),
            seq=payload.get("seq"), channels=int(payload.get("channels", 1)),
            sample_rate=int(payload.get("sample_rate", self.sample_rate)),
        )
        if not windows:
            return []

        speech   = [self.vad.speech_prob(w.samples) for w in windows]
        to_model = [i for i, p in enumerate(speech) if p >= sess.settings.vad_threshold]
        outputs: Dict[int, Optional[Dict[str, Any]]] = {}
        recognizer = self._recognizer(sess.settings)
        if to_model:
            t0  = time.perf_counter()
            out = recognizer.predict([windows[i].samples for i in to_model])
            self._infer_s += time.perf_counter() - t0
            outputs = dict(zip(to_model, out))
        self._audio_s += len(windows) * sess.settings.hop_ms / 1000.0

        results = []
        for i, w in enumerate(windows):
            o = outputs.get(i)
            p = o["probs"] if o is not None else None
            if p is not None:
                sess.smoother.update(p)
                sess.speech_windows += 1
            processed = time.time()
            r = AudioAnalytics(
                camera_id=camera_id,
                window_seq=sess.window_seq,
                pts_start_ms=w.pts_start_ms,
                pts_end_ms=w.pts_end_ms,
                capture_wall_ts=w.capture_wall_ts,
                processed_wall_ts=processed,
                speech_prob=round(speech[i], 4),
                is_speech=p is not None,
                scores=[round(float(v), 5) for v in p] if p is not None else None,
                label=label_of(int(np.argmax(p))) if p is not None else None,
                logits=_rounded(o["logits"]) if o is not None else None,
                emb=_rounded(o["emb"]) if o is not None and o.get("emb") is not None else None,
                smoothed_scores=([round(float(v), 5) for v in sess.smoother.probs]
                                 if sess.smoother.probs is not None else None),
                smoothed_label=sess.smoother.label,
                model=getattr(recognizer, "name", sess.settings.model),
            )
            self.latency.add(processed - w.capture_wall_ts)
            sess.window_seq += 1
            sess.windows += 1
            self.windows_total += 1
            results.append(r)
        return results

    def handle_event(self, event: Dict[str, Any]) -> None:
        """stream_end освобождает сессию сразу; неполное последнее окно отбрасывается."""
        camera_id = event.get("camera_id")
        if event.get("event") == "stream_end" and camera_id in self._sessions:
            sess = self._sessions.pop(camera_id)
            self.sessions_ended += 1
            _log.info(f"[{camera_id}] audio session ended (reason={event.get('reason')}): "
                      f"{sess.windows} windows, {sess.speech_windows} with speech, "
                      f"{sess.buffer.resets} discontinuities")

    def sweep_idle(self, now: Optional[float] = None) -> List[str]:
        now  = time.time() if now is None else now
        idle = [c for c, s in self._sessions.items() if now - s.last_seen > self.session_idle_timeout_s]
        for c in idle:
            self._sessions.pop(c, None)
            _log.info(f"[{c}] audio session released after inactivity")
        return idle

    def real_time_factor(self) -> float:
        """Время инференса к длительности обработанного звука: меньше 1 — успеваем."""
        return self._infer_s / self._audio_s if self._audio_s > 0 else 0.0

    def process(self, log_every_windows: int = 50) -> None:
        if self._kafka is None:
            raise RuntimeError("AudioPipeline created without Kafka settings")
        _log.info("Audio pipeline started")
        last_sweep     = time.time()
        last_logged    = 0
        dropped_logged = 0
        try:
            while True:
                msg = self._kafka.consume_message(timeout=0.5)
                now = time.time()
                if now - last_sweep > 5.0:
                    self.sweep_idle(now)
                    last_sweep = now
                if msg is None:
                    continue
                try:
                    payload = msgpack.unpackb(msg.value(), raw=False)
                    if self._events_topic and msg.topic() == self._events_topic:
                        self.handle_event(payload)
                        continue
                    for r in self.handle_chunk(payload, now):
                        self._kafka.produce(r.to_dict())
                except Exception:
                    _log.exception("Audio chunk processing failed")
                    continue

                if self.windows_total - last_logged >= log_every_windows:
                    last_logged = self.windows_total
                    lat         = self.latency.summary_ms()
                    self.latency.reset()
                    dropped        = self.staleness.dropped - dropped_logged
                    dropped_logged = self.staleness.dropped
                    _log.info(
                        f"Audio: {self.windows_total} windows, sessions={len(self._sessions)}, "
                        f"latency p50={lat['p50']:.0f} p95={lat['p95']:.0f} max={lat['max']:.0f} ms, "
                        f"RTF={self.real_time_factor():.4f}, dropped stale={dropped} "
                        f"(total {self.staleness.dropped})"
                    )
        except KeyboardInterrupt:
            _log.info("Audio pipeline interrupted")
        finally:
            self._kafka.close()
            _log.info("Audio pipeline stopped")
