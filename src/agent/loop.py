"""
Цикл агента (фазы 2–4 docs/AGENT.md): разбор каждого момента моделью с ручками, итог сессии, проверка кодом.

Ход определяет код, модель работает внутри коротких ограниченных шагов. У каждого момента свой диалог:
не больше max_tool_calls вызовов ручек, затем submit_finding; ответ проверяется, при ошибке модель один раз
получает её текст. Момент без принятого вердикта остаётся «без оценки модели». Отчёт получается всегда.
"""

import json
import re
import time

from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from src.emotion_moments import Moment
from .config import RunConfig
from .findings import Finding
from .findings import RunData
from .llm import LLMClient
from .llm import LLMError
from .llm import ToolCall
from .mcp_bridge import MCPBridge
from .prompts import NUDGE_SUBMIT
from .prompts import NUDGE_TOOL
from .prompts import RETRY_INVALID
from .prompts import SUBMIT_FINDING
from .prompts import SUBMIT_SUMMARY
from .prompts import SUMMARY_SYSTEM
from .prompts import moment_prompt
from .prompts import summary_prompt
from .prompts import system_prompt
from .report import detector_lines
from .report import mmss
from .trace import Trace
from .validate import validate_finding
from .validate import validate_summary

JSON_RE = re.compile(r"\{.*\}", re.S)


