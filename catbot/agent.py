"""
Agent loop core.

Receives messages, builds context (system + memory + history),
calls LLM, handles tool calls, loops until stop or max_turns.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from loguru import logger

from catbot.providers.base import LLMProvider, Message, ToolCall, LLMResponse
from catbot.tools import ToolRegistry
from catbot.session import Session
from catbot.memory import Memory


@dataclass
class AgentConfig:
    """Configuration for an Agent instance."""

    system_prompt: str = "You are a helpful assistant."
    max_turns: int = 10
    max_tokens: int = 4096
    temperature: float = 0.7
    model: str = ""  # Override provider default if set


class Agent:
    """
    Core agent loop.

    Orchestrates LLM calls, tool execution, session history,
    and memory injection.
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

        # Callbacks
        self.on_tool_call: Callable[[ToolCall, Any], Awaitable[None]] | None = None
        self.on_tool_result: Callable[[str, Any], Awaitable[None]] | None = None
        self.on_llm_response: Callable[[LLMResponse], Awaitable[None]] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        user_message: str,
        session: Session | None = None,
        extra_context: str = "",
    ) -> str:
        """
        Process a user message and return the final assistant reply.

        Args:
            user_message: The user's input text.
            session: Optional session for conversation history persistence.
            extra_context: Additional context injected after system prompt.

        Returns:
            The final text response from the assistant.
        """
        # Build system prompt
        system = self._build_system(extra_context)

        # Load history from session
        history: list[Message] = []
        if session:
            history = session.get_messages()

        # Append user message
        user_msg = Message(role="user", content=user_message)
        history.append(user_msg)
        if session:
            session.append(user_msg)

        # Tool schemas
        tool_schemas = self.tools.schemas() if self.tools else []

        turns = 0
        final_reply = ""

        while turns < self.config.max_turns:
            turns += 1
            logger.debug(f"Agent turn {turns}/{self.config.max_turns}")

            try:
                response = await self.provider.complete(
                    messages=history,
                    system=system,
                    tools=tool_schemas,
                    model=self.config.model or None,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                )
            except Exception as exc:
                logger.error(f"LLM call failed: {exc}")
                raise

            if self.on_llm_response:
                try:
                    await self.on_llm_response(response)
                except Exception as exc:
                    logger.warning(f"on_llm_response callback error: {exc}")

            # Add assistant message to history
            assistant_msg = Message(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls or [],
            )
            history.append(assistant_msg)
            if session:
                session.append(assistant_msg)

            # No tool calls → we're done
            if not response.tool_calls:
                final_reply = response.content or ""
                break

            # Execute tool calls
            tool_results = await self._execute_tool_calls(response.tool_calls)

            # Append tool results as a tool message
            tool_msg = Message(role="tool", content=None, tool_results=tool_results)
            history.append(tool_msg)
            if session:
                session.append(tool_msg)

            # If finish_reason was stop despite tool calls, break
            if response.finish_reason == "stop":
                final_reply = response.content or ""
                break

        else:
            logger.warning(f"Reached max_turns ({self.config.max_turns})")
            final_reply = response.content or "Max turns reached."  # type: ignore[possibly-undefined]

        # Persist to memory history log
        if self.memory:
            try:
                await self.memory.append_history(user_message, final_reply)
            except Exception as exc:
                logger.warning(f"Memory append failed: {exc}")

        return final_reply

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_system(self, extra_context: str = "") -> str:
        """Compose the full system prompt."""
        parts = [self.config.system_prompt]

        if self.memory:
            mem_content = self.memory.get_memory()
            if mem_content:
                parts.append(f"\n## Long-term Memory\n{mem_content}")

        if extra_context:
            parts.append(f"\n{extra_context}")

        return "\n".join(parts)

    async def _execute_tool_calls(
        self, tool_calls: list[ToolCall]
    ) -> list[dict[str, Any]]:
        """Execute all tool calls concurrently and return results."""
        tasks = [self._execute_single_tool(tc) for tc in tool_calls]
        return await asyncio.gather(*tasks)

    async def _execute_single_tool(self, tc: ToolCall) -> dict[str, Any]:
        """Execute a single tool call and return a result dict."""
        logger.info(f"Tool call: {tc.name}({tc.arguments})")

        if self.on_tool_call:
            try:
                await self.on_tool_call(tc, tc.arguments)
            except Exception as exc:
                logger.warning(f"on_tool_call callback error: {exc}")

        try:
            result = await self.tools.execute(tc.name, tc.arguments)
            result_str = str(result) if not isinstance(result, str) else result
        except Exception as exc:
            logger.error(f"Tool '{tc.name}' raised: {exc}")
            result_str = f"Error: {exc}"

        if self.on_tool_result:
            try:
                await self.on_tool_result(tc.call_id, result_str)
            except Exception as exc:
                logger.warning(f"on_tool_result callback error: {exc}")

        logger.debug(f"Tool result [{tc.call_id}]: {result_str[:200]}")
        return {"call_id": tc.call_id, "name": tc.name, "result": result_str}
