"""Цикл агента с поддельной моделью: ручки настоящие (MCP-сервер в том же процессе), ответы модели — по сценарию."""

import asyncio
import json
import re

import httpx
import pytest

from src.affectra_mcp import AffectraTools
from src.affectra_mcp import SessionContext
from src.affectra_mcp import create_server
from src.agent import AgentLoop
from src.agent import LLMClient
from src.agent import LLMError
from src.agent import MCPBridge
from src.agent import analyze
from src.agent import prepare_run
from src.agent.config import AgentConfig
from src.agent.config import LLMConfig
from src.agent.config import RunConfig
from src.agent.llm import LLMResponse
from src.agent.llm import ToolCall
from src.agent.validate import validate_finding
from src.emotion_moments import EmotionSeries
from src.emotion_moments import load_profile

LABEL_RE = re.compile(r"\[(c\d+)\]")


class FakeLLM:
    """Модель-сценарий: policy(messages, tools, n) возвращает LLMResponse; n — номер вызова."""

    name = "fake-qwen"

    def __init__(self, policy):
        self.policy = policy
        self.calls  = []

    async def chat(self, messages, tools=None, response_format=None):
        self.calls.append([dict(m) for m in messages])
        return self.policy(messages, [t["function"]["name"] for t in tools or []], len(self.calls))


def call(name, args, i=0):
    return LLMResponse(tool_calls=[ToolCall(id=f"id{i}", name=name, arguments=json.dumps(args, ensure_ascii=False))])


def moment_span(messages):
    m = re.search(r"\((\d+)–(\d+) с\)", messages[1]["content"])
    return int(m.group(1)), int(m.group(2))


def labels_seen(messages):
    return [x for m in messages if m["role"] == "tool" for x in LABEL_RE.findall(m["content"])[:1]]


def finding(source="c0", verdict="attention", text="Похоже на устойчивую эмоцию."):
    return {"verdict": verdict, "confidence": "medium", "significance": "high", "summary": text,
            "explanation": "Вероятность держится выше порога весь интервал.",
            "evidence": [{"fact": "средняя вероятность 0.80", "source": source}], "operator_check": "Посмотрите момент."}


def good_policy(messages, tools, n):
    """Разумная модель: контекст момента, потом вердикт со ссылкой на свой вызов; итог — submit_summary."""
    if "submit_summary" in tools:
        return call("submit_summary", {"headline": "Два момента стоит посмотреть.", "summary": "Начните с гнева."})
    seen = labels_seen(messages)
    if not seen:
        start, end = moment_span(messages)
        return call("get_moment_context", {"start_s": start, "end_s": end})
    return call("submit_finding", finding(source=seen[-1]))


def make_run(series_doc, privacy="local", top_k=10):
    return prepare_run(series_doc, load_profile("interview"), privacy=privacy, top_k=top_k)


def run_loop(series_doc, policy, run_config=None, clock=None, privacy="local"):
    run   = make_run(series_doc, privacy)
    ctx   = SessionContext(series=EmotionSeries.from_document(series_doc), profile=load_profile("interview"),
                           privacy=privacy)
    llm   = FakeLLM(policy)

    async def go():
        async with MCPBridge(create_server(AffectraTools(ctx))) as bridge:
            loop = AgentLoop(llm, bridge, run_config or RunConfig(), **({"clock": clock} if clock else {}))
            await loop.run(run)
            return loop

    loop = asyncio.run(go())
    return run, loop, llm


def test_happy_path_findings_summary_and_trace(series_doc):
    run, loop, llm = run_loop(series_doc, good_policy)
    assert run.status == "done" and run.notes == [] and run.model == "fake-qwen"
    assert set(run.findings) == {m.id for m in run.moments}
    assert all(e.source.startswith("c") and e.source != "c0" for f in run.findings.values() for e in f.evidence)
    assert run.headline == "Два момента стоит посмотреть." and run.summary == "Начните с гнева."
    kinds = [e["kind"] for e in loop.trace.events]
    assert kinds[0] == "start" and kinds[-1] == "finish" and "tool" in kinds and "validation" in kinds
    system = llm.calls[0][0]["content"]
    assert "/no_think" in system and "Сценарий." in system and "не больше 4 вызовов" in system