def json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Запасной режим: JSON-объект из текста ответа (модель не вызвала инструмент, а написала JSON)."""
    match = JSON_RE.search(text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("arguments"), dict):
        data = data["arguments"]
    return data if isinstance(data, dict) else None


class AgentLoop:
    """
    Разбор моментов RunData моделью.

    Args:
        llm      (LLMClient)          : Клиент модели (или объект с тем же async chat).
        bridge   (MCPBridge)          : Открытое подключение к MCP-серверу разбора.
        config   (RunConfig)          : Бюджеты.
        no_think (bool, optional)     : Пометка /no_think (Qwen) в системных промптах. Defaults to True.
        trace    (Trace, optional)    : Журнал разбора. Defaults to None — новый.
        clock    (Callable, optional) : Источник времени для бюджета. Defaults to time.monotonic.
    """

    def __init__(self, llm: LLMClient, bridge: MCPBridge, config: RunConfig, no_think: bool = True,
                 trace: Optional[Trace] = None, clock=time.monotonic):
        self.llm             = llm
        self.bridge          = bridge
        self.config          = config
        self.no_think        = no_think
        self.trace           = trace or Trace()
        self.clock           = clock
        self._deadline       = 0.0
        self._overview_label = ""
        self._incomplete     = False

    async def run(self, run: RunData) -> RunData:
        """
        Вердикты по первым top_k моментам и итог; статус done или partial, замечания — в run.notes.

        Args:
            run (RunData): Подготовленный разбор (prepare_run).

        Returns:
            RunData: Тот же разбор с вердиктами, текстами модели и статусом.
        """
        self._deadline       = self.clock() + self.config.time_budget_s
        run.model            = self.llm.name
        system               = system_prompt(run.profile, self.config.max_tool_calls, self.no_think)
        overview             = await self.bridge.call("get_session_overview", {})
        self._overview_label = overview.label
        self.trace.add("start", run_id=run.run_id, session=run.session_id, model=run.model, privacy=run.privacy,
                       profile=run.profile.name, moments=len(run.moments), system=system, overview=overview.text)

        selected = sorted(run.moments, key=lambda m: -m.significance)[:run.top_k]
        for i, m in enumerate(selected):
            if self.clock() > self._deadline:
                run.notes.append(f"исчерпано время разбора ({self.config.time_budget_s:.0f} с): "
                                 f"{len(selected) - i} моментов без оценки модели")
                self._incomplete = True
                break
            try:
                finding = await self._moment(m, system, overview.text, overview.label)
            except LLMError as e:
                run.notes.append(f"модель недоступна: {e}")
                self.trace.add("llm_error", moment=m.id, error=str(e))
                self._incomplete = True
                break
            if finding is not None:
                run.findings[m.id] = finding
            else:
                run.notes.append(f"момент {m.id}: модель не дала проверяемого вердикта")
                self._incomplete = True

        if run.findings and not any(n.startswith("модель недоступна") for n in run.notes):
            try:
                await self._summary(run, overview.text)
            except LLMError as e:
                run.notes.append(f"итог без модели: {e}")
                self.trace.add("llm_error", phase="summary", error=str(e))
        run.status      = "partial" if self._incomplete else "done"
        run.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.trace.add("finish", status=run.status, notes=run.notes, findings=len(run.findings),
                       usage=self.trace.usage())
        return run

    async def _moment(self, m: Moment, system: str, overview: str, overview_label: str) -> Optional[Finding]:
        self.bridge.begin_scope()
        interval = f"{mmss(m.start_s)}–{mmss(m.end_s)}"
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": moment_prompt(m, overview, overview_label, detector_lines(m), interval)},
        ]
        tools    = self.bridge.tools + [SUBMIT_FINDING]
        calls    = 0
        retried  = False
        self.trace.add("moment", moment=m.id, interval=interval, types=m.types, prompt=messages[1]["content"])

        for _ in range(self.config.max_turns):
            if self.clock() > self._deadline:
                self.trace.add("budget", moment=m.id, reason="time")
                return None
            resp = await self.llm.chat(messages, tools=tools)
            self.trace.add("llm", moment=m.id, content=resp.content,
                           tool_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments} for c in resp.tool_calls],
                           usage=resp.usage, elapsed_s=resp.elapsed_s)
            messages.append(resp.assistant_message())

            if not resp.tool_calls:
                args = json_from_text(resp.content)
                if args is None:
                    messages.append({"role": "user", "content": NUDGE_TOOL})
                    continue
                finding, errors = self._check(args, m)
                if finding is not None:
                    return finding
                if retried:
                    return None
                retried = True
                messages.append({"role": "user", "content": RETRY_INVALID.format(errors="; ".join(errors))})
                continue

            for call in resp.tool_calls:
                if call.name == "submit_finding":
                    finding, errors = self._submit(call, m)
                    if finding is not None:
                        return finding
                    if retried:
                        return None
                    retried = True
                    messages.append(self._tool_message(call, RETRY_INVALID.format(errors="; ".join(errors))))
                elif calls >= self.config.max_tool_calls:
                    messages.append(self._tool_message(call, NUDGE_SUBMIT))
                    self.trace.add("budget", moment=m.id, reason="tool_calls", tool=call.name)
                else:
                    messages.append(await self._execute(call, m))
                    calls += 1
        self.trace.add("budget", moment=m.id, reason="turns")
        return None

    async def _execute(self, call: ToolCall, m: Moment) -> Dict[str, Any]:
        try:
            args = call.parsed()
        except ValueError as e:
            self.trace.add("tool_error", moment=m.id, tool=call.name, error=str(e))
            return self._tool_message(call, f"аргументы не JSON-объект: {e}")
        result = await self.bridge.call(call.name, args)
        self.trace.add("tool", moment=m.id, label=result.label, tool=call.name, args=args, result=result.text,
                       is_error=result.is_error, repeated=result.repeated)
        prefix = "" if result.repeated else f"[{result.label}]{' ошибка' if result.is_error else ''}\n"
        return self._tool_message(call, prefix + result.text)

    def _submit(self, call: ToolCall, m: Moment):
        try:
            args = call.parsed()
        except ValueError as e:
            self.trace.add("validation", moment=m.id, ok=False, errors=[f"аргументы не JSON: {e}"])
            return None, [f"аргументы не JSON-объект: {e}"]
        return self._check(args, m)

    def _check(self, args: Dict[str, Any], m: Moment):
        labels          = ["c0", self._overview_label, *self.bridge.scope_labels()]
        finding, errors = validate_finding(args, m, labels)
        self.trace.add("validation", moment=m.id, ok=finding is not None, errors=errors, finding=args)
        return finding, errors

    @staticmethod
    def _tool_message(call: ToolCall, text: str) -> Dict[str, Any]:
        return {"role": "tool", "tool_call_id": call.id, "content": text}

    async def _summary(self, run: RunData, overview: str) -> None:
        rows = []
        for m in sorted(run.moments, key=lambda m: m.start_s):
            f = run.findings.get(m.id)
            if f is not None:
                rows.append({"interval": f"{mmss(m.start_s)}–{mmss(m.end_s)}", "types": ", ".join(m.titles()),
                             "verdict": f.verdict, "confidence": f.confidence, "summary": f.summary})
        messages = [{"role": "system", "content": SUMMARY_SYSTEM + ("\n/no_think" if self.no_think else "")},
                    {"role": "user", "content": summary_prompt(overview, rows)}]
        for attempt in range(2):
            resp = await self.llm.chat(messages, tools=[SUBMIT_SUMMARY])
            self.trace.add("llm", phase="summary", content=resp.content,
                           tool_calls=[{"name": c.name, "arguments": c.arguments} for c in resp.tool_calls],
                           usage=resp.usage, elapsed_s=resp.elapsed_s)
            call = next((c for c in resp.tool_calls if c.name == "submit_summary"), None)
            try:
                args = call.parsed() if call else json_from_text(resp.content)
            except ValueError:
                args = None
            result, errors = validate_summary(args or {})
            self.trace.add("validation", phase="summary", ok=result is not None, errors=errors)
            if result is not None:
                run.headline, run.summary = result["headline"], result["summary"]
                return
            messages.append(resp.assistant_message())
            if call is not None:
                messages.append(self._tool_message(call, f"Ошибка: {'; '.join(errors)}. Вызовите submit_summary снова."))
            else:
                messages.append({"role": "user", "content": "Ответьте вызовом submit_summary с headline и summary."})
        run.notes.append("итог сессии — шаблонный: модель не дала проверяемого текста")
