"""
OpenAI-compatible LLM provider.

Supports OpenAI, DeepSeek, Claude via openai-compat, and any
OpenAI-compatible API endpoint.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from loguru import logger

from catbot.providers.base import LLMProvider, Message, ToolCall, LLMResponse


class OpenAIProvider(LLMProvider):
    """
    LLM provider for OpenAI and compatible APIs.

    Args:
        api_key: API key. Falls back to OPENAI_API_KEY env var.
        base_url: API base URL. Defaults to OpenAI's endpoint.
        model: Default model name.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "gpt-4o",
    ) -> None:
        import os
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError("openai package is required: pip install openai") from exc

        self._model = model
        kwargs: dict[str, Any] = {
            "api_key": api_key or os.environ.get("OPENAI_API_KEY", ""),
        }
        if base_url:
            kwargs["base_url"] = base_url

        self._client = AsyncOpenAI(**kwargs)
        logger.debug(f"OpenAIProvider initialized: model={model}, base_url={base_url or 'default'}")

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
        """Call the OpenAI chat completions API."""
        use_model = model or self._model

        # Build message list
        oai_messages: list[dict[str, Any]] = []
        if system:
            oai_messages.append({"role": "system", "content": system})

        for msg in messages:
            if msg.role == "tool":
                # Tool results: one message per result
                for result in msg.tool_results:
                    oai_messages.append({
                        "role": "tool",
                        "tool_call_id": result["call_id"],
                        "content": result["result"],
                    })
            elif msg.role == "assistant" and msg.tool_calls:
                oai_messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.call_id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })
            else:
                oai_messages.append({
                    "role": msg.role,
                    "content": msg.content or "",
                })

        # Build tool schemas
        oai_tools: list[dict[str, Any]] | None = None
        if tools:
            oai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {}),
                    },
                }
                for t in tools
            ]

        kwargs: dict[str, Any] = {
            "model": use_model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if oai_tools:
            kwargs["tools"] = oai_tools
            kwargs["tool_choice"] = "auto"

        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            logger.error(f"OpenAI API error: {exc}")
            raise

        choice = resp.choices[0]
        message = choice.message
        finish_reason = choice.finish_reason or "stop"

        # Parse tool calls
        parsed_tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                parsed_tool_calls.append(
                    ToolCall(
                        call_id=tc.id or str(uuid.uuid4()),
                        name=tc.function.name,
                        arguments=args,
                    )
                )

        usage: dict[str, int] = {}
        if resp.usage:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens or 0,
                "completion_tokens": resp.usage.completion_tokens or 0,
                "total_tokens": resp.usage.total_tokens or 0,
            }

        logger.debug(
            f"OpenAI response: model={resp.model}, finish={finish_reason}, "
            f"tool_calls={len(parsed_tool_calls)}, usage={usage}"
        )

        return LLMResponse(
            content=message.content,
            tool_calls=parsed_tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            model=resp.model or use_model,
        )
