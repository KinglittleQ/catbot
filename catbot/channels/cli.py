"""
CLI channel for local testing.

Reads from stdin, writes to stdout. Supports multi-line input
(end with an empty line or Ctrl+D).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from loguru import logger

from catbot.channels.base import Channel, IncomingMessage, OutgoingMessage, MessageType


class CLIChannel(Channel):
    """
    Simple CLI channel for testing agents locally.

    Usage::

        channel = CLIChannel()
        channel.set_handler(my_handler)
        await channel.start()
    """

    name = "cli"

    def __init__(self, prompt: str = "You: ", bot_name: str = "Bot") -> None:
        super().__init__()
        self.prompt = prompt
        self.bot_name = bot_name
        self._running = False
        self._msg_counter = 0

    async def send(self, message: OutgoingMessage) -> None:
        """Print the message to stdout."""
        if message.text:
            print(f"\n{self.bot_name}: {message.text}\n")
        if message.image_path:
            print(f"\n{self.bot_name}: [Image: {message.image_path}]\n")
        if message.file_path:
            print(f"\n{self.bot_name}: [File: {message.file_path}]\n")

    async def start(self) -> None:
        """Start reading from stdin in a loop."""
        self._running = True
        print(f"[catbot CLI] Type your message and press Enter. Ctrl+C to quit.\n")

        loop = asyncio.get_event_loop()

        while self._running:
            try:
                # Read input in a thread so we don't block the event loop
                line = await loop.run_in_executor(None, self._read_line)
                if line is None:
                    # EOF
                    break
                text = line.strip()
                if not text:
                    continue

                self._msg_counter += 1
                msg = IncomingMessage(
                    channel=self.name,
                    chat_id="cli_default",
                    user_id="user",
                    message_id=f"cli_{self._msg_counter}",
                    text=text,
                    message_type=MessageType.TEXT,
                    is_mention=True,
                )

                try:
                    reply = await self._dispatch(msg)
                    if reply:
                        out = OutgoingMessage(chat_id="cli_default", text=reply)
                        await self.send(out)
                except Exception as exc:
                    logger.error(f"Handler error: {exc}")
                    print(f"\n[Error] {exc}\n")

            except KeyboardInterrupt:
                print("\n[catbot CLI] Bye!")
                break

        self._running = False

    def _read_line(self) -> str | None:
        """Read a line from stdin (blocking)."""
        try:
            sys.stdout.write(self.prompt)
            sys.stdout.flush()
            return sys.stdin.readline()
        except EOFError:
            return None

    async def stop(self) -> None:
        """Stop the CLI loop."""
        self._running = False
        logger.debug("CLIChannel stopped")

    async def add_reaction(self, message_id: str, emoji: str) -> None:
        """Print a reaction indicator."""
        print(f"[reaction +{emoji} on {message_id}]")

    async def remove_reaction(self, message_id: str, emoji: str) -> None:
        """Print a reaction removal indicator."""
        print(f"[reaction -{emoji} on {message_id}]")
