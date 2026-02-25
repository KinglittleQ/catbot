"""
Agent loop core.

Design inspired by openclaw:
- System prompt = SOUL.md + AGENTS.md + MEMORY.md + skills section
- Session history is passed as messages (not rebuilt each turn)
- Tool calls are executed concurrently when possible
- Compaction triggered when token estimate exceeds threshold
- on_tool_call / on_reply callbacks for channel integration

openclaw system prompt structure (src/agents/system-prompt.ts):
  ## Skills (mandatory)
  ## Memory Recall
  ## Authorized Senders
  ## Current Date & Time
  [workspace files: SOUL.md, AGENTS.md, USER.md]
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from loguru import logger

from catbot.providers.base import LLMProvider, LLMResponse
from catbot.session import Message, Session, ToolCall, ToolResult
from catbot.tools import ToolRegistry
from catbot.memory import Memory


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class AgentConfig:
    """Agent configuration."""

    # Identity
    agent_id: str = "main"
    system_prompt: str = "You are a helpful assistant."

    # Model
    model: str = ""                 # Empty = provider default
    max_tokens: int = 4096
    temperature: float = 0.7

    # Loop control
    max_turns: int = 10             # Max tool-use rounds per request

    # Compaction (openclaw-style)
    context_window: int = 128_000   # Model context window in tokens
    compaction_threshold: float = 0.7  # Trigger compaction at 70% full
    compaction_keep_last: int = 10  # Keep last N messages after compaction

    # Workspace
    workspace_dir: str = "~/.catbot/workspace"

    # Timezone (injected into system prompt like openclaw)
    timezone: str = ""


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

OnToolCall = Callable[[str, str, dict[str, Any]], Awaitable[None]]   # (call_id, name, args)
OnToolResult = Callable[[str, str, str], Awaitable[None]]             # (call_id, name, result)
OnReply = Callable[[str], Awaitable[None]]                            # (content)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Agent:
    """Core agent: builds context, calls LLM, executes tools, loops.

    Usage::

        agent = Agent(provider=OpenAIProvider(api_key="..."))
        agent.tools.register(my_tool)
        reply = await agent.run("Hello!", session=session)
    """

    def __init__(
        self,
        provider: LLMProvider,
        tools: ToolRegistry | None = None,
        memory: Memory | None = None,
        config: AgentConfig | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools or ToolRegistry()
        self.memory = memory
        self.config = config or AgentConfig()

        # Callbacks (set by Gateway or caller)
        self.on_tool_call: OnToolCall | None = None
        self.on_tool_result: OnToolResult | None = None
        self.on_reply: OnReply | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        user_message: str,
        session: Session | None = None,
        *,
        extra_system: str = "",
        sender_id: str = "",
    ) -> str:
        """Process a user message and return the final reply.

        Args:
            user_message: The user's input.
            session: Session for conversation history (optional).
            extra_system: Additional text appended to system prompt.
            sender_id: Sender identifier (for authorized-senders section).

        Returns:
            The final assistant text response.
        """
        # Optionally trigger compaction before adding new message
        if session:
            await self._maybe_compact(session)

        # Build system prompt (openclaw-style sections)
        system = self._build_system(extra_system=extra_system, sender_id=sender_id)

        # Append user message
        user_msg = Message(role="user", content=user_message)
        if session:
            await session.add(user_msg)

        # Collect history for LLM
        history = session.get_messages() if session else [user_msg]

        # Tool schemas
        tool_schemas = self.tools.schemas()

        # Agent loop
        final_reply = ""
        for turn in range(1, self.config.max_turns + 1):
            logger.debug(f"[{self.config.agent_id}] Turn {turn}/{self.config.max_turns}")

            # Convert messages to provider format
            llm_messages = self._to_llm_messages(history)

            try:
                response = await self.provider.complete(
                    messages=llm_messages,
                    system=system,
                    tools=tool_schemas or None,
                    model=self.config.model or None,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                )
            except Exception as exc:
                logger.error(f"LLM error on turn {turn}: {exc}")
                raise

            # Record assistant message
            assistant_msg = Message(
                role="assistant",
                content=response.content,
                tool_calls=[
                    ToolCall(tc.call_id, tc.name, tc.arguments)
                    for tc in response.tool_calls
                ],
            )
            history.append(assistant_msg)
            if session:
                await session.add(assistant_msg)

            # No tool calls → done
            if not response.has_tool_calls:
                final_reply = response.content or ""
                break

            # Execute tool calls
            tool_results = await self._execute_tools(response.tool_calls)

            # Record tool results
            tool_msg = Message(role="tool", tool_results=tool_results)
            history.append(tool_msg)
            if session:
                await session.add(tool_msg)

        else:
            logger.warning(f"[{self.config.agent_id}] Reached max_turns={self.config.max_turns}")
            final_reply = response.content or ""  # type: ignore[possibly-undefined]

        # Fire on_reply callback
        if final_reply and self.on_reply:
            try:
                await self.on_reply(final_reply)
            except Exception as exc:
                logger.warning(f"on_reply callback error: {exc}")

        return final_reply

    # ------------------------------------------------------------------
    # System prompt (openclaw-style)
    # ------------------------------------------------------------------

    def _build_system(self, extra_system: str = "", sender_id: str = "") -> str:
        """Build the full system prompt.

        Structure mirrors openclaw's system-prompt.ts:
          [base system prompt]
          [SOUL.md / AGENTS.md if present]
          ## Memory
          [MEMORY.md contents]
          ## Authorized Senders
          [sender_id if provided]
          ## Current Date & Time
          [timestamp + timezone]
          [extra_system]
        """
        parts: list[str] = [self.config.system_prompt]

        if self.memory:
            # Bootstrap files (openclaw: workspace.ts loads SOUL.md, AGENTS.md, USER.md)
            for getter, label in [
                (self.memory.get_soul, ""),
                (self.memory.get_agents_md, ""),
            ]:
                content = getter()
                if content.strip():
                    parts.append(content.strip())

            # Long-term memory
            mem = self.memory.get_memory()
            if mem.strip():
                parts.append(f"## Memory\n{mem.strip()}")

        # Authorized senders (openclaw: buildUserIdentitySection)
        if sender_id:
            parts.append(f"## Authorized Senders\n{sender_id}")

        # Current date/time (openclaw: buildTimeSection)
        tz_name = self.config.timezone or "UTC"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        parts.append(f"## Current Date & Time\n{now} ({tz_name})")

        if extra_system:
            parts.append(extra_system)

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Message conversion
    # ------------------------------------------------------------------

    def _to_llm_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert catbot Messages to provider-agnostic dict format."""
        result: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                # Inline system messages (e.g. compaction summaries)
                result.append({"role": "user", "content": f"[System: {msg.content}]"})
            elif msg.role == "user":
                result.append({"role": "user", "content": msg.content or ""})
            elif msg.role == "assistant":
                d: dict[str, Any] = {"role": "assistant", "content": msg.content}
                if msg.tool_calls:
                    d["tool_calls"] = [
                        {"call_id": tc.call_id, "name": tc.name, "arguments": tc.arguments}
                        for tc in msg.tool_calls
                    ]
                result.append(d)
            elif msg.role == "tool":
                result.append({
                    "role": "tool",
                    "tool_results": [
                        {"call_id": tr.call_id, "name": tr.name, "content": tr.content}
                        for tr in msg.tool_results
                    ],
                })
        return result

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tools(
        self, tool_calls: list[Any]
    ) -> list[ToolResult]:
        """Execute all tool calls concurrently."""
        tasks = [self._execute_one(tc) for tc in tool_calls]
        return await asyncio.gather(*tasks)

    async def _execute_one(self, tc: Any) -> ToolResult:
        """Execute a single tool call."""
        logger.info(f"Tool: {tc.name}({json.dumps(tc.arguments, ensure_ascii=False)[:120]})")

        if self.on_tool_call:
            try:
                await self.on_tool_call(tc.call_id, tc.name, tc.arguments)
            except Exception as exc:
                logger.warning(f"on_tool_call callback error: {exc}")

        result = await self.tools.execute(tc.name, tc.arguments)

        if self.on_tool_result:
            try:
                await self.on_tool_result(tc.call_id, tc.name, result)
            except Exception as exc:
                logger.warning(f"on_tool_result callback error: {exc}")

        logger.debug(f"Tool result [{tc.call_id[:8]}]: {result[:200]}")
        return ToolResult(call_id=tc.call_id, name=tc.name, content=result)

    # ------------------------------------------------------------------
    # Compaction (openclaw-style)
    # ------------------------------------------------------------------

    async def _maybe_compact(self, session: Session) -> None:
        """Trigger compaction if session is too large.

        openclaw's compaction: summarize old messages, keep last N,
        prepend summary as a system message.
        """
        token_estimate = session.estimate_tokens()
        threshold = int(self.config.context_window * self.config.compaction_threshold)

        if token_estimate < threshold:
            return

        logger.info(
            f"[{self.config.agent_id}] Compacting session "
            f"(~{token_estimate} tokens > {threshold} threshold)"
        )

        messages = session.get_messages()
        if len(messages) <= self.config.compaction_keep_last:
            return

        to_summarize = messages[:-self.config.compaction_keep_last]

        # Build a summary prompt
        conversation_text = "\n".join(
            f"{m.role}: {m.content or '[tool call]'}"
            for m in to_summarize
        )
        summary_prompt = (
            f"Summarize this conversation concisely. "
            f"Preserve key decisions, facts, and context.\n\n{conversation_text}"
        )

        try:
            summary_response = await self.provider.complete(
                messages=[{"role": "user", "content": summary_prompt}],
                system="You are a concise summarizer. Respond only with the summary.",
                model=self.config.model or None,
                max_tokens=1024,
                temperature=0.3,
            )
            summary = summary_response.content or "No summary available."
        except Exception as exc:
            logger.warning(f"Compaction summarization failed: {exc}")
            summary = f"[{len(to_summarize)} messages omitted due to context limit]"

        await session.compact(summary, keep_last=self.config.compaction_keep_last)
        logger.info(f"[{self.config.agent_id}] Compaction done: summary + {self.config.compaction_keep_last} messages kept")
