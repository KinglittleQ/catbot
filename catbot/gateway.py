"""
Gateway — connects channels to the agent.

Inspired by openclaw's gateway architecture:
- Routes IncomingMessage → Agent.run() → OutgoingMessage
- Manages session keys (openclaw format: agent:<id>:<channel>:<type>:<chat_id>)
- Middleware chain (rate limiting, allowlists, logging)
- Concurrent message handling per chat (queue per chat_id)
- Send policy: allow/deny per session type (openclaw: send-policy.ts)

Session key format (mirrors openclaw/src/sessions/session-key-utils.ts):
    agent:<agentId>:<channel>:direct:<senderId>    — DM
    agent:<agentId>:<channel>:group:<groupId>      — Group chat
    agent:<agentId>:cli:direct:local               — CLI
    agent:<agentId>:cron:cron:<jobId>              — Cron job
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from catbot.agent import Agent
from catbot.channels.base import BaseChannel, IncomingMessage, OutgoingMessage
from catbot.session import SessionManager, make_session_key
from catbot.memory import Memory


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

Middleware = Callable[[IncomingMessage, Callable], Awaitable[str | None]]
"""A middleware is an async function:
    async def my_middleware(msg, next) -> str | None:
        # return None to block, or call next(msg) to continue
        if not allowed(msg.sender_id):
            return None
        return await next(msg)
