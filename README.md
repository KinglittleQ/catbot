# catbot 🐱

A minimal Python agent framework inspired by [openclaw](https://github.com/openclaw/openclaw), with native Feishu (Lark) support.

**~1,200 lines** of clean, typed Python implementing the core of an agentic AI system.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  CHANNELS                        │
│         Feishu (WebSocket)    CLI               │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────┐
│                  GATEWAY                          │
│  Routes messages · Manages sessions · Reactions  │
└──────────────────┬───────────────────────────────┘
                   │
      ┌────────────┴────────────┐
      ▼                         ▼
┌─────────────┐       ┌──────────────────────────┐
│   SESSIONS  │       │        WORKSPACE          │
│             │       │                          │
│ JSONL files │       │ SOUL.md  — identity      │
│ Compaction  │       │ MEMORY.md — long-term    │
│ Daily reset │       │ HISTORY.md — event log   │
│ Session keys│       │ skills/  — SKILL.md      │
└─────────────┘       └──────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────┐
│                   AGENT                           │
│        Think → Tool Call → Observe → Loop         │
└──────────────────┬───────────────────────────────┘
                   │
      ┌────────────┴────────────┐
      ▼                         ▼
┌─────────────┐       ┌──────────────────────────┐
│  PROVIDERS  │       │         TOOLS             │
│             │       │                          │
│  Anthropic  │       │ @tool decorator          │
│  OpenAI     │       │ Auto JSON schema         │
│  DeepSeek…  │       │ Sync + async handlers    │
└─────────────┘       └──────────────────────────┘
```

## Quick Start

```python
import asyncio, os
from catbot import Gateway, GatewayConfig, AnthropicProvider

async def main():
    gw = Gateway(
        provider=AnthropicProvider(api_key=os.environ["ANTHROPIC_API_KEY"]),
        config=GatewayConfig(
            feishu_app_id=os.environ["FEISHU_APP_ID"],
            feishu_app_secret=os.environ["FEISHU_APP_SECRET"],
        ),
    )
    gw.add_builtin_tools()
    await gw.start()

asyncio.run(main())
```

## Install

```bash
pip install catbot[all]        # all providers + feishu
pip install catbot[anthropic]  # anthropic only
pip install catbot[openai]     # openai only
pip install catbot[feishu]     # feishu channel only
```

## Custom Tools

```python
from catbot import tool, Gateway

@tool()
async def get_weather(city: str) -> str:
    """Get weather for a city. city: City name."""
    return f"25°C sunny in {city}"

gw = Gateway(provider=..., config=...)
gw.add_tool(get_weather)
```

The `@tool` decorator **automatically generates JSON schema** from type annotations and docstrings — no boilerplate.

## Session Keys (openclaw-style)

Session keys follow the format:
```
agent:<agentId>:<channel>:<type>:<chatId>
```

Examples:
```
agent:main:feishu:direct:ou_abc123     # Feishu DM
agent:main:feishu:group:oc_xyz789      # Feishu group chat
agent:main:cli:direct:local            # CLI session
agent:main:cron:cron:daily_report      # Scheduled task
```

## Workspace Files

catbot loads workspace files into the system prompt (openclaw-style):

| File | Purpose |
|------|---------|
| `SOUL.md` | Agent identity and personality |
| `AGENTS.md` | Agent instructions |
| `USER.md` | User preferences and context |
| `memory/MEMORY.md` | Long-term facts (loaded every turn) |
| `memory/HISTORY.md` | Event log (append-only, grep-searchable) |
| `skills/<name>/SKILL.md` | Skill descriptions injected into prompt |

Default workspace: `~/.catbot/workspace/`

## Providers

| Provider | Class | Notes |
|----------|-------|-------|
| OpenAI | `OpenAIProvider` | Also works with DeepSeek, Groq, etc. |
| Anthropic | `AnthropicProvider` | Supports prompt caching |

```python
# OpenAI
from catbot import OpenAIProvider
provider = OpenAIProvider(api_key="sk-...", model="gpt-4o")

# DeepSeek (OpenAI-compatible)
provider = OpenAIProvider(
    api_key="sk-...",
    api_base="https://api.deepseek.com/v1",
    model="deepseek-chat",
)

# Anthropic with prompt caching
from catbot import AnthropicProvider
provider = AnthropicProvider(
    api_key="sk-ant-...",
    model="claude-opus-4-5",
    enable_cache=True,
)
```

## Feishu Setup

1. Create an app at [Feishu Open Platform](https://open.feishu.cn/)
2. Enable **Bot** capability
3. Subscribe to event: `im.message.receive_v1`
4. Set permissions: `im:message`, `im:message:send_as_bot`
5. Configure env vars:

```bash
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export ANTHROPIC_API_KEY=sk-ant-xxx
```

## Compaction

When conversation history exceeds 80% of the context window, catbot automatically:
1. Summarizes old messages using the LLM
2. Replaces them with a compact summary
3. Keeps the most recent messages intact

This mirrors openclaw's compaction design.

## License

MIT
