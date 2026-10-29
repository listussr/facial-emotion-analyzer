"""MCP-сервер affectra-data: ручки напрямую и через клиент MCP в том же процессе."""

import asyncio
import numpy as np
import pytest

from mcp import Client

from src.affectra_mcp import TOOL_NAMES
from src.affectra_mcp import AffectraTools
from src.affectra_mcp import RecordingSource
from src.affectra_mcp import SessionContext
from src.affectra_mcp import ToolError
from src.affectra_mcp import create_server
from src.affectra_mcp.formatting import pick_step
from src.emotion_labels import EMOTION_LABELS
from src.emotion_moments import EmotionSeries
from src.emotion_moments import load_profile

META = {"camera_id": "upload_ab12cd", "name": "Опрос Иванова", "started_at": "2026-10-24T14:05:00+00:00"}


def dist(main: int, p: float = 0.8) -> list:
    v = [(1 - p) / 5] * 6
    v[main] = p
    return v


def doc(n: int = 200) -> dict:
    fused = [dist(4)] * 60 + [dist(5)] * 40 + [None] * 10 + [dist(4)] * (n - 110)
    voice = [None] * 50 + [dist(5)] * 30 + [None] * (n - 80)
    return {"camera_id": "upload_ab12cd", "labels": list(EMOTION_LABELS), "step_s": 1.0, "t": list(range(n)),
            "fused": fused, "face": fused, "voice": voice,
            "speech": [0.0] * 50 + [1.0] * 30 + [0.0] * (n - 80),
            "face_present": [1.0] * 100 + [0.0] * 10 + [1.0] * (n - 110),
            "predicted": [0.0] * n, "others": [0.0] * n, "subject": {"people": ["face:3f2a-uuid"], "tracks": [1]}}


def tools(privacy: str = "local") -> AffectraTools:
    return AffectraTools(SessionContext(series=EmotionSeries.from_document(doc()), profile=load_profile("interview"),
                                        privacy=privacy, meta=META))


def test_pick_step_limits_points():
    assert pick_step(60, 1, None, 120) == 1 and pick_step(3600, 1, None, 120) == 30
    assert pick_step(100, 1, 7, 120) == 10


def test_overview_hides_identity_in_external():
    local, external = tools("local").get_session_overview(), tools("external").get_session_overview()
    assert "Опрос Иванова" in local and "face:3f2a-uuid" in local
    for secret in ("Опрос", "upload_ab12cd", "3f2a", "2026"):
        assert secret not in external
    assert "03:20" in external and "речь 15%" in external


def test_series_table_is_compact_and_bounded():
    text = tools().get_emotion_series(0, 200)
    lines = text.splitlines()
    assert lines[1].startswith("t_s,гнев") and len(lines) - 2 <= 120 and "шаг 2 с" in lines[0]
    gap = tools().get_emotion_series(100, 110)
    assert ",,,,,,0.00" in gap
    with pytest.raises(ToolError):
        tools().get_emotion_series(150, 100)
    with pytest.raises(ToolError):
        tools().get_emotion_series(modality="audio")


def test_detectors_and_context():
    t = tools()
    moments = t.detect_moments()
    assert "устойчивая грусть" in moments and "m1" in moments
    assert "01:00–01:40" in t.find_sustained("sad", 0.6, 8)
    assert "нейтр" in t.find_abrupt_changes() and "→ грусть" in t.find_abrupt_changes()
    ctx = t.get_moment_context(60, 100, pad_s=15)
    assert "до 00:45–01:00" in ctx and "голос: грусть" in ctx and "после 01:40–01:55" in ctx and "лицо 33%" in ctx
    assert "речи нет" in t.get_voice_windows(100, 200)
    with pytest.raises(ToolError):
        t.find_sustained("rage")


def test_mcp_client_lists_and_calls_tools():
    async def run():
        async with Client(create_server(tools("external"))) as client:
            listed = await client.list_tools()
            result = await client.call_tool("get_emotion_series", {"from_s": 60, "to_s": 70})
            error  = await client.call_tool("find_sustained", {"emotion": "rage"})
            return listed, result, error

    listed, result, error = asyncio.run(run())
    names = [t.name for t in listed.tools]
    assert names == TOOL_NAMES
    schema = next(t for t in listed.tools if t.name == "get_emotion_series").input_schema
    assert set(schema["properties"]) == {"from_s", "to_s", "modality", "step_s"}
    assert "грусть" in result.content[0].text and not result.is_error
    assert error.is_error and "emotion" in error.content[0].text


def test_recording_source_reads_series_and_meta():
    class Body:
        def read(self):
            return b'{"camera_id": "s1", "t": []}'

    class Storage:
        def get(self, key):
            assert key == "sessions/s1/series.json"
            return {"Body": Body()}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def execute(self, sql, params):
            assert "reader.sessions" in sql and params == ("s1",)

        def fetchone(self):
            return ("s1", "Опрос", "upload", "ready", None, None, 12.5)

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    series, meta = RecordingSource(Storage(), lambda: Conn()).load("s1")
    assert series["camera_id"] == "s1" and meta["name"] == "Опрос" and meta["duration_sec"] == 12.5
    assert RecordingSource(Storage()).meta("s1") is None


def test_unknown_privacy_rejected():
    with pytest.raises(ValueError):
        AffectraTools(SessionContext(series=EmotionSeries.from_document(doc(10)), profile=load_profile("interview"),
                                     privacy="public"))
    assert np.isnan(EmotionSeries.from_document(doc(120)).fused[105, 0])