"""


# ---------------------------------------------------------------------------
# Gateway config
# ---------------------------------------------------------------------------

@dataclass
class GatewayConfig:
    """Gateway configuration."""

    agent_id: str = "main"

    # Session
    daily_reset: bool = False           # Reset sessions each day
    session_dir: str = "~/.catbot/sessions"

    # Send policy (openclaw: resolveSendPolicy)
    # "allow" | "deny" | per-channel overrides
    send_policy: str = "allow"
    deny_channels: list[str] = field(default_factory=list)
    allow_senders: list[str] = field(default_factory=list)  # Empty = allow all

    # Concurrency: max parallel agent runs per chat_id
    max_concurrent_per_chat: int = 1


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------

class Gateway:
    """Connects channels to the agent with session management and middleware.

    Usage::

        gw = Gateway(agent=agent)
        gw.add_channel(FeishuChannel(app_id=..., app_secret=...))
        gw.use(rate_limit_middleware)
        await gw.run()
    """

    def __init__(
        self,
        agent: Agent,
        config: GatewayConfig | None = None,
        memory: Memory | None = None,
    ) -> None:
        self.agent = agent
        self.config = config or GatewayConfig()
        self.memory = memory

        self._channels: dict[str, BaseChannel] = {}
        self._middleware: list[Middleware] = []
        self._sessions = SessionManager(self.config.session_dir)

        # Per-chat semaphores to serialize messages from the same chat
        self._chat_locks: dict[str, asyncio.Semaphore] = {}

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def add_channel(self, channel: BaseChannel) -> "Gateway":
        """Register a channel. Returns self for chaining."""
        self._channels[channel.name] = channel
        logger.info(f"[gateway] Channel registered: {channel.name!r}")
        return self

    def use(self, middleware: Middleware) -> "Gateway":
        """Add a middleware to the chain. Returns self for chaining."""
        self._middleware.append(middleware)
        return self

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start all channels and block until stopped."""
        if not self._channels:
            raise RuntimeError("No channels registered. Call add_channel() first.")

        tasks = [
            asyncio.create_task(
                channel.start(self._on_message),
                name=f"channel:{name}",
            )
            for name, channel in self._channels.items()
        ]
        logger.info(f"[gateway] Started {len(tasks)} channel(s): {list(self._channels.keys())}")

        try:
            await asyncio.gather(*tasks)
        except Exception as exc:
            logger.error(f"[gateway] Fatal error: {exc}")
            raise
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop all channels."""
        for channel in self._channels.values():
            try:
                await channel.stop()
            except Exception as exc:
                logger.warning(f"[gateway] Error stopping channel {channel.name!r}: {exc}")

    # ------------------------------------------------------------------
    # Message routing
    # ------------------------------------------------------------------

    async def _on_message(self, msg: IncomingMessage) -> None:
        """Called by channels when a message arrives."""
        # Apply send policy
        if not self._check_send_policy(msg):
            logger.debug(f"[gateway] Message blocked by send policy: {msg.sender_id!r}")
            return

        # Run middleware chain
        async def execute_chain(m: IncomingMessage) -> str | None:
            return await self._run_agent(m)

        handler = execute_chain
        for mw in reversed(self._middleware):
            prev = handler
            async def make_handler(middleware: Middleware, next_handler: Callable) -> Callable:
                async def h(m: IncomingMessage) -> str | None:
                    return await middleware(m, next_handler)
                return h
            handler = await make_handler(mw, prev)

        # Serialize per chat_id
        lock = self._get_chat_lock(msg.chat_id)
        async with lock:
            try:
                reply = await handler(msg)
                if reply:
                    await self._send_reply(msg, reply)
            except Exception as exc:
                logger.error(f"[gateway] Error processing message: {exc}")

    async def _run_agent(self, msg: IncomingMessage) -> str | None:
        """Run the agent for an incoming message."""
        session_key = self._make_session_key(msg)
        session = await self._sessions.get(
            session_key,
            daily_reset=self.config.daily_reset,
        )

        logger.info(
            f"[gateway] {msg.channel}:{msg.chat_id} "
            f"({session_key}) — {msg.content[:80]!r}"
        )

        try:
            reply = await self.agent.run(
                user_message=msg.content,
                session=session,
                sender_id=msg.sender_id,
            )
            return reply
        except Exception as exc:
            logger.error(f"[gateway] Agent error: {exc}")
            return f"Error: {exc}"

    async def _send_reply(self, original: IncomingMessage, reply: str) -> None:
        """Send a reply through the originating channel."""
        channel = self._channels.get(original.channel)
        if not channel:
            logger.warning(f"[gateway] Channel {original.channel!r} not found for reply")
            return

        out = OutgoingMessage(
            channel=original.channel,
            chat_id=original.chat_id,
            content=reply,
            thread_id=original.thread_id,
            reply_to_id=original.reply_to_id,
        )
        await channel.send(out)

    # ------------------------------------------------------------------
    # Session key (openclaw-style)
    # ------------------------------------------------------------------

    def _make_session_key(self, msg: IncomingMessage) -> str:
        """Build a canonical session key for a message.

        Mirrors openclaw's session key format:
            agent:<agentId>:<channel>:<type>:<id>
        """
        chat_type = "group" if msg.is_group else "direct"
        chat_id = msg.group_id if msg.is_group else msg.sender_id
        return make_session_key(
            agent_id=self.config.agent_id,
            channel=msg.channel,
            chat_type=chat_type,  # type: ignore[arg-type]
            chat_id=chat_id or msg.chat_id,
        )

    # ------------------------------------------------------------------
    # Send policy (openclaw: resolveSendPolicy)
    # ------------------------------------------------------------------

    def _check_send_policy(self, msg: IncomingMessage) -> bool:
        """Check if a message should be processed."""
        # Global deny
        if self.config.send_policy == "deny":
            return False

        # Channel-level deny
        if msg.channel in self.config.deny_channels:
            return False

        # Allowlist check
        if self.config.allow_senders and msg.sender_id not in self.config.allow_senders:
            logger.debug(f"[gateway] Sender {msg.sender_id!r} not in allow_senders")
            return False

        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_chat_lock(self, chat_id: str) -> asyncio.Semaphore:
        if chat_id not in self._chat_locks:
            self._chat_locks[chat_id] = asyncio.Semaphore(
                self.config.max_concurrent_per_chat
            )
        return self._chat_locks[chat_id]

    # ------------------------------------------------------------------
    # Direct processing (for cron / CLI usage)
    # ------------------------------------------------------------------

    async def process(
        self,
        content: str,
        channel: str = "cli",
        chat_id: str = "local",
        sender_id: str = "system",
        is_group: bool = False,
        session_key: str | None = None,
    ) -> str:
        """Process a message directly without going through channel routing.

        Useful for cron jobs, CLI, and testing.
        """
        msg = IncomingMessage(
            channel=channel,
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            is_group=is_group,
        )

        key = session_key or self._make_session_key(msg)
        session = await self._sessions.get(key, daily_reset=self.config.daily_reset)

        return await self.agent.run(
            user_message=content,
            session=session,
            sender_id=sender_id,
        )


# ---------------------------------------------------------------------------
# Built-in middleware factories
# ---------------------------------------------------------------------------

def rate_limit(max_per_minute: int = 10) -> Middleware:
    """Rate-limit middleware: max N messages per sender per minute."""
    import time
    counts: dict[str, list[float]] = {}

    async def middleware(msg: IncomingMessage, next: Callable) -> str | None:
        now = time.time()
        window = counts.setdefault(msg.sender_id, [])
        # Remove entries older than 60s
        counts[msg.sender_id] = [t for t in window if now - t < 60]
        if len(counts[msg.sender_id]) >= max_per_minute:
            logger.warning(f"[rate_limit] {msg.sender_id!r} exceeded {max_per_minute}/min")
            return "Rate limit exceeded. Please wait a moment."
        counts[msg.sender_id].append(now)
        return await next(msg)

    return middleware


def allow_senders(sender_ids: list[str]) -> Middleware:
    """Allowlist middleware: only process messages from listed senders."""
    allowed = set(sender_ids)

    async def middleware(msg: IncomingMessage, next: Callable) -> str | None:
        if msg.sender_id not in allowed:
            logger.debug(f"[allow_senders] Blocked: {msg.sender_id!r}")
            return None
        return await next(msg)

    return middleware


def log_messages() -> Middleware:
    """Logging middleware: log every incoming message."""
    async def middleware(msg: IncomingMessage, next: Callable) -> str | None:
        logger.info(f"[log] {msg.channel}/{msg.chat_id} [{msg.sender_id}]: {msg.content[:100]!r}")
        result = await next(msg)
        logger.info(f"[log] reply: {(result or '')[:100]!r}")
        return result
    return middleware
