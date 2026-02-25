"""
Gateway: connects channels to the agent.

Manages multiple channels, routes messages to the agent,
manages sessions, and supports middleware.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Awaitable

from loguru import logger

from catbot.agent import Agent
from catbot.channels.base import Channel, IncomingMessage, OutgoingMessage
from catbot.session import SessionManager


# Middleware type: async function (msg, next) → str | None
MiddlewareFn = Callable[
    [IncomingMessage, Callable[[IncomingMessage], Awaitable[str | None]]],
    Awaitable[str | None],
]


class Gateway:
    """
    Routes messages from one or more channels to an Agent.

    Responsibilities:
    - Register channels
    - Manage sessions (via SessionManager)
    - Apply middleware chain
    - Call agent.run() and send reply back via channel
    """

    def __init__(
        self,
        agent: Agent,
        session_manager: SessionManager | None = None,
        daily_reset: bool = False,
    ) -> None:
        self.agent = agent
        self.sessions = session_manager or SessionManager()
        self.daily_reset = daily_reset
        self._channels: dict[str, Channel] = {}
        self._middlewares: list[MiddlewareFn] = []

    # ------------------------------------------------------------------
    # Channel management
    # ------------------------------------------------------------------

    def add_channel(self, channel: Channel) -> None:
        """Register a channel with this gateway."""
        channel.set_handler(self._make_handler(channel))
        self._channels[channel.name] = channel
        logger.info(f"Gateway: registered channel '{channel.name}'")

    def get_channel(self, name: str) -> Channel | None:
        """Look up a channel by name."""
        return self._channels.get(name)

    # ------------------------------------------------------------------
    # Middleware
    # ------------------------------------------------------------------

    def use(self, middleware: MiddlewareFn) -> None:
        """
        Add a middleware function.

        Middleware signature::

            async def my_middleware(
                msg: IncomingMessage,
                next: Callable[[IncomingMessage], Awaitable[str | None]],
            ) -> str | None:
                # pre-processing
                result = await next(msg)
                # post-processing
                return result
        """
        self._middlewares.append(middleware)
        logger.debug(f"Gateway: added middleware {middleware.__name__}")

    def _make_handler(self, channel: Channel) -> Callable[[IncomingMessage], Awaitable[str | None]]:
        """Create the message handler for a specific channel."""
        async def handler(msg: IncomingMessage) -> str | None:
            return await self._process(msg, channel)
        return handler

    async def _process(self, msg: IncomingMessage, channel: Channel) -> str | None:
        """Run the middleware chain and then the agent."""
        # Build the innermost handler
        async def core(m: IncomingMessage) -> str | None:
            return await self._run_agent(m, channel)

        # Wrap with middlewares (last registered = innermost)
        handler = core
        for mw in reversed(self._middlewares):
            prev = handler
            async def wrapped(m: IncomingMessage, _mw=mw, _prev=prev) -> str | None:
                return await _mw(m, _prev)
            handler = wrapped

        try:
            return await handler(msg)
        except Exception as exc:
            logger.error(f"Gateway processing error: {exc}")
            return f"⚠️ Internal error: {exc}"

    async def _run_agent(self, msg: IncomingMessage, channel: Channel) -> str | None:
        """Fetch/create session and run the agent."""
        session = await self.sessions.get(
            msg.session_key, daily_reset=self.daily_reset
        )
        logger.info(
            f"Gateway: [{msg.channel}] user={msg.user_id} chat={msg.chat_id} "
            f"text={msg.text[:80]!r}"
        )
        try:
            reply = await self.agent.run(
                user_message=msg.text,
                session=session,
            )
        except Exception as exc:
            logger.error(f"Agent.run() error: {exc}")
            reply = f"⚠️ Agent error: {exc}"

        return reply

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all registered channels concurrently."""
        if not self._channels:
            logger.warning("Gateway.start(): no channels registered")
            return

        logger.info(f"Gateway: starting {len(self._channels)} channel(s)...")
        tasks = [asyncio.create_task(ch.start()) for ch in self._channels.values()]
        try:
            await asyncio.gather(*tasks)
        except Exception as exc:
            logger.error(f"Gateway error: {exc}")
            raise

    async def stop(self) -> None:
        """Stop all registered channels."""
        for ch in self._channels.values():
            try:
                await ch.stop()
            except Exception as exc:
                logger.warning(f"Error stopping channel '{ch.name}': {exc}")
        logger.info("Gateway stopped")

    async def send(self, channel_name: str, message: OutgoingMessage) -> None:
        """Send a message directly via a named channel."""
        channel = self._channels.get(channel_name)
        if channel is None:
            raise ValueError(f"Unknown channel: {channel_name!r}")
        await channel.send(message)

    # ------------------------------------------------------------------
    # Convenience: built-in rate-limit middleware
    # ------------------------------------------------------------------

    @staticmethod
    def rate_limit(
        max_calls: int = 10,
        window_seconds: float = 60.0,
    ) -> MiddlewareFn:
        """
        Simple per-user rate limiting middleware.

        Args:
            max_calls: Maximum number of calls per user per window.
            window_seconds: Time window in seconds.
        """
        import time
        call_log: dict[str, list[float]] = {}

        async def middleware(
            msg: IncomingMessage,
            next_fn: Callable[[IncomingMessage], Awaitable[str | None]],
        ) -> str | None:
            user_key = f"{msg.channel}:{msg.user_id}"
            now = time.monotonic()
            timestamps = call_log.get(user_key, [])
            # Remove old entries outside window
            timestamps = [t for t in timestamps if now - t < window_seconds]
            if len(timestamps) >= max_calls:
                logger.warning(f"Rate limit hit for {user_key}")
                return f"⚠️ Rate limit: max {max_calls} messages per {window_seconds:.0f}s."
            timestamps.append(now)
            call_log[user_key] = timestamps
            return await next_fn(msg)

        middleware.__name__ = "rate_limit"
        return middleware

    @staticmethod
    def allow_users(allowed_user_ids: list[str]) -> MiddlewareFn:
        """
        Middleware that restricts access to a whitelist of user IDs.

        Args:
            allowed_user_ids: List of permitted user IDs.
        """
        allowed = set(allowed_user_ids)

        async def middleware(
            msg: IncomingMessage,
            next_fn: Callable[[IncomingMessage], Awaitable[str | None]],
        ) -> str | None:
            if msg.user_id not in allowed:
                logger.warning(f"Unauthorized user: {msg.user_id}")
                return "⛔ Access denied."
            return await next_fn(msg)

        middleware.__name__ = "allow_users"
        return middleware
