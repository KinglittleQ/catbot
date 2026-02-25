"""
Anthropic provider with prompt caching support.

Implements openclaw-style cache_control breakpoints:
- BP1: system prompt (always cached)
- BP2: before last user message (cached after N messages)
- BP3: second-to-last message

Usage::

    provider = AnthropicProvider(api_key="sk-ant-...", model="claude-opus-4-5")
    # With prompt caching
    provider = AnthropicProvider(
        api_key="sk-ant-...",
        model="claude-opus-4-5",
        enable_cache=True,
    )
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from .base import LLMProvider, LLMResponse, ToolCall


class AnthropicProvider(LLMProvider):
    """Anthropic provider with native SDK and optional prompt caching."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-4-5",
        api_base: str | None = None,
        enable_cache: bool = False,
        max_cache_breakpoints: int = 3,
    ) -> None:
        try:
            import anthropic
            self._anthropic = anthropic
        except ImportError:
            raise ImportError("pip install anthropic")

        self._model = model
        self._enable_cache = enable_cache
        self._max_cache_breakpoints = max_cache_breakpoints
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=api_base,
        )

    def default_model(self) -> str:
        return self._model

    async def complete(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        # Convert messages to Anthropic format
        anthropic_messages = self._convert_messages(messages)

        # Apply cache breakpoints (openclaw-style: last 2 user messages)
        if self._enable_cache:
            self._apply_cache_breakpoints(anthropic_messages)

        # Build system parameter
        system_param: Any = None
        if system:
            if self._enable_cache:
                system_param = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            else:
                system_param = system

        # Convert tools to Anthropic format
        anthropic_tools = None
        if tools:
            anthropic_tools = [self._convert_tool(t) for t in tools]

        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_param is not None:
            kwargs["system"] = system_param
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        try:
            resp = await self._client.messages.create(**kwargs)
        except Exception as exc:
            logger.error(f"Anthropic API error: {exc}")
            raise

        # Parse response content blocks
        content_text: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in resp.content:
            if block.type == "text":
                content_text.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    call_id=block.id,
                    name=block.name,
                    arguments=block.input or {},
                ))

        # Usage stats
        usage: dict[str, int] = {}
        if resp.usage:
            usage = {
                "input": resp.usage.input_tokens,
                "output": resp.usage.output_tokens,
            }
            if hasattr(resp.usage, "cache_read_input_tokens") and resp.usage.cache_read_input_tokens:
                usage["cache_read"] = resp.usage.cache_read_input_tokens
            if hasattr(resp.usage, "cache_creation_input_tokens") and resp.usage.cache_creation_input_tokens:
                usage["cache_write"] = resp.usage.cache_creation_input_tokens

        return LLMResponse(
            content="\n".join(content_text) or None,
            tool_calls=tool_calls,
            finish_reason=resp.stop_reason or "stop",
            usage=usage,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _convert_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert catbot message format to Anthropic format."""
        result: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role")
            if role == "system":
                # System messages are passed separately; skip here
                continue
            elif role == "user":
                result.append({"role": "user", "content": msg.get("content", "")})
            elif role == "assistant":
                content: list[dict[str, Any]] = []
                if msg.get("content"):
                    content.append({"type": "text", "text": msg["content"]})
                for tc in msg.get("tool_calls", []):
                    content.append({
                        "type": "tool_use",
                        "id": tc["call_id"],
                        "name": tc["name"],
                        "input": tc.get("arguments", {}),
                    })
                result.append({"role": "assistant", "content": content or ""})
            elif role == "tool":
                # Tool results → user message with tool_result blocks
                tool_results = msg.get("tool_results", [])
                if tool_results:
                    content = [
                        {
                            "type": "tool_result",
                            "tool_use_id": tr["call_id"],
                            "content": tr.get("content", ""),
                        }
                        for tr in tool_results
                    ]
                    result.append({"role": "user", "content": content})

        return result

    def _apply_cache_breakpoints(self, messages: list[dict[str, Any]]) -> None:
        """Apply cache_control to the last N user messages (openclaw-style)."""
        user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]
        # Mark last max_cache_breakpoints user messages
        for idx in user_indices[-self._max_cache_breakpoints:]:
            msg = messages[idx]
            content = msg.get("content")
            if isinstance(content, str):
                messages[idx]["content"] = [
                    {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                ]
            elif isinstance(content, list) and content:
                # Add cache_control to last block
                content[-1]["cache_control"] = {"type": "ephemeral"}

    def _convert_tool(self, tool_schema: dict[str, Any]) -> dict[str, Any]:
        """Convert OpenAI tool schema to Anthropic format."""
        fn = tool_schema.get("function", tool_schema)
        return {
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        }
