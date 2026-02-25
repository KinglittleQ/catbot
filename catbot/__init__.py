"""
catbot - A minimal Python agent framework with Feishu support.
"""

from catbot.agent import Agent, AgentConfig
from catbot.tools import tool, ToolRegistry
from catbot.session import Session, SessionManager
from catbot.memory import Memory
from catbot.gateway import Gateway
from catbot.providers.base import LLMProvider, Message, ToolCall, LLMResponse
from catbot.providers.openai import OpenAIProvider
from catbot.providers.anthropic import AnthropicProvider
from catbot.channels.base import Channel, IncomingMessage, OutgoingMessage
from catbot.channels.cli import CLIChannel
from catbot.channels.feishu import FeishuChannel

__version__ = "0.1.0"
__all__ = [
    "Agent",
    "AgentConfig",
    "tool",
    "ToolRegistry",
    "Session",
    "SessionManager",
    "Memory",
    "Gateway",
    "LLMProvider",
    "Message",
    "ToolCall",
    "LLMResponse",
    "OpenAIProvider",
    "AnthropicProvider",
    "Channel",
    "IncomingMessage",
    "OutgoingMessage",
    "CLIChannel",
    "FeishuChannel",
]
