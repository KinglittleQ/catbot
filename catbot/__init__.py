"""catbot — A minimal Python agent framework with Feishu support."""

from catbot.agent import Agent, AgentConfig
from catbot.gateway import Gateway, GatewayConfig, rate_limit, allow_senders, log_messages
from catbot.tools import Tool, ToolRegistry, tool, get_builtin_tools
from catbot.session import Session, SessionManager, Message, ToolCall, ToolResult, make_session_key
from catbot.memory import Memory
from catbot.channels.base import BaseChannel, IncomingMessage, OutgoingMessage
from catbot.channels.cli import CLIChannel
from catbot.providers.base import LLMProvider, LLMResponse

__version__ = "0.1.0"
__all__ = [
    # Core
    "Agent", "AgentConfig",
    "Gateway", "GatewayConfig",
    # Tools
    "Tool", "ToolRegistry", "tool", "get_builtin_tools",
    # Session
    "Session", "SessionManager", "Message", "ToolCall", "ToolResult", "make_session_key",
    # Memory
    "Memory",
    # Channels
    "BaseChannel", "IncomingMessage", "OutgoingMessage", "CLIChannel",
    # Providers
    "LLMProvider", "LLMResponse",
    # Middleware
    "rate_limit", "allow_senders", "log_messages",
]
