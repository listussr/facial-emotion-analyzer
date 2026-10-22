import csv
import io

from datetime import datetime
from datetime import timezone

from src.emotion_labels import EMOTION_LABELS
from src.web_server.routes.history import _csv_iter_for_tracks
from src.web_server.routes.history import _row_to_track

CREATED = datetime(2026, 10, 1, tzinfo=timezone.utc)

LEGACY_ROW = {
    "user_id": "u1", "created_at": CREATED,
    "data": {"camera_id": "cam", "track_id": 1,
             "samples": [{"t": 1.0, "label": "surprise", "scores": [0, 0, 0, 0, 0, 0, 0.1, 0.9]}]},
}
NEW_ROW = {
    "user_id": "u1", "created_at": CREATED, "modality": "fused", "label_set": "crema-d-6",
    "data": {"camera_id": "cam", "track_id": 2, "modality": "fused", "labels": list(EMOTION_LABELS),
             "samples": [{"t": 2.0, "label": "happy", "scores": [0, 0, 0, 0.7, 0.3, 0]}]},
}


def read_csv(rows):
    return list(csv.DictReader(io.StringIO("".join(_csv_iter_for_tracks(rows)))))


def test_csv_mixed_legacy_and_new_records():
    legacy, new = read_csv([LEGACY_ROW, NEW_ROW])
    assert list(legacy)[-8:] == list(EMOTION_LABELS) + ["contempt", "surprise"]
    assert legacy["modality"] == "face" and legacy["label_set"] == "legacy-8"
    assert legacy["surprise"] == "0.9" and legacy["sad"] == "0.1" and legacy["contempt"] == "0.0"
    assert new["modality"] == "fused" and new["happy"] == "0.7" and new["neutral"] == "0.3"
    assert new["contempt"] == "" and new["surprise"] == ""


def test_csv_only_new_records_has_six_columns():
    [row] = read_csv([NEW_ROW])
    assert list(row)[-6:] == list(EMOTION_LABELS)
    assert "contempt" not in row


def test_track_history_carries_labels():
    legacy = _row_to_track(LEGACY_ROW)
    assert legacy.modality == "face" and legacy.label_set == "legacy-8" and len(legacy.labels) == 8
    new = _row_to_track(NEW_ROW)
    assert new.modality == "fused" and new.labels == list(EMOTION_LABELS)
