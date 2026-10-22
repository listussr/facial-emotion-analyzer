"""Предобработка входов — ровно та, на которой обучались модели.

Аудио:  моно, 16 кГц -> обрезка тишины по краям (top_db=30) -> нормировка по пику
        -> центральный фрагмент не длиннее 4 с.
Видео:  T=16 равномерно выбранных кадров окна -> одна рамка лица на окно (медиана рамок
        YuNet по кадрам, самое крупное лицо в кадре) -> расширение рамки на 25 % с каждой
        стороны -> resize 224x224 (INTER_AREA, без сохранения пропорций) -> RGB
        -> /255 -> нормировка ImageNet -> (T, 3, 224, 224).
"""
from __future__ import annotations


import numpy as np

SR = 16_000
MAX_SEC = 4.0
TRIM_TOP_DB = 30
MIN_SEC = 0.3                # короче — считаем, что речи нет (модальность отсутствует)
N_FRAMES = 16
FACE_SIZE = 224
FACE_MARGIN = 0.25
CLIP_SECONDS = 3.0
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], np.float32)
from .backends import MODELS  # noqa: E402

DEFAULT_YUNET = MODELS / "face_detection_yunet_2023mar.onnx"


# ===================================================================== аудио
def prepare_audio(y: np.ndarray, sr: int) -> np.ndarray | None:
    """Сырой сигнал (L,) или (каналы, L) -> float32 (L',) для аудиомодели; None — нет речи."""
    import librosa
    y = np.asarray(y, dtype=np.float32)
    if y.ndim == 2:                                    # (каналы, L) -> моно
        y = y.mean(0)
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    if y.size == 0 or not np.isfinite(y).all():
        return None
    y, _ = librosa.effects.trim(y, top_db=TRIM_TOP_DB)
    peak = float(np.abs(y).max()) if y.size else 0.0
    if y.size < MIN_SEC * SR or peak < 1e-4:
        return None
    y = y / peak
    n = int(SR * MAX_SEC)
    if len(y) > n:                                     # центральный фрагмент
        s = (len(y) - n) // 2
        y = y[s:s + n]
    return y.astype(np.float32)


def read_audio_ffmpeg(path, ffmpeg: str = "ffmpeg") -> np.ndarray | None:
    """Звуковая дорожка любого медиафайла (mp4, flv, mkv, rtsp-запись) -> моно 16 кГц float32.
    Нужен ffmpeg в PATH. None — дорожки нет."""
    import subprocess
    cmd = [ffmpeg, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True)
    if out.returncode != 0 or not out.stdout:
        return None
    return np.frombuffer(out.stdout, dtype=np.float32).copy()


def read_audio_raw(path) -> np.ndarray | None:
    """Сырой моно-сигнал 16 кГц: wav/flac/ogg — через soundfile, иначе (видео, mp3, ...) — ffmpeg."""
    import librosa
    try:
        y, _ = librosa.load(str(path), sr=SR, mono=True)
        return y
    except Exception:
        return read_audio_ffmpeg(path)


def load_audio_file(path) -> np.ndarray | None:
    """Файл -> подготовленный сигнал для аудиомодели (prepare_audio) или None."""
    y = read_audio_raw(path)
    return None if y is None else prepare_audio(y, SR)


# ===================================================================== видео
def sample_indices(total: int, n: int = N_FRAMES) -> np.ndarray:
    """Индексы n кадров, равномерно по окну из total кадров (как при обучении)."""
    return np.linspace(0, max(total - 1, 0), n).round().astype(int)


