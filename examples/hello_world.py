"""
Hello World example — minimal catbot usage with CLI channel.

Run:
    export OPENAI_API_KEY=sk-...
    python examples/hello_world.py
"""

import asyncio
import os

from catbot import Agent, AgentConfig, Gateway, GatewayConfig
from catbot.channels.cli import CLIChannel
from catbot.providers.openai import OpenAIProvider
from catbot.tools import get_builtin_tools, ToolRegistry


async def main() -> None:
    # 1. Provider
    provider = OpenAIProvider(
        api_key=os.environ["OPENAI_API_KEY"],
        model="gpt-4o-mini",
    )

    # 2. Tools
    tools = ToolRegistry()
    for t in get_builtin_tools():
        tools.register(t)

    # 3. Agent
    agent = Agent(
        provider=provider,
        tools=tools,
        config=AgentConfig(system_prompt="You are a helpful assistant."),
    )

    # 4. Gateway + CLI channel
    gw = Gateway(agent=agent, config=GatewayConfig())
    gw.add_channel(CLIChannel())

    # 5. Run!
    await gw.run()


if __name__ == "__main__":
    asyncio.run(main())
