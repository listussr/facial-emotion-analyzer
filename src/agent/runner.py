"""Разбор сессии целиком: данные (только чтение) → детекторы → модель с ручками MCP → файлы разбора."""

import json
import logging

from pathlib import Path
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

from src.emotion_moments import load_profile
from .config import AgentConfig
from .findings import RunData
from .llm import LLMClient
from .loop import AgentLoop
from .mcp_bridge import MCPBridge
from .mcp_bridge import server_params
from .run import prepare_run
from .store import write_run
from .trace import Trace

_log = logging.getLogger("agent")


def load_session(session_id: Optional[str] = None,
                 series_path: Optional[str] = None) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """series.json и метаданные: из файла или из хранилища и БД с правами только на чтение."""
    if series_path:
        with open(series_path, encoding="utf-8") as f:
            return json.load(f), None
    from mcp_server import make_source

    return make_source().load(session_id)


async def analyze(config: AgentConfig, session_id: Optional[str] = None, series_path: Optional[str] = None,
                  use_llm: bool = True, out_dir: Optional[Path] = None, llm: Optional[LLMClient] = None,
                  bridge_server: Any = None) -> Tuple[RunData, Path]:
    """
    Разбор одной сессии.

    Без модели (use_llm=False) — отчёт по детекторам. С моделью: MCP-сервер запускается дочерним процессом
    с профилем приватности из настроек, цикл агента выносит вердикты и итог.

    Args:
        config        (AgentConfig)          : Настройки агента.
        session_id    (str, optional)        : Id сессии (данные из хранилища). Defaults to None.
        series_path   (str, optional)        : Путь к series.json вместо хранилища. Defaults to None.
        use_llm       (bool, optional)       : Разбирать моменты моделью. Defaults to True.
        out_dir       (Path, optional)       : Каталог разборов. Defaults to config.run.output_dir.
        llm           (LLMClient, optional)  : Клиент модели (для тестов). Defaults to None — по настройкам.
        bridge_server (Any, optional)        : Сервер MCP (для тестов — MCPServer в процессе). Defaults to None.

    Returns:
        Tuple[RunData, Path]: Разбор и каталог его файлов.
    """
    rc        = config.run
    doc, meta = load_session(session_id, series_path)
    run       = prepare_run(doc, load_profile(rc.profile), session_id=session_id, meta=meta, privacy=rc.privacy,
                            top_k=rc.top_k)
    trace     = Trace()
    if use_llm and run.moments:
        server = bridge_server or server_params(session_id=session_id, series_path=series_path,
                                                privacy=rc.privacy, profile=rc.profile)
        client = llm or LLMClient(config.llm)
        try:
            async with MCPBridge(server) as bridge:
                await AgentLoop(client, bridge, rc, no_think=config.llm.no_think, trace=trace).run(run)
        finally:
            if llm is None:
                await client.close()
    elif use_llm:
        run.model = (llm.name if llm else config.llm.model)
    path = write_run(run, Path(out_dir or rc.output_dir), trace if use_llm else None)
    _log.info(f"{run.session_id}: {run.status}, моментов {len(run.moments)}, вердиктов {len(run.findings)} → {path}")
    return run, path
