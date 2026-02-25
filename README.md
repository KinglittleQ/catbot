# catbot

> A minimal Python agent framework with native Feishu (Lark) support.

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        Gateway                          │
│   (middleware chain: rate-limit, auth, logging, ...)    │
└──────────────┬──────────────────────────┬───────────────┘
               │                          │
    ┌──────────▼──────────┐   ┌───────────▼────────────┐
    │   FeishuChannel     │   │      CLIChannel         │
    │ (WebSocket / lark)  │   │   (stdin / stdout)      │
    └─────────────────────┘   └────────────────────────┘
               │
    ┌──────────▼──────────────────────────────────────┐
    │                    Agent                        │
    │  system + memory + session history → LLM call   │
    │  → tool calls → loop until stop                 │
    └──────┬──────────────────────┬───────────────────┘
           │                      │
  ┌────────▼───────┐   ┌──────────▼──────────┐
  │  LLMProvider   │   │    ToolRegistry      │
  │  OpenAI /      │   │  read_file           │
  │  Anthropic     │   │  write_file          │
  └────────────────┘   │  exec_shell          │
                        │  web_search          │
                        │  + custom tools      │
                        └──────────────────────┘
           │
  ┌────────▼───────────────────┐
  │  SessionManager            │
  │  (JSONL per chat)          │
  ├────────────────────────────┤
  │  Memory                    │
  │  MEMORY.md (long-term)     │
  │  HISTORY.md (append log)   │
  └────────────────────────────┘
```

## Quick Start

```bash
pip install catbot
export OPENAI_API_KEY=sk-...
```

```python
import asyncio
from catbot import Agent, OpenAIProvider

agent = Agent(provider=OpenAIProvider())
reply = asyncio.run(agent.run("What is the capital of France?"))
print(reply)  # Paris
```

## Interactive CLI

```python
import asyncio
from catbot import Agent, OpenAIProvider, CLIChannel, Gateway

async def main():
    agent = Agent(provider=OpenAIProvider(model="gpt-4o-mini"))
    gateway = Gateway(agent=agent)
    gateway.add_channel(CLIChannel())
    await gateway.start()

asyncio.run(main())
```

## Feishu Bot

### 1. Create a Feishu App

1. Go to [Feishu Developer Console](https://open.feishu.cn/)
2. Create a new app → enable **Bot** capability
3. Subscribe to event: `im.message.receive_v1`
4. Enable **WebSocket** long connection (no public server needed!)
5. Copy **App ID** and **App Secret**

### 2. Configure & Run

```bash
export FEISHU_APP_ID=cli_xxxxxxxxxxxx
export FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxx
export ANTHROPIC_API_KEY=sk-ant-...
python examples/feishu_bot.py
```

See [`examples/feishu_bot.py`](examples/feishu_bot.py) for the full example.

## Custom Tools

```python
from catbot import tool, ToolRegistry, Agent, OpenAIProvider

@tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"Sunny, 22°C in {city}"  # Replace with real API call

registry = ToolRegistry()
registry.register(get_weather)

agent = Agent(provider=OpenAIProvider(), tools=registry)
```

## Memory

```python
from catbot import Agent, Memory, OpenAIProvider

memory = Memory(memory_file="MEMORY.md", history_file="HISTORY.md")

# MEMORY.md is loaded into every system prompt
# HISTORY.md is an append-only log of all conversations

agent = Agent(provider=OpenAIProvider(), memory=memory)
```

## Sessions

```python
from catbot import SessionManager

sessions = SessionManager(base_dir="./sessions")
session = await sessions.get("feishu:oc_xxxx", daily_reset=True)
```

Sessions are stored as JSONL files (one message per line), keyed by `channel:chat_id`.

## Middleware

```python
from catbot import Gateway

gateway = Gateway(agent=agent)

# Built-in rate limiting
gateway.use(Gateway.rate_limit(max_calls=10, window_seconds=60))

# Built-in user allowlist
gateway.use(Gateway.allow_users(["user_id_1", "user_id_2"]))

# Custom middleware
async def logging_middleware(msg, next_fn):
    print(f"[{msg.channel}] {msg.user_id}: {msg.text}")
    result = await next_fn(msg)
    print(f"[{msg.channel}] bot: {result}")
    return result

gateway.use(logging_middleware)
```

## API Reference

### `Agent`

```python
Agent(
    provider: LLMProvider,
    tools: ToolRegistry | None = None,
    memory: Memory | None = None,
    config: AgentConfig | None = None,
)

await agent.run(
    user_message: str,
    session: Session | None = None,
    extra_context: str = "",
) -> str
```

### `AgentConfig`

| Field | Default | Description |
|-------|---------|-------------|
| `system_prompt` | `"You are a helpful assistant."` | System prompt |
| `max_turns` | `10` | Max tool-call iterations |
| `max_tokens` | `4096` | Max tokens per LLM call |
| `temperature` | `0.7` | Sampling temperature |
| `model` | `""` | Model override |

### `OpenAIProvider`

```python
OpenAIProvider(
    api_key: str | None = None,   # or OPENAI_API_KEY env var
    base_url: str | None = None,  # custom endpoint (DeepSeek, etc.)
    model: str = "gpt-4o",
)
```

### `AnthropicProvider`

```python
AnthropicProvider(
    api_key: str | None = None,   # or ANTHROPIC_API_KEY env var
    model: str = "claude-3-5-sonnet-20241022",
    enable_caching: bool = True,  # prompt caching
)
```

### `FeishuChannel`

```python
FeishuChannel(
    app_id: str | None = None,    # or FEISHU_APP_ID env var
    app_secret: str | None = None, # or FEISHU_APP_SECRET env var
    respond_in_group_only_when_mentioned: bool = True,
)
```

### `Gateway`

```python
Gateway(
    agent: Agent,
    session_manager: SessionManager | None = None,
    daily_reset: bool = False,
)

gateway.add_channel(channel: Channel)
gateway.use(middleware: MiddlewareFn)
await gateway.start()
await gateway.stop()
```

## Project Structure

```
catbot/
├── catbot/
│   ├── agent.py          # Agent loop (core)
│   ├── tools.py          # @tool decorator + ToolRegistry + built-ins
│   ├── session.py        # JSONL session persistence
│   ├── memory.py         # MEMORY.md + HISTORY.md
│   ├── gateway.py        # Multi-channel router + middleware
│   ├── providers/
│   │   ├── base.py       # LLMProvider ABC
│   │   ├── openai.py     # OpenAI / compatible
│   │   └── anthropic.py  # Anthropic Claude
│   └── channels/
│       ├── base.py       # Channel ABC
│       ├── feishu.py     # Feishu WebSocket
│       └── cli.py        # CLI (local testing)
├── examples/
│   ├── hello_world.py
│   └── feishu_bot.py
└── pyproject.toml
```

## License

MIT
