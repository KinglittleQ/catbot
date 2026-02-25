"""
Feishu bot — production entry point.

Reads config from environment variables (.env file or system env).
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv  # pip install python-dotenv
from loguru import logger

# Load .env from current directory or parent
load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        print(f"ERROR: {key} is not set. Copy .env.example to .env and fill in values.")
        sys.exit(1)
    return val


async def main() -> None:
    # ----------------------------------------------------------------
    # Config from env
    # ----------------------------------------------------------------
    feishu_app_id = _require("FEISHU_APP_ID")
    feishu_app_secret = _require("FEISHU_APP_SECRET")

    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    openai_base = os.getenv("OPENAI_BASE_URL", "")

    if not anthropic_key and not openai_key:
        print("ERROR: Set ANTHROPIC_API_KEY or OPENAI_API_KEY")
        sys.exit(1)

    # ----------------------------------------------------------------
    # Imports (lazy, so missing optional deps give clear errors)
    # ----------------------------------------------------------------
    from catbot import Agent, AgentConfig, Gateway, GatewayConfig, Memory
    from catbot import rate_limit, log_messages
    from catbot.channels.feishu import FeishuChannel
    from catbot.tools import get_builtin_tools, ToolRegistry

    # ----------------------------------------------------------------
    # Provider
    # ----------------------------------------------------------------
    if anthropic_key:
        from catbot.providers.anthropic import AnthropicProvider
        provider = AnthropicProvider(
            api_key=anthropic_key,
            model=os.getenv("MODEL", "claude-haiku-4-5"),
            enable_cache=True,
        )
        logger.info("Provider: Anthropic")
    else:
        from catbot.providers.openai import OpenAIProvider
        provider = OpenAIProvider(
            api_key=openai_key,
            model=os.getenv("MODEL", "gpt-4o-mini"),
            api_base=openai_base or None,
        )
        logger.info("Provider: OpenAI-compatible")

    # ----------------------------------------------------------------
    # Memory
    # ----------------------------------------------------------------
    workspace = os.getenv("WORKSPACE_DIR", "~/.catbot/workspace")
    memory = Memory(workspace_dir=workspace)
    memory.init()

    # ----------------------------------------------------------------
    # Tools
    # ----------------------------------------------------------------
    tools = ToolRegistry()
    for t in get_builtin_tools():
        tools.register(t)

    # ----------------------------------------------------------------
    # Agent
    # ----------------------------------------------------------------
    soul_text = memory.get_soul() or (
        "You are a helpful AI assistant. "
        "Be concise and friendly. Reply in the user's language."
    )
    agent = Agent(
        provider=provider,
        tools=tools,
        memory=memory,
        config=AgentConfig(
            agent_id="catbot",
            system_prompt=soul_text,
            max_turns=int(os.getenv("MAX_TURNS", "10")),
            context_window=int(os.getenv("CONTEXT_WINDOW", "200000")),
            timezone="Asia/Shanghai",
        ),
    )

    # ----------------------------------------------------------------
    # Channel
    # ----------------------------------------------------------------
    feishu = FeishuChannel(
        app_id=feishu_app_id,
        app_secret=feishu_app_secret,
        only_at_in_group=os.getenv("FEISHU_GROUP_MENTION_ONLY", "true").lower() != "false",
    )

    # ----------------------------------------------------------------
    # Gateway
    # ----------------------------------------------------------------
    gw = Gateway(
        agent=agent,
        memory=memory,
        config=GatewayConfig(
            agent_id="catbot",
            daily_reset=os.getenv("DAILY_RESET", "false").lower() == "true",
        ),
    )
    gw.add_channel(feishu)
    gw.use(log_messages())
    gw.use(rate_limit(max_per_minute=int(os.getenv("RATE_LIMIT", "20"))))

    logger.info("catbot starting...")
    await gw.run()


if __name__ == "__main__":
    # Configure loguru
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        level=os.getenv("LOG_LEVEL", "INFO"),
    )
    logger.add(
        Path("~/.catbot/catbot.log").expanduser(),
        rotation="10 MB",
        retention="7 days",
        level="DEBUG",
    )

    asyncio.run(main())
