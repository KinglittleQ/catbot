"""
Anthropic native LLM provider.

Supports Claude models via the official anthropic SDK.
Features prompt caching via cache_control.
"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger

from catbot.providers.base import LLMProvider, Message, ToolCall, LLMResponse


class AnthropicProvider(LLMProvider):
    """
    LLM provider for Anthropic Claude models.

    Args:
        api_key: Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
        model: Default model name.
        enable_caching: Enable prompt caching (cache_control) for system prompt.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-3-5-sonnet-20241022",
        enable_caching: bool = True,
    ) -> None:
        import os
        try:
            import anthropic as _anthropic
            self._anthropic = _anthropic
        except ImportError as exc:
            raise ImportError("anthropic package is required: pip install anthropic") from exc

        self._model = model
        self._enable_caching = enable_caching
        self._client = _anthropic.AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
        )
        logger.debug(f"AnthropicProvider initialized: model={model}, caching={enable_caching}")

    @property
    def default_model(self) -> str:
        return self._model

    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Call the Anthropic Messages API."""
        use_model = model or self._model

        # Build system prompt with optional cache_control
        system_param: Any
        if system:
            if self._enable_caching:
                system_param = [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            else:
                system_param = system
        else:
            system_param = None

        # Convert messages to Anthropic format
        ant_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "tool":
                # Tool results
                tool_result_blocks: list[dict[str, Any]] = []
                for result in msg.tool_results:
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": result["call_id"],
                        "content": result["result"],
                    })
                ant_messages.append({"role": "user", "content": tool_result_blocks})

            elif msg.role == "assistant":
                content_blocks: list[Any] = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.call_id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                ant_messages.append({"role": "assistant", "content": content_blocks})

            else:
                # user message
                ant_messages.append({
                    "role": msg.role,
                    "content": msg.content or "",
                })

        # Build tool definitions
        ant_tools: list[dict[str, Any]] | None = None
        if tools:
            ant_tools = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]

        kwargs: dict[str, Any] = {
            "model": use_model,
            "max_tokens": max_tokens,
            "messages": ant_messages,
            "temperature": temperature,
        }
        if system_param is not None:
            kwargs["system"] = system_param
        if ant_tools:
            kwargs["tools"] = ant_tools

        try:
            resp = await self._client.messages.create(**kwargs)
        except Exception as exc:
            logger.error(f"Anthropic API error: {exc}")
            raise

        # Parse response content blocks
        text_parts: list[str] = []
        parsed_tool_calls: list[ToolCall] = []

        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                parsed_tool_calls.append(
                    ToolCall(
                        call_id=block.id or str(uuid.uuid4()),
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                    )
                )

        content = "\n".join(text_parts) if text_parts else None
        finish_reason = "tool_calls" if parsed_tool_calls else str(resp.stop_reason or "stop")

        usage: dict[str, int] = {}
        if resp.usage:
            usage = {
                "input_tokens": resp.usage.input_tokens or 0,
                "output_tokens": resp.usage.output_tokens or 0,
            }
            # Cache stats if available
            if hasattr(resp.usage, "cache_read_input_tokens"):
                usage["cache_read_tokens"] = resp.usage.cache_read_input_tokens or 0
            if hasattr(resp.usage, "cache_creation_input_tokens"):
                usage["cache_write_tokens"] = resp.usage.cache_creation_input_tokens or 0

        logger.debug(
            f"Anthropic response: model={resp.model}, stop={resp.stop_reason}, "
            f"tool_calls={len(parsed_tool_calls)}, usage={usage}"
        )

        return LLMResponse(
            content=content,
            tool_calls=parsed_tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            model=resp.model or use_model,
        )