def test_text_instead_of_tool_then_json_in_text(series_doc):
    def policy(messages, tools, n):
        if "submit_summary" in tools:
            return LLMResponse(content='Итог: {"headline": "Есть что посмотреть.", "summary": "Кратко."}')
        if messages[-1]["role"] == "user" and "Ответьте вызовом" in messages[-1]["content"]:
            return LLMResponse(content="Вот ответ: " + json.dumps(finding(), ensure_ascii=False))
        return LLMResponse(content="Думаю, это грусть.")

    run, _, _ = run_loop(series_doc, policy)
    assert run.status == "done" and len(run.findings) == len(run.moments)
    assert run.headline == "Есть что посмотреть."


def test_invalid_twice_leaves_moment_without_verdict(series_doc):
    def policy(messages, tools, n):
        if "submit_summary" in tools:
            return call("submit_summary", {"headline": "", "summary": ""})
        return call("submit_finding", finding(source="c99"))

    run, loop, _ = run_loop(series_doc, policy)
    assert run.findings == {} and run.status == "partial"
    assert any("не дала проверяемого вердикта" in n for n in run.notes)
    errors = [e for e in loop.trace.events if e["kind"] == "validation" and not e["ok"]]
    assert errors and "c99" in errors[0]["errors"][0]


def test_repeated_calls_are_not_executed_and_budget_forces_submit(series_doc):
    def policy(messages, tools, n):
        if "submit_summary" in tools:
            return call("submit_summary", {"headline": "Итог.", "summary": "Кратко."})
        if any("Бюджет вызовов исчерпан" in (m.get("content") or "") for m in messages):
            return call("submit_finding", finding(source=labels_seen(messages)[0]))
        tries = sum(1 for m in messages if m["role"] == "tool")
        if tries < 2:
            return call("get_session_overview", {}, tries)
        return call("get_emotion_series", {"from_s": tries, "to_s": tries + 20}, tries)

    run, loop, _ = run_loop(series_doc, policy, RunConfig(max_tool_calls=3, max_turns=8))
    assert run.status == "done" and len(run.findings) == len(run.moments)
    repeated = [e for e in loop.trace.events if e["kind"] == "tool" and e["repeated"]]
    budget   = [e for e in loop.trace.events if e["kind"] == "budget" and e["reason"] == "tool_calls"]
    assert repeated and budget


def test_llm_unavailable_gives_partial_run(series_doc):
    def policy(messages, tools, n):
        raise LLMError("модель недоступна (http://pc:11434/v1): connection refused")

    run, _, _ = run_loop(series_doc, policy)
    assert run.status == "partial" and run.findings == {} and run.headline is None
    assert run.notes[0].startswith("модель недоступна")


def test_time_budget_stops_remaining_moments(series_doc):
    answers = []

    def policy(messages, tools, n):
        answers.append(n)
        return good_policy(messages, tools, n)

    def clock():
        return 0 if len(answers) < 2 else 10_000  # время «кончается» после первого момента

    run, _, _ = run_loop(series_doc, policy, RunConfig(time_budget_s=900), clock=clock)
    assert run.status == "partial" and 0 < len(run.findings) < len(run.moments)
    assert any("исчерпано время" in n for n in run.notes)


def test_external_privacy_keeps_identity_out_of_prompts(series_doc):
    _, loop, llm = run_loop(series_doc, good_policy, privacy="external")
    text = json.dumps(llm.calls, ensure_ascii=False)
    assert "upload_ab12cd" not in text and "face:u1" not in text


