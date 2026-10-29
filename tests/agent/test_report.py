"""Отчёт разбора: без модели (только детекторы) и с вердиктами модели."""

from src.agent import Evidence
from src.agent import Finding
from src.agent import build_report
from src.agent import prepare_run
from src.emotion_moments import load_profile
from tests.agent.conftest import make_series_doc

META = {"camera_id": "upload_ab12cd", "name": "Опрос Иванова", "started_at": "2026-10-24T14:05:00+00:00"}


def series_doc() -> dict:
    return make_series_doc()


def test_detectors_only_report():
    run  = prepare_run(series_doc(), load_profile("interview"), meta=META)
    html = build_report(run)
    assert len(run.moments) >= 2 and run.session_id == "upload_ab12cd"
    assert "Опрос Иванова" in html and "Без объяснения модели" in html and "не использовалась" in html
    assert html.count("<svg") == 1 + len(run.split()[0]) and "устойчивая грусть" in html
    assert "/recordings/upload_ab12cd?t=" in html and "trace.jsonl" not in html
    doc = run.findings_document()
    assert doc["model"] is None and all(m["finding"] is None for m in doc["moments"])


def test_external_privacy_hides_name_in_report():
    run = prepare_run(series_doc(), load_profile("interview"), meta=META, privacy="external")
    assert "Опрос Иванова" not in build_report(run)


def test_findings_order_cards_and_texts():
    run = prepare_run(series_doc(), load_profile("interview"), meta=META)
    first, second = sorted(run.moments, key=lambda m: m.start_s)[:2]
    run.model    = "qwen-test"
    run.headline = "Один момент стоит посмотреть."
    run.findings = {
        first.id: Finding(first.id, "not_significant", "high", "low", "Короткий эпизод.", "Нейтрально.", []),
        second.id: Finding(second.id, "attention", "medium", "high", "Устойчивый гнев около 20 с.",
                           "Вероятность гнева держится выше 0.7.", [Evidence("средняя 0.80", "get_emotion_series")],
                           "Посмотрите 02:20–02:40."),
    }
    cards, rest = run.split()
    assert cards[0].id == second.id and first.id in [m.id for m in rest]
    html = build_report(run)
    assert "Один момент стоит посмотреть." in html and "Устойчивый гнев около 20 с." in html
    assert "средняя 0.80" in html and "стоит посмотреть — 1" in html and "trace.jsonl" in html
    assert run.findings_document()["moments"][0]["finding"]["verdict"] in ("not_significant", "attention")
