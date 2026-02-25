"""
OpenAI-compatible provider.

Works with: OpenAI, Azure OpenAI, DeepSeek, Moonshot, Groq, Together,
any OpenAI-compatible endpoint.

Usage::

    provider = OpenAIProvider(api_key="sk-...", model="gpt-4o")
    # DeepSeek
    provider = OpenAIProvider(
        api_key="sk-...",
        api_base="https://api.deepseek.com/v1",
        model="deepseek-chat",
    )
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from .base import LLMProvider, LLMResponse, ToolCall


class OpenAIProvider(LLMProvider):
    """OpenAI-compatible provider (uses openai SDK)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        api_base: str | None = None,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("pip install openai")

        self._model = model
        self._client = AsyncOpenAI(
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
        full_messages: list[dict[str, Any]] = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "messages": full_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            logger.error(f"OpenAI API error: {exc}")
            raise

        choice = resp.choices[0]
        msg = choice.message

        # Parse tool calls
        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append(ToolCall(
                    call_id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        usage = {}
        if resp.usage:
            usage = {
                "input": resp.usage.prompt_tokens,
                "output": resp.usage.completion_tokens,
            }

        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
        )
