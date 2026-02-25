"""
CLI channel — interactive terminal for local testing.

Usage::

    channel = CLIChannel()
    await gateway.run()   # starts CLI REPL
"""

from __future__ import annotations

import asyncio
import sys

from loguru import logger

from catbot.channels.base import BaseChannel, IncomingMessage, MessageHandler, OutgoingMessage


class CLIChannel(BaseChannel):
    """Interactive CLI channel for local testing."""

    name = "cli"

    def __init__(self, prompt: str = "You: ", sender_id: str = "local") -> None:
        self.prompt = prompt
        self.sender_id = sender_id
        self._running = False
        self._handler: MessageHandler | None = None

    async def start(self, on_message: MessageHandler) -> None:
        self._handler = on_message
        self._running = True

        print("catbot CLI — type 'quit' or Ctrl+C to exit\n")

        while self._running:
            try:
                # Read input (non-blocking via executor)
                loop = asyncio.get_event_loop()
                try:
                    line = await loop.run_in_executor(None, self._read_line)
                except EOFError:
                    break

                if not line:
                    continue
                if line.lower() in ("quit", "exit", "q"):
                    break

                incoming = IncomingMessage(
                    channel="cli",
                    sender_id=self.sender_id,
                    chat_id="cli:local",
                    content=line,
                )

                if self._handler:
                    await self._handler(incoming)

            except KeyboardInterrupt:
                break
            except Exception as exc:
                logger.error(f"[cli] Error: {exc}")

        print("\nBye!")

    def _read_line(self) -> str:
        try:
            return input(self.prompt).strip()
        except EOFError:
            raise

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: OutgoingMessage) -> bool:
        print(f"\nBot: {msg.content}\n")
        return True
