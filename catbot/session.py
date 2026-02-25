"""
Session management with JSONL persistence.

Inspired by openclaw's session design:
- Session key format: "agent:<agentId>:<channel>:<type>:<id>"
  e.g. "agent:main:feishu:direct:ou_xxx" or "agent:main:feishu:group:oc_xxx"
- Incremental JSONL persistence (append-only, never rewrite)
- Compaction: summarize old messages to stay within context window
- Daily reset support
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import aiofiles
from loguru import logger


# ---------------------------------------------------------------------------
# Message dataclass
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    name: str
    content: str


@dataclass
class Message:
    role: Literal["user", "assistant", "tool", "system"]
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    # Metadata (not sent to LLM)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "timestamp": self.timestamp}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = [
                {"call_id": tc.call_id, "name": tc.name, "arguments": tc.arguments}
                for tc in self.tool_calls
            ]
        if self.tool_results:
            d["tool_results"] = [
                {"call_id": tr.call_id, "name": tr.name, "content": tr.content}
                for tr in self.tool_results
            ]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        tool_calls = [
            ToolCall(tc["call_id"], tc["name"], tc.get("arguments", {}))
            for tc in data.get("tool_calls", [])
        ]
        tool_results = [
            ToolResult(tr["call_id"], tr["name"], tr.get("content", ""))
            for tr in data.get("tool_results", [])
        ]
        return cls(
            role=data["role"],
            content=data.get("content"),
            tool_calls=tool_calls,
            tool_results=tool_results,
            timestamp=data.get("timestamp", ""),
        )


# ---------------------------------------------------------------------------
# Session key helpers (openclaw-style)
# ---------------------------------------------------------------------------

def make_session_key(
    agent_id: str,
    channel: str,
    chat_type: Literal["direct", "group", "channel", "cron", "subagent"],
    chat_id: str,
) -> str:
    """Build a canonical session key.

    Format: agent:<agentId>:<channel>:<type>:<chatId>
    Examples:
        agent:main:feishu:direct:ou_abc123
        agent:main:feishu:group:oc_xyz789
        agent:main:cli:direct:local
        agent:main:cron:cron:daily_report
    """
    return f"agent:{agent_id}:{channel}:{chat_type}:{chat_id}"


def parse_session_key(key: str) -> dict[str, str] | None:
    """Parse a session key into components. Returns None if invalid."""
    parts = key.split(":")
    if len(parts) < 5 or parts[0] != "agent":
        return None
    return {
        "agent_id": parts[1],
        "channel": parts[2],
        "chat_type": parts[3],
        "chat_id": ":".join(parts[4:]),  # chat_id may contain colons
    }


def is_group_session(key: str) -> bool:
    parsed = parse_session_key(key)
    return parsed is not None and parsed["chat_type"] in ("group", "channel")


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class Session:
    """A conversation session backed by an append-only JSONL file.

    Design notes (from openclaw):
    - Never rewrite the file; always append
    - last_consolidated tracks how many messages have been compacted
    - Compaction replaces old messages with a summary message
    """

    def __init__(self, key: str, path: Path) -> None:
        self.key = key
        self.path = path
        self._messages: list[Message] = []
        self._loaded = False
        self.last_consolidated: int = 0  # index up to which messages are compacted

    async def load(self) -> None:
        """Load messages from the JSONL file."""
        if not self.path.exists():
            self._messages = []
            self._loaded = True
            return

        messages: list[Message] = []
        try:
            async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
                async for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        # Skip compaction metadata lines
                        if data.get("_type") == "meta":
                            self.last_consolidated = data.get("last_consolidated", 0)
                            continue
                        messages.append(Message.from_dict(data))
                    except (json.JSONDecodeError, KeyError) as exc:
                        logger.warning(f"Skipping malformed line in {self.path}: {exc}")
        except Exception as exc:
            logger.error(f"Failed to load session {self.key}: {exc}")

        self._messages = messages
        self._loaded = True
        logger.debug(f"Loaded session {self.key!r}: {len(messages)} messages")

    async def _append_raw(self, data: dict[str, Any]) -> None:
        """Append a raw JSON line to the file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with aiofiles.open(self.path, "a", encoding="utf-8") as f:
                await f.write(json.dumps(data, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.error(f"Failed to persist to session {self.key}: {exc}")

    def get_messages(self) -> list[Message]:
        """Return all messages (for passing to LLM)."""
        if not self._loaded:
            logger.warning(f"Session {self.key!r} not loaded")
            return []
        return list(self._messages)

    def append(self, msg: Message) -> None:
        """Append in-memory only (no I/O)."""
        self._messages.append(msg)

    async def add(self, msg: Message) -> None:
        """Append and persist."""
        self.append(msg)
        await self._append_raw(msg.to_dict())

    async def compact(self, summary: str, keep_last: int = 10) -> None:
        """Replace old messages with a summary, keep the most recent ones.

        This mirrors openclaw's compaction: old messages are summarized
        into a single system message prepended to the kept messages.
        """
        if len(self._messages) <= keep_last:
            return

        to_compact = self._messages[:-keep_last]
        to_keep = self._messages[-keep_last:]

        summary_msg = Message(
            role="system",
            content=f"[Summary of {len(to_compact)} earlier messages]\n{summary}",
        )
        self._messages = [summary_msg] + to_keep
        self.last_consolidated = len(to_compact)

        # Rewrite the file with compacted state
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self.path, "w", encoding="utf-8") as f:
                meta = {"_type": "meta", "last_consolidated": self.last_consolidated}
                await f.write(json.dumps(meta) + "\n")
                for msg in self._messages:
                    await f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")
            logger.info(f"Session {self.key!r} compacted: {len(to_compact)} → summary + {len(to_keep)} kept")
        except Exception as exc:
            logger.error(f"Compaction write failed for {self.key}: {exc}")

    async def reset(self) -> None:
        """Clear all messages."""
        self._messages = []
        self.last_consolidated = 0
        if self.path.exists():
            try:
                self.path.unlink()
                logger.info(f"Session {self.key!r} reset")
            except Exception as exc:
                logger.error(f"Failed to delete session file: {exc}")

    async def daily_reset(self) -> bool:
        """Reset if last modified on a previous calendar day. Returns True if reset."""
        if not self.path.exists():
            return False
        try:
            mtime = datetime.fromtimestamp(self.path.stat().st_mtime).date()
            if mtime < date.today():
                await self.reset()
                return True
        except Exception as exc:
            logger.warning(f"daily_reset check failed: {exc}")
        return False

    def estimate_tokens(self) -> int:
        """Rough token estimate for all messages (chars / 4)."""
        total = 0
        for msg in self._messages:
            if msg.content:
                total += len(msg.content) // 4
            for tc in msg.tool_calls:
                total += len(json.dumps(tc.arguments)) // 4 + 10
            for tr in msg.tool_results:
                total += len(tr.content) // 4 + 10
        return total

    def __len__(self) -> int:
        return len(self._messages)

    def __repr__(self) -> str:
        return f"Session(key={self.key!r}, messages={len(self._messages)}, tokens≈{self.estimate_tokens()})"


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    """Manages sessions keyed by canonical session keys."""

    def __init__(self, base_dir: str | Path = "~/.catbot/sessions") -> None:
        self.base_dir = Path(base_dir).expanduser()
        self._cache: dict[str, Session] = {}

    def _key_to_path(self, key: str) -> Path:
        """Convert session key to a safe file path."""
        safe = key.replace(":", "__").replace("/", "_").replace("..", "")
        return self.base_dir / f"{safe}.jsonl"

    async def get(
        self,
        key: str,
        *,
        daily_reset: bool = False,
    ) -> Session:
        """Get or create a session."""
        if key not in self._cache:
            path = self._key_to_path(key)
            session = Session(key, path)
            await session.load()
            self._cache[key] = session

        session = self._cache[key]
        if daily_reset and await session.daily_reset():
            await session.load()

        return session

    async def delete(self, key: str) -> None:
        if key in self._cache:
            await self._cache[key].reset()
            del self._cache[key]

    def list_keys(self) -> list[str]:
        return list(self._cache.keys())
