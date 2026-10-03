import random

import numpy as np

from src.video_producing.audio_chunker import AudioChunker

RATE = 16000
SPC = 1600  # 100 мс


def pcm(samples: int, channels: int = 1) -> bytes:
    return np.arange(samples * channels, dtype=np.int16).tobytes()


def test_random_pieces_give_exact_chunks():
    ch = AudioChunker(RATE, 1, SPC)
    data = pcm(RATE)  # 1 с
    rnd = random.Random(0)
    out, pos, sent = [], 0, 0
    while pos < len(data):
        n = rnd.randint(1, 900) * 2
        piece = data[pos:pos + n]
        # pts передаём только для первого куска — дальше поток непрерывен
        out += ch.push(piece, 0 if pos == 0 else None, 1000.0)
        pos += len(piece)
        sent += len(piece) // 2
    assert ch.flush() is None
    assert [c.pts_ms for c in out] == list(range(0, 1000, 100))
    assert all(c.num_samples == SPC for c in out)
    assert b''.join(c.pcm for c in out) == data
    assert abs(out[3].capture_wall_ts - 1000.3) < 1e-9


def test_consistent_pts_does_not_rebase():
    ch = AudioChunker(RATE, 1, SPC)
    out = []
    for i in range(10):  # блоки по 20 мс с честным pts
        out += ch.push(pcm(320), i * 20, 0.0)
    assert [c.pts_ms for c in out] == [0, 100]


def test_flush_returns_partial_tail():
    ch = AudioChunker(RATE, 1, SPC)
    out = ch.push(pcm(2000), 0, 0.0)
    tail = ch.flush()
    assert len(out) == 1
    assert tail.num_samples == 400 and tail.pts_ms == 100
    assert ch.flush() is None


def test_discontinuity_rebases():
    ch = AudioChunker(RATE, 1, SPC)
    out = ch.push(pcm(2000), 0, 0.0)      # 1 полный чанк + 400 сэмплов в буфере
    out += ch.push(pcm(1600), 5000, 5.0)  # разрыв: ожидали 125 мс
    assert [c.pts_ms for c in out] == [0, 100, 5000]
    assert [c.num_samples for c in out] == [1600, 400, 1600]


def test_stereo_samples_counted_per_channel():
    ch = AudioChunker(RATE, 2, SPC)
    out = ch.push(pcm(1600, channels=2), 0, 0.0)
    assert len(out) == 1 and out[0].num_samples == 1600
    assert len(out[0].pcm) == 1600 * 2 * 2
