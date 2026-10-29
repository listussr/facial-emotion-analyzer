"""MCP-сервер affectra-data: готовые ручки только на чтение для агента-аналитика (docs/AGENT.md)."""

from .data import RecordingSource
from .server import create_server
from .tools import PRIVACY_LEVELS
from .tools import TOOL_NAMES
from .tools import AffectraTools
from .tools import SessionContext
from .tools import ToolError

__all__ = [
    "AffectraTools", "PRIVACY_LEVELS", "RecordingSource", "SessionContext", "TOOL_NAMES", "ToolError",
    "create_server",
]
