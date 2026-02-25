"""
Hello World example - minimal catbot usage.

Run:
    export OPENAI_API_KEY=sk-...
    python examples/hello_world.py
"""

import asyncio
from catbot import Agent, AgentConfig, OpenAIProvider, CLIChannel, Gateway, SessionManager


async def main() -> None:
    # 1. Create a provider
    provider = OpenAIProvider(model="gpt-4o-mini")

    # 2. Create an agent
    agent = Agent(
        provider=provider,
        config=AgentConfig(system_prompt="You are a helpful assistant. Be concise."),
    )

    # 3. Quick one-shot call (no channel needed)
    reply = await agent.run("Hello! What is 2 + 2?")
    print(f"Agent: {reply}")

    # 4. Interactive CLI session
    print("\n--- Interactive mode (Ctrl+C to quit) ---")
    sessions = SessionManager(base_dir="./sessions")
    gateway = Gateway(agent=agent, session_manager=sessions)

    cli = CLIChannel()
    gateway.add_channel(cli)
    await gateway.start()


if __name__ == "__main__":
    asyncio.run(main())
