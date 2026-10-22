import numpy as np

RATE = 16000


def tone(ms: int, freq: float = 440.0, amp: float = 0.3, rate: int = RATE) -> np.ndarray:
    t = np.arange(rate * ms // 1000) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def silence(ms: int, rate: int = RATE) -> np.ndarray:
    return np.zeros(rate * ms // 1000, dtype=np.float32)


def to_pcm(x: np.ndarray, channels: int = 1) -> bytes:
    s = np.clip(x * 32767, -32768, 32767).astype("<i2")
    if channels > 1:
        s = np.repeat(s[:, None], channels, axis=1).ravel()
    return s.tobytes()


def chunks(signal: np.ndarray, camera_id: str = "cam", chunk_ms: int = 100, start_pts: int = 0,
           start_seq: int = 0, wall0: float = 1000.0, rate: int = RATE):
    """Нарезать сигнал на сообщения как у продюсера (AudioMessage, schema v2)."""
    n   = rate * chunk_ms // 1000
    out = []
    for i in range(0, signal.size, n):
        k     = i // n
        piece = signal[i:i + n]
        pts   = start_pts + k * chunk_ms
        out.append({
            "camera_id": camera_id, "stream": "audio", "seq": start_seq + k, "pts_ms": pts,
            "capture_wall_ts": wall0 + pts / 1000, "sample_rate": rate, "channels": 1,
            "num_samples": piece.size, "audio_data": to_pcm(piece),
        })
    return out
