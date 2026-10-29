"""Мост агента к MCP-серверу affectra-data: ручки как инструменты модели, вызовы с метками c1, c2, ..."""

import json
import os
import sys

from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from mcp import Client
from mcp import StdioServerParameters

REPO = Path(__file__).resolve().parents[2]


@dataclass
class ToolResult:
    """Результат вызова ручки; label — метка, по которой модель ссылается на него в доказательствах."""
    label:    str
    name:     str
    args:     Dict[str, Any]
    text:     str
    is_error: bool = False
    repeated: bool = False


def server_params(session_id: Optional[str] = None, series_path: Optional[str] = None, privacy: str = "local",
                  profile: str = "interview", env: Optional[Dict[str, str]] = None) -> StdioServerParameters:
    """Запуск mcp_server.py дочерним процессом для одного разбора."""
    args = [str(REPO / "mcp_server.py"), "--privacy", privacy, "--profile", profile]
    args += ["--session", session_id] if session_id else ["--series", str(series_path)]
    return StdioServerParameters(command=sys.executable, args=args, cwd=str(REPO),
                                 env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})})


class MCPBridge:
    """
    Подключение к MCP-серверу на время разбора.

    Вызовы получают сквозные метки c1, c2, ...; метка c0 зарезервирована за данными детекторов, которые агент
    даёт сам. Повтор вызова с теми же аргументами в пределах одного момента (begin_scope) не исполняется —
    возвращается ссылка на прежний результат: у каждого момента свой диалог с моделью.

    Args:
        server: StdioServerParameters (дочерний процесс) или MCPServer (в том же процессе, для тестов).
    """

    def __init__(self, server: Any):
        self.server  = server
        self.results: List[ToolResult] = []
        self._client: Optional[Client] = None
        self._stack  = AsyncExitStack()
        self._scope  = 0
        self._tools: List[Dict[str, Any]] = []

    async def __aenter__(self) -> "MCPBridge":
        self._client = await self._stack.enter_async_context(Client(self.server))
        listed       = await self._client.list_tools()
        self._tools  = [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                                                           "parameters": t.input_schema}} for t in listed.tools]
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    @property
    def tools(self) -> List[Dict[str, Any]]:
        """Ручки в формате инструментов OpenAI."""
        return list(self._tools)

    def tool_names(self) -> List[str]:
        return [t["function"]["name"] for t in self._tools]

    def labels(self) -> List[str]:
        return [r.label for r in self.results]

    def begin_scope(self) -> None:
        """Начало разбора нового момента: повторы ищутся только среди его вызовов."""
        self._scope = len(self.results)

    def scope_labels(self) -> List[str]:
        return [r.label for r in self.results[self._scope:]]

    async def call(self, name: str, args: Dict[str, Any]) -> ToolResult:
        """
        Вызов ручки.

        Args:
            name (str)           : Имя ручки.
            args (Dict[str, Any]): Аргументы.

        Returns:
            ToolResult: Результат с меткой; для повтора — repeated=True и метка прежнего вызова.
        """
        key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
        for r in self.results[self._scope:]:
            if (r.name, json.dumps(r.args, sort_keys=True, ensure_ascii=False)) == key:
                return ToolResult(label=r.label, name=name, args=args, repeated=True,
                                  text=f"этот вызов уже был — см. результат [{r.label}]; не повторяйте его",
                                  is_error=r.is_error)
        label = f"c{len(self.results) + 1}"
        if name not in self.tool_names():
            result = ToolResult(label, name, args, f"нет ручки {name}; есть: {', '.join(self.tool_names())}", True)
        else:
            res    = await self._client.call_tool(name, args)
            text   = "\n".join(getattr(c, "text", "") for c in res.content) or "(пустой ответ)"
            result = ToolResult(label, name, args, text, bool(res.is_error))
        self.results.append(result)
        return result