def test_validate_finding_rules(series_doc):
    m = make_run(series_doc).moments[0]
    ok, errors = validate_finding(finding(), m, ["c0"])
    assert ok is not None and errors == []
    bad = dict(finding(), verdict="suspicious", confidence="sure", summary="", evidence=[])
    _, errors = validate_finding(bad, m, ["c0"])
    assert len(errors) == 3
    _, errors = validate_finding(dict(finding(), evidence=[]), m, ["c0"])
    assert errors == ["для этого вердикта нужно хотя бы одно доказательство с меткой источника"]
    far = dict(finding(), operator_check="Посмотрите 59:00.")
    assert validate_finding(far, m, ["c0"])[0] is None
    near = dict(finding(), operator_check=f"Посмотрите {int(m.start_s) // 60:02d}:{int(m.start_s) % 60:02d}.")
    assert validate_finding(near, m, ["c0"])[0] is not None


def test_llm_client_parses_tool_calls_and_retries():
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            return httpx.Response(503, text="loading")
        return httpx.Response(200, json={"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "x1", "type": "function", "function": {"name": "get_profile", "arguments": {}}}]}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 9}})

    async def go():
        client = LLMClient(LLMConfig(model="qwen3:4b", extra_body={"think": False}),
                           transport=httpx.MockTransport(handler))
        try:
            return await client.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
        finally:
            await client.close()

    resp = asyncio.run(go())
    assert resp.tool_calls[0].name == "get_profile" and resp.tool_calls[0].parsed() == {}
    assert resp.usage["prompt_tokens"] == 120 and len(attempts) == 2
    body = attempts[-1]
    assert body["model"] == "qwen3:4b" and body["think"] is False and body["seed"] == 7 and body["tools"]


def test_llm_client_unreachable_raises():
    def handler(request):
        raise httpx.ConnectError("refused")

    async def go():
        client = LLMClient(LLMConfig(), retries=1, transport=httpx.MockTransport(handler))
        try:
            await client.chat([{"role": "user", "content": "hi"}])
        finally:
            await client.close()

    with pytest.raises(LLMError, match="недоступна"):
        asyncio.run(go())


def test_analyze_writes_run_files(series_doc, tmp_path):
    path_series = tmp_path / "series.json"
    path_series.write_text(json.dumps(series_doc), encoding="utf-8")
    ctx = SessionContext(series=EmotionSeries.from_document(series_doc), profile=load_profile("interview"))
    run, path = asyncio.run(analyze(AgentConfig(), series_path=str(path_series), out_dir=tmp_path / "runs",
                                    llm=FakeLLM(good_policy), bridge_server=create_server(AffectraTools(ctx))))
    files = {p.name for p in path.iterdir()}
    assert files == {"report.html", "findings.json", "run.json", "trace.jsonl"}
    meta = json.loads((path / "run.json").read_text(encoding="utf-8"))
    assert meta["status"] == "done" and meta["model"] == "fake-qwen" and meta["verdicts"] == {"attention": len(run.moments)}
    html = (path / "report.html").read_text(encoding="utf-8")
    assert "Два момента стоит посмотреть." in html and "Без объяснения модели" not in html

    plain, plain_path = asyncio.run(analyze(AgentConfig(), series_path=str(path_series), use_llm=False,
                                            out_dir=tmp_path / "runs"))
    assert plain.model is None and "trace.jsonl" not in {p.name for p in plain_path.iterdir()}


def test_bridge_spawns_mcp_server_over_stdio(series_doc, tmp_path):
    from src.agent.mcp_bridge import server_params

    path = tmp_path / "series.json"
    path.write_text(json.dumps(series_doc), encoding="utf-8")

    async def go():
        async with MCPBridge(server_params(series_path=str(path), privacy="external")) as bridge:
            first  = await bridge.call("detect_moments", {})
            again  = await bridge.call("detect_moments", {})
            wrong  = await bridge.call("drop_table", {})
            return bridge.tool_names(), first, again, wrong

    names, first, again, wrong = asyncio.run(go())
    assert "get_moment_context" in names and first.label == "c1" and "устойчивая грусть" in first.text
    assert again.repeated and again.label == "c1" and wrong.is_error and wrong.label == "c2"
