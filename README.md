# catbot 🐱

> A minimal Python agent framework with Feishu support — inspired by [openclaw](https://github.com/openclaw/openclaw).

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Overview

catbot is a clean Python implementation of the openclaw agent architecture:

- **Agent loop** — think → tool call → observe → repeat (like openclaw's pi-agent-core)
- **openclaw-style session keys** — `agent:<id>:<channel>:<type>:<chat_id>`
- **Compaction** — summarize old messages when context window fills up (mirrors openclaw's `compaction.ts`)
- **Memory** — `MEMORY.md` (long-term facts) + `HISTORY.md` (event log)
- **Feishu WebSocket** — native lark-oapi integration, no public server needed
- **Middleware chain** — rate limiting, allowlists, logging (openclaw's send-policy)
- **Multi-provider** — OpenAI-compatible + Anthropic (with prompt caching)

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    CHANNELS                          │
│         Feishu (WebSocket)    CLI    (custom)        │
└──────────────────────┬──────────────────────────────┘
                       │ IncomingMessage
                       ▼
┌──────────────────────────────────────────────────────┐
│                    GATEWAY                            │
│  Session keys • Middleware chain • Send policy        │
│  Concurrency control • Channel routing               │
└──────────────────────┬───────────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
┌───────────────────┐     ┌───────────────────────────┐
│  SESSION STORE    │     │        MEMORY             │
│                   │     │                           │
│ • JSONL append    │     │ • SOUL.md (personality)   │
│ • openclaw keys   │     │ • AGENTS.md (instructions)│
│ • Compaction      │     │ • MEMORY.md (long-term)   │
│ • Daily reset     │     │ • HISTORY.md (log)        │
└───────────────────┘     └───────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│                     AGENT                             │
│   Build system prompt → LLM → Tool calls → Loop      │
│   Compaction trigger • on_tool_call callbacks        │
└──────────────────────┬───────────────────────────────┘
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
┌───────────────┐           ┌─────────────────────┐
│   PROVIDERS   │           │      TOOLS          │
│               │           │                     │
│ • OpenAI      │           │ • @tool decorator   │
│ • Anthropic   │           │ • Auto JSON schema  │
│   (+ caching) │           │ • read/write/exec   │
└───────────────┘           └─────────────────────┘
```

## Quick Start

```bash
pip install catbot
export OPENAI_API_KEY=sk-...
python examples/hello_world.py
```

Or in 5 lines:

```python
import asyncio, os
from catbot import Agent, Gateway, GatewayConfig
from catbot.channels.cli import CLIChannel
from catbot.providers.openai import OpenAIProvider

agent = Agent(provider=OpenAIProvider(api_key=os.environ["OPENAI_API_KEY"]))
gw = Gateway(agent=agent)
gw.add_channel(CLIChannel())
asyncio.run(gw.run())
```

## Feishu Setup

1. Create an app at [open.feishu.cn](https://open.feishu.cn/app)
2. Enable **Bot** capability
3. Subscribe to `im.message.receive_v1` event
4. Set connection mode to **WebSocket** (长连接模式)
5. Grant permissions: `im:message`, `im:message:send_as_bot`

```bash
pip install "catbot[feishu]"
export FEISHU_APP_ID=cli_xxx
export FEISHU_APP_SECRET=xxx
export ANTHROPIC_API_KEY=sk-ant-xxx
python examples/feishu_bot.py
```

Features:
- 👀 reaction when processing, ✅ when done
- Group @ detection (`only_at_in_group=True`)
- Supports text, image, file messages
- No public server needed (WebSocket long connection)

## Custom Tools

```python
from catbot import tool, ToolRegistry

@tool()
async def search_web(query: str, max_results: int = 5) -> str:
    """Search the web for information.
    
    query: The search query.
    max_results: Maximum number of results to return.
    """
    # your implementation
    return results

tools = ToolRegistry()
tools.register(search_web)
agent = Agent(provider=..., tools=tools)
```

## Middleware

```python
from catbot import Gateway, rate_limit, allow_senders, log_messages

gw = Gateway(agent=agent)
gw.use(log_messages())                          # Log all messages
gw.use(rate_limit(max_per_minute=10))           # Rate limit
gw.use(allow_senders(["ou_abc123", "ou_xyz"]))  # Allowlist
```

## Session Keys (openclaw-compatible)

Session keys follow openclaw's format:

| Format | Example | Use case |
|--------|---------|----------|
| `agent:main:feishu:direct:<openId>` | DM with user | 1:1 chat |
| `agent:main:feishu:group:<chatId>` | Group chat | Multi-user |
| `agent:main:cli:direct:local` | CLI session | Testing |
| `agent:main:cron:cron:<jobId>` | Cron job | Scheduled tasks |

## Memory / Workspace

catbot uses the same workspace file convention as openclaw:

```
~/.catbot/workspace/
├── SOUL.md      # Agent personality (loaded into system prompt)
├── AGENTS.md    # Agent instructions
├── USER.md      # User context
└── memory/
    ├── MEMORY.md    # Long-term facts (loaded every turn)
    └── HISTORY.md   # Append-only event log (grep-searchable)
```

## Compaction

When the session token estimate exceeds 70% of the context window,
catbot automatically compacts old messages:

1. Summarize messages `[0 .. -keep_last]` via LLM
2. Replace with a summary system message
3. Keep the last `keep_last` messages verbatim

This mirrors openclaw's `compaction.ts` behavior.

## Providers

### OpenAI-compatible

```python
from catbot.providers.openai import OpenAIProvider

# OpenAI
p = OpenAIProvider(api_key="sk-...", model="gpt-4o")

# DeepSeek
p = OpenAIProvider(
    api_key="sk-...",
    api_base="https://api.deepseek.com/v1",
    model="deepseek-chat",
)
```

### Anthropic (with prompt caching)

```python
from catbot.providers.anthropic import AnthropicProvider

p = AnthropicProvider(
    api_key="sk-ant-...",
    model="claude-opus-4-5",
    enable_cache=True,   # Adds cache_control to system + last N user messages
)
```

## Installation

```bash
# Core (OpenAI)
pip install catbot

# With Anthropic
pip install "catbot[anthropic]"

# With Feishu
pip install "catbot[feishu]"

# Everything
pip install "catbot[all]"
```

## License

MIT
