"""
Abstract base classes for channels.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    AUDIO = "audio"
    UNKNOWN = "unknown"


@dataclass
class IncomingMessage:
    """A message received from a channel."""

    channel: str                      # Channel name, e.g. "feishu", "cli"
    chat_id: str                      # Chat/group ID
    user_id: str                      # Sender user ID
    message_id: str                   # Platform message ID
    text: str                         # Plain text content
    message_type: MessageType = MessageType.TEXT
    raw: Any = None                   # Raw platform event object
    attachments: list[dict[str, Any]] = field(default_factory=list)
    is_mention: bool = False          # Was the bot @mentioned?
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def session_key(self) -> str:
        """Composite session key: 'channel:chat_id'."""
        return f"{self.channel}:{self.chat_id}"


@dataclass
class OutgoingMessage:
    """A message to be sent via a channel."""

    chat_id: str
    text: str | None = None
    image_path: str | None = None
    file_path: str | None = None
    reply_to_message_id: str | None = None
    message_type: MessageType = MessageType.TEXT
    metadata: dict[str, Any] = field(default_factory=dict)


# Handler type: async function that receives an IncomingMessage and returns a reply string
MessageHandler = Callable[[IncomingMessage], Awaitable[str | None]]


class Channel(ABC):
    """Abstract base class for message channels."""

    name: str = "base"

    def __init__(self) -> None:
        self._handler: MessageHandler | None = None

    def set_handler(self, handler: MessageHandler) -> None:
        """Register the message handler callback."""
        self._handler = handler

    async def _dispatch(self, msg: IncomingMessage) -> str | None:
        """Dispatch an incoming message to the registered handler."""
        if self._handler is None:
            return None
        return await self._handler(msg)

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> None:
        """Send a message through this channel."""
        ...

    @abstractmethod
    async def start(self) -> None:
        """Start the channel (connect, listen, etc.)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel gracefully."""
        ...

    async def add_reaction(self, message_id: str, emoji: str) -> None:
        """Add an emoji reaction to a message (optional, no-op by default)."""
        pass

    async def remove_reaction(self, message_id: str, emoji: str) -> None:
        """Remove an emoji reaction from a message (optional, no-op by default)."""
        pass
