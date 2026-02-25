"""
Memory system: MEMORY.md (long-term facts) + HISTORY.md (append-only log).

Inspired by openclaw's workspace bootstrap files:
- SOUL.md / AGENTS.md → system prompt identity
- MEMORY.md → long-term facts, loaded every turn
- HISTORY.md → event log, grep-searchable, NOT loaded automatically

The Memory class handles both files and provides helpers for
reading/writing/appending.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import aiofiles
from loguru import logger


class Memory:
    """Two-layer memory: MEMORY.md (facts) + HISTORY.md (log)."""

    def __init__(self, workspace_dir: str | Path = "~/.catbot/workspace") -> None:
        self.workspace = Path(workspace_dir).expanduser()
        self.memory_path = self.workspace / "memory" / "MEMORY.md"
        self.history_path = self.workspace / "memory" / "HISTORY.md"

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def init(self) -> None:
        """Create workspace directories and default files if missing."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "memory").mkdir(parents=True, exist_ok=True)

        if not self.memory_path.exists():
            self.memory_path.write_text(
                "# Long-term Memory\n\n(No memories yet.)\n",
                encoding="utf-8",
            )
        if not self.history_path.exists():
            self.history_path.write_text(
                "# History Log\n\n",
                encoding="utf-8",
            )
        logger.debug(f"Memory initialized at {self.workspace}")

    # ------------------------------------------------------------------
    # MEMORY.md — long-term facts
    # ------------------------------------------------------------------

    def get_memory(self) -> str:
        """Read MEMORY.md synchronously (used during system prompt build)."""
        try:
            return self.memory_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except Exception as exc:
            logger.warning(f"Failed to read MEMORY.md: {exc}")
            return ""

    async def write_memory(self, content: str) -> None:
        """Overwrite MEMORY.md with new content."""
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with aiofiles.open(self.memory_path, "w", encoding="utf-8") as f:
                await f.write(content)
        except Exception as exc:
            logger.error(f"Failed to write MEMORY.md: {exc}")

    async def update_memory(self, section: str, content: str) -> None:
        """Append or update a named section in MEMORY.md.

        If a section with the given heading exists, it is replaced.
        Otherwise, the section is appended at the end.
        """
        current = self.get_memory()
        heading = f"## {section}"

        if heading in current:
            # Replace existing section
            lines = current.split("\n")
            new_lines: list[str] = []
            in_section = False
            for line in lines:
                if line.startswith(heading):
                    in_section = True
                    new_lines.append(heading)
                    new_lines.append(content)
                    continue
                if in_section and line.startswith("## "):
                    in_section = False
                if not in_section:
                    new_lines.append(line)
            updated = "\n".join(new_lines)
        else:
            updated = current.rstrip() + f"\n\n{heading}\n{content}\n"

        await self.write_memory(updated)

    # ------------------------------------------------------------------
    # HISTORY.md — append-only event log
    # ------------------------------------------------------------------

    async def append_history(self, entry: str) -> None:
        """Append a timestamped entry to HISTORY.md."""
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        line = f"\n## {ts}\n{entry.strip()}\n"
        try:
            async with aiofiles.open(self.history_path, "a", encoding="utf-8") as f:
                await f.write(line)
        except Exception as exc:
            logger.error(f"Failed to append to HISTORY.md: {exc}")

    def grep_history(self, pattern: str, max_results: int = 20) -> list[str]:
        """Search HISTORY.md for lines matching a pattern (case-insensitive)."""
        try:
            text = self.history_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except Exception as exc:
            logger.warning(f"Failed to read HISTORY.md: {exc}")
            return []

        pattern_lower = pattern.lower()
        matches = [
            line for line in text.splitlines()
            if pattern_lower in line.lower()
        ]
        return matches[:max_results]

    # ------------------------------------------------------------------
    # Workspace bootstrap files (openclaw-style)
    # ------------------------------------------------------------------

    def get_soul(self) -> str:
        """Read SOUL.md (agent identity/personality)."""
        soul_path = self.workspace / "SOUL.md"
        try:
            return soul_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def get_agents_md(self) -> str:
        """Read AGENTS.md (agent instructions)."""
        agents_path = self.workspace / "AGENTS.md"
        try:
            return agents_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def get_user_md(self) -> str:
        """Read USER.md (user context/preferences)."""
        user_path = self.workspace / "USER.md"
        try:
            return user_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
