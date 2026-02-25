"""
Feishu bot example.

Setup:
1. Create a Feishu app at https://open.feishu.cn/app
2. Enable "Bot" capability
3. Subscribe to im.message.receive_v1 event
4. Set connection mode to WebSocket (长连接)
5. Set environment variables and run this script

Run:
    export FEISHU_APP_ID=cli_xxx
    export FEISHU_APP_SECRET=xxx
    export ANTHROPIC_API_KEY=sk-ant-xxx
    python examples/feishu_bot.py
"""

import asyncio
import os

from catbot import Agent, AgentConfig, Gateway, GatewayConfig, Memory
from catbot import rate_limit, log_messages
from catbot.channels.feishu import FeishuChannel
from catbot.providers.anthropic import AnthropicProvider
from catbot.tools import get_builtin_tools, ToolRegistry


SOUL = """
You are a helpful AI assistant. You are concise, accurate, and friendly.
You can read/write files and run shell commands when needed.
""".strip()


async def main() -> None:
    # 1. Provider (Anthropic with prompt caching)
    provider = AnthropicProvider(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        model="claude-opus-4-5",
        enable_cache=True,
    )

    # 2. Memory (workspace files)
    memory = Memory(workspace_dir="~/.catbot/workspace")
    memory.init()

    # 3. Tools
    tools = ToolRegistry()
    for t in get_builtin_tools():
        tools.register(t)

    # 4. Agent
    agent = Agent(
        provider=provider,
        tools=tools,
        memory=memory,
        config=AgentConfig(
            agent_id="feishu-bot",
            system_prompt=SOUL,
            model="claude-opus-4-5",
            max_turns=10,
            context_window=200_000,
            timezone="Asia/Shanghai",
        ),
    )

    # 5. Feishu channel
    feishu = FeishuChannel(
        app_id=os.environ["FEISHU_APP_ID"],
        app_secret=os.environ["FEISHU_APP_SECRET"],
        only_at_in_group=True,   # Only respond when @mentioned in groups
    )

    # 6. Gateway with middleware
    gw = Gateway(
        agent=agent,
        memory=memory,
        config=GatewayConfig(
            agent_id="feishu-bot",
            daily_reset=False,
        ),
    )
    gw.add_channel(feishu)
    gw.use(log_messages())
    gw.use(rate_limit(max_per_minute=20))

    print("Feishu bot starting...")
    await gw.run()


if __name__ == "__main__":
    asyncio.run(main())
