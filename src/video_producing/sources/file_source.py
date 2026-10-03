import logging
import time
from typing import Iterator, List, Optional, Union

import av

from ..audio_chunker import AudioChunker
from .base import AudioChunk, MediaSource, SourceInfo, VideoFrame

_LIVE_PREFIXES = ('rtsp://', 'rtsps://', 'rtmp://', 'http://', 'https://', 'udp://', 'srt://')


class FileSource(MediaSource):
    def __init__(self, path: str, sample_rate: int, channels: int, samples_per_chunk: int,
                 rtsp_transport: str = 'tcp', open_timeout_s: float = 10.0):
        """
        Источник из файла или сетевого потока через PyAV (ffmpeg).

        Видео и аудио демультиплексируются в одном цикле, поэтому события
        выходят примерно в порядке времени. `pts_ms` обоих потоков считаются
        от общего нуля — минимального стартового времени выбранных потоков.

        Args:
            path (str): Путь к файлу или URL (rtsp://, http:// и т.п.).
            sample_rate (int): Частота дискретизации аудио на выходе, Гц.
            channels (int): Количество аудиоканалов на выходе (1 или 2).
            samples_per_chunk (int): Сэмплов (на канал) в одном аудиочанке.
            rtsp_transport (str): Транспорт для RTSP ('tcp' / 'udp').
            open_timeout_s (float): Таймаут открытия и чтения сетевого потока.
        """
        self._path = path
        self._sample_rate = sample_rate
        self._channels = channels
        self._samples_per_chunk = samples_per_chunk
        self._rtsp_transport = rtsp_transport
        self._open_timeout_s = open_timeout_s

        self._is_live = path.lower().startswith(_LIVE_PREFIXES)
        self._container = None
        self._video_stream = None
        self._audio_stream = None
        self._resampler: Optional[av.AudioResampler] = None
        self._chunker: Optional[AudioChunker] = None
        self._zero_s: Optional[float] = None
        self._wall_t0: Optional[float] = None

    def open(self, want_video: bool, want_audio: bool) -> SourceInfo:
        options = {}
        if self._is_live:
            if self._path.lower().startswith(('rtsp://', 'rtsps://')):
                options['rtsp_transport'] = self._rtsp_transport
            timeout = (self._open_timeout_s, self._open_timeout_s)
        else:
            timeout = None

        self._container = av.open(self._path, options=options, timeout=timeout)

        has_video = bool(self._container.streams.video)
        has_audio = bool(self._container.streams.audio)

        if want_video and has_video:
            self._video_stream = self._container.streams.video[0]
            self._video_stream.thread_type = 'AUTO'
        if want_audio and has_audio:
            self._audio_stream = self._container.streams.audio[0]
            layout = 'mono' if self._channels == 1 else 'stereo'
            self._resampler = av.AudioResampler(format='s16', layout=layout, rate=self._sample_rate)
            self._chunker = AudioChunker(self._sample_rate, self._channels, self._samples_per_chunk)

        self._zero_s = self._compute_zero()
        self._wall_t0 = time.time()

        info = SourceInfo(has_video=has_video, has_audio=has_audio, is_live=self._is_live)
        if has_video:
            vs = self._container.streams.video[0]
            if vs.average_rate:
                info.native_fps = float(vs.average_rate)
            if vs.frames:
                info.total_frames = int(vs.frames)
        if has_audio:
            info.audio_native_rate = self._container.streams.audio[0].rate
        if self._container.duration:
            info.duration_sec = self._container.duration / av.time_base
        if info.total_frames is None and info.native_fps and info.duration_sec:
            info.total_frames = int(info.native_fps * info.duration_sec)

        logging.info(
            f"FileSource opened {self._path}: video={has_video} audio={has_audio} "
            f"live={self._is_live} fps={info.native_fps} duration={info.duration_sec}"
        )
        return info

    def _compute_zero(self) -> Optional[float]:
        starts: List[float] = []
        for stream in (self._video_stream, self._audio_stream):
            if stream is not None and stream.start_time is not None and stream.time_base:
                starts.append(float(stream.start_time * stream.time_base))
        return min(starts) if starts else None

    def _rel_ms(self, t: Optional[float]) -> Optional[float]:
        if t is None:
            return None
        if self._zero_s is None:
            self._zero_s = t
        return (t - self._zero_s) * 1000.0

    def _wall_ts(self, pts_ms: Optional[float]) -> float:
        if self._is_live or pts_ms is None:
            return time.time()
        return self._wall_t0 + pts_ms / 1000.0

    def read(self) -> Iterator[Union[VideoFrame, AudioChunk]]:
        if self._container is None:
            raise RuntimeError("FileSource.read() called before open()")

        streams = [s for s in (self._video_stream, self._audio_stream) if s is not None]
        if not streams:
            return

        last_video_ms = -1
        for packet in self._container.demux(*streams):
            if packet.stream is self._video_stream:
                for frame in packet.decode():
                    pts_ms = self._rel_ms(frame.time)
                    if pts_ms is None:
                        pts_ms = last_video_ms + 1
                    pts_ms_i = int(round(pts_ms))
                    if pts_ms_i <= last_video_ms:
                        continue
                    last_video_ms = pts_ms_i
                    yield VideoFrame(
                        pts_ms=pts_ms_i,
                        capture_wall_ts=self._wall_ts(pts_ms),
                        image=frame.to_ndarray(format='bgr24'),
                    )
            elif packet.stream is self._audio_stream:
                for frame in packet.decode():
                    yield from self._resample(frame)

        if self._audio_stream is not None:
            yield from self._resample(None)
            tail = self._chunker.flush()
            if tail is not None:
                yield tail

    def _resample(self, frame) -> Iterator[AudioChunk]:
        for out in self._resampler.resample(frame):
            pts_ms = self._rel_ms(out.time) if out.pts is not None else None
            pcm = out.to_ndarray().tobytes()
            yield from self._chunker.push(pcm, pts_ms, self._wall_ts(pts_ms))

    def close(self) -> None:
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                logging.exception("FileSource close failed")
            self._container = None
