"""
Feishu bot example.

Setup:
    1. Create a Feishu app at https://open.feishu.cn/
    2. Enable "Bot" capability
    3. Subscribe to "Receive messages" event (im.message.receive_v1)
    4. Enable WebSocket long connection
    5. Set env vars and run:

    export FEISHU_APP_ID=cli_xxxx
    export FEISHU_APP_SECRET=xxxx
    export ANTHROPIC_API_KEY=sk-ant-xxxx
    python examples/feishu_bot.py
"""

import asyncio
import os

from loguru import logger

from catbot import (
    Agent,
    AgentConfig,
    AnthropicProvider,
    FeishuChannel,
    Gateway,
    Memory,
    SessionManager,
)
from catbot.tools import default_registry


async def main() -> None:
    # --- Provider ---
    provider = AnthropicProvider(
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        model="claude-3-5-sonnet-20241022",
        enable_caching=True,
    )

    # --- Memory ---
    memory = Memory(memory_file="MEMORY.md", history_file="HISTORY.md")

    # --- Tools ---
    tools = default_registry()

    # --- Agent ---
    agent = Agent(
        provider=provider,
        tools=tools,
        memory=memory,
        config=AgentConfig(
            system_prompt=(
                "You are a helpful Feishu bot assistant. "
                "You can read/write files and execute shell commands when needed. "
                "Be concise and friendly."
            ),
            max_turns=8,
            max_tokens=2048,
        ),
    )

    # --- Session manager ---
    sessions = SessionManager(base_dir="./sessions")

    # --- Gateway with middleware ---
    gateway = Gateway(agent=agent, session_manager=sessions, daily_reset=True)

    # Rate limit: 20 messages per user per minute
    gateway.use(Gateway.rate_limit(max_calls=20, window_seconds=60))

    # --- Feishu channel ---
    feishu = FeishuChannel(
        app_id=os.environ.get("FEISHU_APP_ID"),
        app_secret=os.environ.get("FEISHU_APP_SECRET"),
        respond_in_group_only_when_mentioned=True,
    )
    gateway.add_channel(feishu)

    logger.info("Feishu bot starting...")
    await gateway.start()


if __name__ == "__main__":
    asyncio.run(main())
