"""
Channel base classes.

A Channel is a message source/sink (Feishu, CLI, Telegram, etc.).
The Gateway connects channels to the Agent.

Inspired by openclaw's channel architecture:
- Each channel has a unique name
- Channels emit IncomingMessage events
- Channels receive OutgoingMessage to send
- Group messages carry group_id; only respond when @mentioned
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class IncomingMessage:
    """A message received from a channel."""

    channel: str        # Channel name, e.g. "feishu", "cli"
    sender_id: str      # User/sender identifier
    chat_id: str        # Chat/conversation identifier
    content: str        # Text content

    # Group chat
    is_group: bool = False
    group_id: str = ""

    # Thread/topic (openclaw: thread session keys)
    thread_id: str = ""

    # Reply context
    reply_to_id: str = ""

    # Raw metadata from the channel SDK
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutgoingMessage:
    """A message to send through a channel."""

    channel: str
    chat_id: str
    content: str

    # Optional: reply in thread
    thread_id: str = ""
    reply_to_id: str = ""

    # Attachments
    image_path: str = ""
    file_path: str = ""

    # Metadata (channel-specific extras)
    metadata: dict[str, Any] = field(default_factory=dict)


# Callback type: called when a message arrives
MessageHandler = Callable[[IncomingMessage], Awaitable[None]]


class BaseChannel(ABC):
    """Abstract base class for all channels."""

    name: str = "base"

    @abstractmethod
    async def start(self, on_message: MessageHandler) -> None:
        """Start listening for messages. Calls on_message for each."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel."""
        ...

    @abstractmethod
    async def send(self, msg: OutgoingMessage) -> bool:
        """Send a message. Returns True on success."""
        ...

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> bool:
        """Convenience: send a plain text message."""
        return await self.send(OutgoingMessage(
            channel=self.name,
            chat_id=chat_id,
            content=text,
            **kwargs,
        ))