def read_video_frames(path, n: int = N_FRAMES) -> np.ndarray | None:
    """Видеофайл -> (n, H, W, 3) RGB uint8, кадры равномерно по всему файлу."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    if total > 0:
        for idx in sample_indices(total, n):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ok, fr = cap.read()
            if ok:
                frames.append(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
    else:                                              # нет счётчика кадров — читаем всё
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            frames.append(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
        frames = [frames[i] for i in sample_indices(len(frames), n)] if frames else []
    cap.release()
    if not frames:
        return None
    while len(frames) < n:
        frames.append(frames[-1])
    return np.stack(frames[:n])


def select_frames(frames, n: int = N_FRAMES) -> np.ndarray:
    """Список/массив кадров окна (любой длины) -> ровно n кадров равномерно."""
    frames = list(frames)
    return np.stack([frames[i] for i in sample_indices(len(frames), n)])


class FaceCropper:
    """Кроп лица YuNet (OpenCV >= 4.8, cv2.FaceDetectorYN)."""

    def __init__(self, model_path=DEFAULT_YUNET, score_threshold: float = 0.6):
        import cv2
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(str(model_path), "", (320, 320), score_threshold, 0.3, 5000)
        self.thr = score_threshold
        self._size = None

    def detect(self, rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Все лица кадра: [(x, y, w, h), ...] — для внешнего трекинга нескольких людей."""
        h, w = rgb.shape[:2]
        if self._size != (w, h):
            self.det.setInputSize((w, h))
            self._size = (w, h)
        _, faces = self.det.detect(self.cv2.cvtColor(rgb, self.cv2.COLOR_RGB2BGR))
        if faces is None:
            return []
        return [tuple(int(v) for v in f[:4]) for f in faces if f[-1] >= self.thr]

    def window_box(self, frames) -> tuple[int, int, int, int] | None:
        """Одна рамка на окно: медиана рамок самого крупного лица по кадрам."""
        boxes = []
        for f in frames:
            found = self.detect(f)
            if found:
                boxes.append(max(found, key=lambda b: b[2] * b[3]))
        if not boxes:
            return None
        return tuple(int(v) for v in np.median(np.array(boxes), axis=0))

    def crop(self, frames, box) -> np.ndarray:
        """Кадры (T, H, W, 3) + рамка (x, y, w, h) лица -> (T, 224, 224, 3) uint8."""
        H, W = frames[0].shape[:2]
        x, y, w, h = box
        dx, dy = int(w * FACE_MARGIN), int(h * FACE_MARGIN)
        x, y, w, h = x - dx, y - dy, w + 2 * dx, h + 2 * dy
        x, y = max(0, x), max(0, y)
        w, h = min(w, W - x), min(h, H - y)
        return np.stack([self.cv2.resize(f[y:y + h, x:x + w], (FACE_SIZE, FACE_SIZE),
                                         interpolation=self.cv2.INTER_AREA) for f in frames])

    def crop_window(self, frames, box=None) -> np.ndarray | None:
        """Кропы лица окна; box=None — найти самое крупное лицо; None — лица нет."""
        box = box or self.window_box(frames)
        return None if box is None else self.crop(frames, box)


def faces_to_input(faces: np.ndarray) -> np.ndarray:
    """(T, 224, 224, 3) RGB uint8 -> (T, 3, 224, 224) float32, нормировка ImageNet."""
    x = faces.astype(np.float32) / 255.0
    x = (x - IMAGENET_MEAN) / IMAGENET_STD
    return np.ascontiguousarray(x.transpose(0, 3, 1, 2))


def frame_times(n_used: int = N_FRAMES) -> np.ndarray:
    """Времена кадров (секунды, шкала 0..3), как при обучении.

    Модель обучена на 16 кадрах окна: время кадра k = (k + 0.5) / 16 * 3.
    Для режима 8 кадров берутся кадры 1, 3, ..., 15 из 16 (центры 8 сегментов)."""
    if n_used == N_FRAMES:
        fi = np.arange(N_FRAMES)
    else:
        edges = np.linspace(0, N_FRAMES, n_used + 1)
        fi = np.floor((edges[:-1] + edges[1:]) / 2).astype(int)
    return ((fi + 0.5) / N_FRAMES * CLIP_SECONDS).astype(np.float32), fi
