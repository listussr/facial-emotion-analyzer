"""MCP-сервер affectra-data: ручки одного разбора поверх AffectraTools."""

from mcp.server.mcpserver import MCPServer

from .tools import TOOL_NAMES
from .tools import AffectraTools

INSTRUCTIONS = (
    "Данные одной сессии анализа эмоций: один человек в кадре (субъект P1). Время — секунды от начала сессии. "
    "Вероятности — выраженная эмоция из 6 классов CREMA-D по моделям лица и голоса, а не чувства человека. "
    "Начните с get_session_overview и detect_moments; для разбора момента — get_moment_context и ряды "
    "в его интервале. Все ручки только читают данные."
)


def create_server(tools: AffectraTools) -> MCPServer:
    """
    Сервер с ручками разбора.

    Набор ручек одинаков для профилей external и local; разница — в содержимом ответов (имя сессии и id людей
    только в local). Ручки истории человека и кадров записи появятся во второй версии и только в local.

    Args:
        tools (AffectraTools): Ручки, привязанные к сессии разбора.

    Returns:
        MCPServer: Сервер, готовый к run("stdio") или к подключению Client в том же процессе.
    """
    server = MCPServer(name="affectra-data", instructions=INSTRUCTIONS)
    for name in TOOL_NAMES:
        method = getattr(tools, name)
        server.add_tool(method, name=name, description=(method.__doc__ or "").strip(), structured_output=False)
    return server
