"""Настройки агента: agent.yaml в корне проекта и переопределения из переменных окружения AGENT_*."""

import os
import yaml

from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Optional

CONFIG_PATH = Path(os.getenv("AGENT_CONFIG", Path(__file__).resolve().parents[2] / "agent.yaml"))


@dataclass
class LLMConfig:
    """Сервер модели с OpenAI-совместимым API."""
    base_url:    str = "http://localhost:11434/v1"
    model:       str = "qwen3:4b"
    api_key:     str = "ollama"
    temperature: float = 0.2
    seed:        Optional[int] = 7
    max_tokens:  int = 900
    timeout_s:   float = 180.0
    no_think:    bool = True
    extra_body:  Dict[str, Any] = field(default_factory=dict)


@dataclass
class RunConfig:
    """Параметры разбора: профили и бюджеты."""
    privacy:        str = "local"
    profile:        str = "interview"
    top_k:          int = 10
    max_tool_calls: int = 4
    max_turns:      int = 8
    time_budget_s:  float = 900.0
    output_dir:     str = "data/agent_runs"


@dataclass
class AgentConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    run: RunConfig = field(default_factory=RunConfig)


def _section(cls, data: Optional[Dict[str, Any]]):
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in (data or {}).items() if k in known})


def load_config(path: Optional[Path] = None) -> AgentConfig:
    """
    Настройки из YAML (если файл есть) с переопределениями из окружения.

    Args:
        path (Path, optional): Путь к agent.yaml. Defaults to CONFIG_PATH.

    Returns:
        AgentConfig: Настройки.
    """
    path = Path(path or CONFIG_PATH)
    data: Dict[str, Any] = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    cfg = AgentConfig(llm=_section(LLMConfig, data.get("llm")), run=_section(RunConfig, data.get("run")))
    for env, section, name in (("AGENT_LLM_BASE_URL", cfg.llm, "base_url"), ("AGENT_LLM_MODEL", cfg.llm, "model"),
                               ("AGENT_LLM_API_KEY", cfg.llm, "api_key"), ("AGENT_PRIVACY", cfg.run, "privacy"),
                               ("AGENT_PROFILE", cfg.run, "profile")):
        if os.getenv(env):
            setattr(section, name, os.getenv(env))
    return cfg
