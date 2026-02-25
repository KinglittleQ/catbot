"""
Feishu Bot — production-ready Feishu bot using catbot.

Setup:
    1. Create a Feishu app at https://open.feishu.cn/
    2. Enable "Bot" capability
    3. Subscribe to "Receive messages" event (im.message.receive_v1)
    4. Set env vars:
         FEISHU_APP_ID=cli_xxx
         FEISHU_APP_SECRET=xxx
         ANTHROPIC_API_KEY=sk-ant-xxx  (or OPENAI_API_KEY)

Run:
    python examples/feishu_bot.py
"""

import asyncio
import os

from catbot import Gateway, GatewayConfig, AnthropicProvider
from catbot.tools import tool, get_builtin_tools


# ---------------------------------------------------------------------------
# Custom tools
# ---------------------------------------------------------------------------

@tool()
async def get_weather(city: str) -> str:
    """Get current weather for a city.

    city: City name, e.g. "Beijing" or "海口"
    """
    # Replace with a real weather API call
    return f"Weather in {city}: 25°C, sunny ☀️"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    # Provider (Anthropic with prompt caching)
    provider = AnthropicProvider(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        model="claude-haiku-4-5",
        enable_cache=True,
    )

    # Gateway config
    config = GatewayConfig(
        agent_id="main",
        feishu_app_id=os.environ["FEISHU_APP_ID"],
        feishu_app_secret=os.environ["FEISHU_APP_SECRET"],
        feishu_bot_name=os.getenv("FEISHU_BOT_NAME", ""),
        feishu_group_mention_only=True,   # In groups, only respond when @mentioned
        system_prompt=(
            "You are a helpful AI assistant. "
            "Be concise and friendly. Reply in the user's language."
        ),
        workspace_dir="~/.catbot/workspace",
        compaction_enabled=True,
        context_window=200_000,
        daily_reset=False,
    )

    # Build gateway
    gw = Gateway(provider=provider, config=config)

    # Register tools
    gw.add_builtin_tools()
    gw.add_tool(get_weather)

    # Start (blocks until interrupted)
    await gw.start()


if __name__ == "__main__":
    asyncio.run(main())
