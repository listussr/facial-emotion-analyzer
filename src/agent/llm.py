"""Клиент модели с OpenAI-совместимым API (Ollama, llama.cpp, vLLM, внешний): чат с инструментами."""

import asyncio
import json
import time

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import httpx

from .config import LLMConfig


class LLMError(RuntimeError):
    """Модель недоступна или ответила не по протоколу."""


@dataclass
class ToolCall:
    id:        str
    name:      str
    arguments: str

    def parsed(self) -> Dict[str, Any]:
        """Аргументы вызова; ValueError — не JSON-объект."""
        data = json.loads(self.arguments or "{}")
        if not isinstance(data, dict):
            raise ValueError("arguments must be a JSON object")
        return data


@dataclass
class LLMResponse:
    content:    str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage:      Dict[str, int] = field(default_factory=dict)
    elapsed_s:  float = 0.0

    def assistant_message(self) -> Dict[str, Any]:
        """Сообщение ассистента для продолжения диалога (с вызовами инструментов, если были)."""
        msg: Dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.name, "arguments": c.arguments}} for c in self.tool_calls]
        return msg


class LLMClient:
    """
    Чат с моделью через /chat/completions.

    Сбои сети и ответы 5xx повторяются retries раз с паузой; остальное — LLMError.
    """

    def __init__(self, config: LLMConfig, retries: int = 1, transport: Optional[httpx.AsyncBaseTransport] = None):
        self.config  = config
        self.retries = retries
        self._client = httpx.AsyncClient(base_url=config.base_url.rstrip("/"), timeout=config.timeout_s,
                                         headers={"Authorization": f"Bearer {config.api_key}"}, transport=transport)

    @property
    def name(self) -> str:
        return self.config.model

    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None,
                   response_format: Optional[Dict[str, Any]] = None) -> LLMResponse:
        """
        Один ответ модели.

        Args:
            messages        (List[Dict])          : Диалог в формате OpenAI.
            tools           (List[Dict], optional): Инструменты в формате OpenAI. Defaults to None.
            response_format (Dict, optional)      : Ограничение ответа (json_schema). Defaults to None.

        Raises:
            LLMError: Модель недоступна или ответ не разобрать.

        Returns:
            LLMResponse: Текст, вызовы инструментов, расход токенов, время.
        """
        c    = self.config
        body = {"model": c.model, "messages": messages, "temperature": c.temperature, "max_tokens": c.max_tokens,
                **({"seed": c.seed} if c.seed is not None else {}), **c.extra_body}
        if tools:
            body["tools"] = tools
        if response_format:
            body["response_format"] = response_format
        started = time.monotonic()
        for attempt in range(self.retries + 1):
            try:
                r = await self._client.post("/chat/completions", json=body)
                if r.status_code >= 500 and attempt < self.retries:
                    await asyncio.sleep(1 + attempt)
                    continue
                r.raise_for_status()
                return self._parse(r.json(), time.monotonic() - started)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                if attempt >= self.retries:
                    raise LLMError(f"модель недоступна ({c.base_url}): {e}") from e
                await asyncio.sleep(1 + attempt)
            except httpx.HTTPStatusError as e:
                raise LLMError(f"модель ответила {e.response.status_code}: {e.response.text[:300]}") from e
        raise LLMError("модель недоступна")

    @staticmethod
    def _parse(data: Dict[str, Any], elapsed: float) -> LLMResponse:
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"ответ без choices: {str(data)[:300]}") from e
        calls = []
        for i, c in enumerate(msg.get("tool_calls") or []):
            fn   = c.get("function") or {}
            args = fn.get("arguments")
            calls.append(ToolCall(id=c.get("id") or f"call_{i}", name=fn.get("name", ""),
                                  arguments=args if isinstance(args, str) else json.dumps(args or {}, ensure_ascii=False)))
        return LLMResponse(content=msg.get("content") or "", tool_calls=calls, usage=data.get("usage") or {},
                           elapsed_s=round(elapsed, 3))

    async def close(self) -> None:
        await self._client.aclose()
