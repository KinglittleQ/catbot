"""
catbot CLI entry point.

Usage:
    catbot                  # Start interactive CLI with default settings
    catbot --help
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="catbot",
        description="catbot - minimal Python agent framework",
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic"],
        default="openai",
        help="LLM provider to use (default: openai)",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Model name override",
    )
    parser.add_argument(
        "--system",
        default="You are a helpful assistant.",
        help="System prompt",
    )
    parser.add_argument(
        "--session-dir",
        default="./sessions",
        help="Directory for session files (default: ./sessions)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="catbot 0.1.0",
    )

    args = parser.parse_args()

    asyncio.run(_run_cli(args))


async def _run_cli(args: argparse.Namespace) -> None:
    from catbot.agent import Agent, AgentConfig
    from catbot.channels.cli import CLIChannel
    from catbot.gateway import Gateway
    from catbot.session import SessionManager
    from catbot.tools import default_registry

    # Build provider
    if args.provider == "anthropic":
        from catbot.providers.anthropic import AnthropicProvider
        provider = AnthropicProvider(
            model=args.model or "claude-3-5-sonnet-20241022",
        )
    else:
        from catbot.providers.openai import OpenAIProvider
        provider = OpenAIProvider(
            model=args.model or "gpt-4o-mini",
        )

    tools = default_registry()
    agent = Agent(
        provider=provider,
        tools=tools,
        config=AgentConfig(system_prompt=args.system),
    )

    sessions = SessionManager(base_dir=args.session_dir)
    gateway = Gateway(agent=agent, session_manager=sessions)
    cli = CLIChannel()
    gateway.add_channel(cli)

    await gateway.start()


if __name__ == "__main__":
    main()
