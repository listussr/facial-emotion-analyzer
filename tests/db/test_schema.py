from src.db_managing import LEGACY_LABEL_SET
from src.db_managing import LEGACY_LABELS
from src.db_managing import MODALITIES
from src.db_managing import csv_label_columns
from src.db_managing import label_set_of
from src.db_managing import labels_for
from src.db_managing import scores_by_label
from src.db_managing.schema import MIGRATIONS
from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import LABEL_SET


def test_labels_of_new_and_legacy_records():
    assert labels_for({"labels": list(EMOTION_LABELS)}) == EMOTION_LABELS
    assert labels_for({}) == LEGACY_LABELS
    assert labels_for({"labels": []}) == LEGACY_LABELS


def test_label_set_of():
    assert label_set_of({"label_set": "x"}) == "x"
    assert label_set_of({"labels": ["a"]}) == LABEL_SET
    assert label_set_of({}) == LEGACY_LABEL_SET


def test_scores_by_label_uses_record_order():
    legacy = scores_by_label(LEGACY_LABELS, [0.1, 0.2, 0.3, 0, 0, 0, 0, 0.4])
    assert legacy["contempt"] == 0.2 and legacy["surprise"] == 0.4
    new = scores_by_label(EMOTION_LABELS, [0.5, 0.1, 0, 0, 0.4, 0])
    assert new["disgust"] == 0.1 and new["neutral"] == 0.4


def test_csv_columns_current_first_then_legacy_extras():
    assert csv_label_columns([EMOTION_LABELS]) == list(EMOTION_LABELS)
    cols = csv_label_columns([EMOTION_LABELS, LEGACY_LABELS])
    assert cols[:6] == list(EMOTION_LABELS) and cols[6:] == ["contempt", "surprise"]


def test_migrations_are_idempotent_sql():
    assert MODALITIES == ("face", "fused")
    for name, sql in MIGRATIONS:
        assert "IF NOT EXISTS" in sql, name
