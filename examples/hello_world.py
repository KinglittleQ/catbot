"""
Hello World — the simplest catbot example.

Run:
    export OPENAI_API_KEY=sk-...
    python examples/hello_world.py
"""

import asyncio
import os

from catbot import Agent, AgentConfig, OpenAIProvider, ToolRegistry, SessionManager
from catbot import make_session_key, Session
from catbot.tools import get_builtin_tools


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

    # 4. Session
    sessions = SessionManager("~/.catbot/sessions")
    key = make_session_key("main", "cli", "direct", "demo")
    session = await sessions.get(key)

    # 5. Chat loop
    print("catbot hello world — Ctrl+C to exit\n")
    while True:
        try:
            user = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        reply = await agent.run(user, session)
        print(f"Bot> {reply}\n")


if __name__ == "__main__":
    asyncio.run(main())
