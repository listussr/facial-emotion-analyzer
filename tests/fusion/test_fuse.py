import numpy as np
import pytest

from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import NUM_EMOTIONS
from src.fusion.fuse import DEFAULT_METHOD
from src.fusion.fuse import METHODS
from src.fusion.fuse import FusedSmoother
from src.fusion.fuse import ModalityFusion
from src.fusion.voice_state import VoiceState
from src.fusion.voice_state import overlap_ms


def onehot(i):
    return list(np.eye(NUM_EMOTIONS)[i])


def logits(i, strength=5.0):
    return [float(v) for v in np.eye(NUM_EMOTIONS)[i] * strength]


def window(seq, start, end, speech=True, label_idx=1):
    return {"camera_id": "cam", "window_seq": seq, "pts_start_ms": start, "pts_end_ms": end,
            "is_speech": speech, "speech_prob": 1.0 if speech else 0.0,
            "smoothed_scores": onehot(label_idx), "smoothed_label": EMOTION_LABELS[label_idx],
            "logits": logits(label_idx) if speech else None,
            "emb": [0.1] * 256 if speech else None}


def face_window(seq, idx=0, emb=True):
    return {"seq": seq, "start_pts_ms": 0, "end_pts_ms": 3000, "logits": logits(idx),
            "emb": [0.2] * 256 if emb else None}


@pytest.fixture(scope="module")
def fusion():
    return ModalityFusion()



def test_all_methods_produce_probabilities(fusion):
    for m in METHODS:
        out = fusion.fuse(m, face_window(1, idx=0), window(0, 0, 3000, label_idx=3))
        assert len(out["probs"]) == NUM_EMOTIONS and sum(out["probs"]) == pytest.approx(1, abs=1e-4)
        assert out["label"] in EMOTION_LABELS and out["used"] == m


def test_missing_modality_falls_back_to_the_other(fusion):
    out = fusion.fuse("stack_hidden", face_window(1, idx=4), None)
    assert out["used"] == "stack_hidden -> video_only (нет аудио)" and out["label"] == "neutral"
    out = fusion.fuse("stack_hidden", None, window(0, 0, 3000, label_idx=3))
    assert out["used"] == "stack_hidden -> audio_only (нет видео)" and out["label"] == "happy"
    assert fusion.fuse("stack_hidden", None, None) is None
    assert fusion.fuse("audio_only", face_window(1), None) is None
    assert fusion.fuse("mean_probs", {"seq": 1}, window(0, 0, 3000, speech=False)) is None


def test_single_modality_methods_match_softmax(fusion):
    out = fusion.fuse("video_only", face_window(1, idx=2), window(0, 0, 3000, label_idx=3))
    z   = np.exp(logits(2))
    assert out["probs"] == pytest.approx(list(z / z.sum()), abs=1e-4)


def test_stack_hidden_without_embedding_uses_stack_logits(fusion):
    out = fusion.fuse("stack_hidden", face_window(1, emb=False), window(0, 0, 3000))
    assert out["used"] == "stack_hidden -> stack_logits"


def test_unknown_method_uses_default(fusion):
    assert fusion.resolve("nope") == DEFAULT_METHOD


def test_without_params_trained_methods_fall_back(tmp_path):
    f = ModalityFusion(tmp_path / "missing.json")
    assert not f.trained and f.resolve("stack_hidden") == "mean_probs"
    assert f.resolve("confidence_weighted") == "confidence_weighted"
    assert f.fuse("stack_hidden", face_window(1), window(0, 0, 3000))["used"] == "mean_probs"


def test_kit_weighted_logits_alpha_is_used(fusion):
    alpha = fusion._kit.params["weighted_logits"]["alpha"]
    out   = fusion.fuse("weighted_logits", face_window(1, idx=0), window(0, 0, 3000, label_idx=1))
    s     = alpha * np.array(logits(1)) + (1 - alpha) * np.array(logits(0))
    z     = np.exp(s - s.max())
    assert out["probs"] == pytest.approx(list(z / z.sum()), abs=1e-4)



def test_smoother_ema_replace_and_undetermined():
    s = FusedSmoother(keep=0.5, undetermined_below=0.4)
    s.update(("face", 1), onehot(0))
    assert s.label == "anger"
    s.update(("face", 2), onehot(1))
    assert s.scores[0] == pytest.approx(0.5) and s.label == "anger"
    s.update(("face", 2), onehot(2))
    assert s.scores[1] == 0 and s.scores[2] == pytest.approx(0.5)
    flat = FusedSmoother()
    flat.update("k", [1 / NUM_EMOTIONS] * NUM_EMOTIONS)
    assert flat.label is None and len(flat.scores) == NUM_EMOTIONS



def test_as_of_join_takes_latest_finished_window():
    s = VoiceState()
    s.add(window(0, 0, 2000, label_idx=1))
    s.add(window(1, 1000, 3000, label_idx=2))
    assert s.evidence_at(1500) is None
    e = s.evidence_at(2100)
    assert e.window_seq == 0 and e.age_ms == 100
    e = s.evidence_at(3500)
    assert e.window_seq == 1 and e.label == "fear" and e.age_ms == 500


def test_small_future_tolerance():
    s = VoiceState()
    s.add(window(0, 0, 2000))
    assert s.evidence_at(1850).age_ms == 0


def test_silence_keeps_voice_but_ages_it():
    s = VoiceState()
    s.add(window(0, 0, 2000, speech=True))
    s.add(window(1, 1000, 3000, speech=False))
    s.add(window(2, 2000, 4000, speech=False))
    e = s.evidence_at(4000)
    assert e.window_seq == 0 and e.age_ms == 2000 and e.speaking is False


def test_out_of_order_windows_are_sorted():
    s = VoiceState()
    s.add(window(1, 1000, 3000, label_idx=2))
    s.add(window(0, 0, 2000, label_idx=1))
    assert s.out_of_order == 1
    assert s.evidence_at(2500).window_seq == 0
    assert s.evidence_at(3000).window_seq == 1


def test_history_is_bounded():
    s = VoiceState(history=3)
    for i in range(10):
        s.add(window(i, i * 1000, i * 1000 + 2000))
    assert len(s) == 3


def test_overlap():
    assert overlap_ms(0, 3000, 1000, 4000) == 2000
    assert overlap_ms(0, 1000, 2000, 3000) == 0


def test_best_window_by_overlap():
    s = VoiceState()
    s.add(window(0, 0, 3000, label_idx=1))
    s.add(window(1, 1000, 4000, label_idx=2))
    s.add(window(2, 2000, 5000, speech=False))
    assert s.best_window(1000, 4000, 1000)["window_seq"] == 1
    assert s.best_window(1500, 4500, 1000)["window_seq"] == 1
    assert s.best_window(4500, 7500, 1000) is None
    assert s.best_window(0, 3000, 1000)["window_seq"] == 0


def test_latest_speech_window_respects_age():
    s = VoiceState()
    s.add(window(0, 0, 3000))
    s.add(window(1, 1000, 4000, speech=False))
    assert s.latest_speech_window(4000, 3000)["window_seq"] == 0
    assert s.latest_speech_window(7000, 3000) is None
    assert s.latest_speech_window(2000, 3000) is None
