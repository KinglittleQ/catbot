"""
Tool system — @tool decorator + ToolRegistry.

Design:
- @tool decorator auto-generates JSON schema from type annotations + docstring
- ToolRegistry: register / lookup / execute
- Supports sync and async handlers
- Built-in tools: read_file, write_file, exec_shell, web_search
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, get_type_hints

from loguru import logger


# ---------------------------------------------------------------------------
# Tool dataclass
# ---------------------------------------------------------------------------

class Tool:
    """A callable tool the agent can invoke."""

    def __init__(
        self,
        name: str,
        description: str,
        handler: Callable,
        parameters: dict[str, Any] | None = None,
        required: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.handler = handler
        self.parameters = parameters or {}
        self.required = required or []

    def to_schema(self) -> dict[str, Any]:
        """Return the OpenAI-compatible tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                },
            },
        }

    async def __call__(self, **kwargs: Any) -> Any:
        result = self.handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    def __repr__(self) -> str:
        return f"Tool(name={self.name!r})"


# ---------------------------------------------------------------------------
# @tool decorator
# ---------------------------------------------------------------------------

_PY_TO_JSON: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def tool(
    name: str | None = None,
    description: str | None = None,
) -> Callable:
    """Decorator that wraps a function as a Tool with auto-generated schema.

    Usage::

        @tool()
        def read_file(path: str) -> str:
            \"\"\"Read a file and return its contents.\"\"\"
            return Path(path).read_text()

        @tool(name="exec_shell", description="Run a shell command")
        async def run_cmd(command: str, timeout: int = 30) -> str:
            ...
    """
    def decorator(fn: Callable) -> Tool:
        tool_name = name or fn.__name__
        tool_desc = description or (inspect.getdoc(fn) or "").split("\n")[0]

        sig = inspect.signature(fn)
        try:
            hints = get_type_hints(fn)
        except Exception:
            hints = {}

        properties: dict[str, Any] = {}
        required_params: list[str] = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            json_type = _PY_TO_JSON.get(hints.get(param_name, str), "string")
            prop: dict[str, Any] = {"type": json_type}

            # Extract per-param description from docstring (Google style)
            doc = inspect.getdoc(fn) or ""
            for line in doc.splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{param_name}:") or stripped.startswith(f"{param_name} "):
                    desc_part = stripped.split(":", 1)[-1].strip()
                    if desc_part:
                        prop["description"] = desc_part
                    break

            properties[param_name] = prop

            if param.default is inspect.Parameter.empty:
                required_params.append(param_name)

        return Tool(
            name=tool_name,
            description=tool_desc,
            handler=fn,
            parameters=properties,
            required=required_params,
        )

    return decorator


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Registry for tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> None:
        self._tools[t.name] = t
        logger.debug(f"Registered tool: {t.name!r}")

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_schema() for t in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        t = self._tools.get(name)
        if not t:
            return f"Error: unknown tool '{name}'"
        try:
            result = await t(**arguments)
            return str(result) if not isinstance(result, str) else result
        except Exception as exc:
            logger.error(f"Tool '{name}' error: {exc}")
            return f"Error: {exc}"

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        names = list(self._tools.keys())
        return f"ToolRegistry(tools={names})"


# ---------------------------------------------------------------------------
# Built-in tools
# ---------------------------------------------------------------------------

@tool()
def read_file(path: str) -> str:
    """Read the contents of a file at the given path.

    path: The file path to read (absolute or relative).
    """
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except Exception as exc:
        return f"Error reading {path}: {exc}"


@tool()
def write_file(path: str, content: str) -> str:
    """Write content to a file, creating parent directories as needed.

    path: The file path to write to.
    content: The text content to write.
    """
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written {len(content)} bytes to {path}"
    except Exception as exc:
        return f"Error writing {path}: {exc}"


@tool()
def list_dir(path: str) -> str:
    """List the contents of a directory.

    path: The directory path to list.
    """
    try:
        p = Path(path).expanduser()
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
        lines = [f"{'[DIR] ' if e.is_dir() else '      '}{e.name}" for e in entries]
        return "\n".join(lines) if lines else "(empty)"
    except Exception as exc:
        return f"Error listing {path}: {exc}"


@tool()
async def exec_shell(command: str, timeout: int = 30, working_dir: str = "") -> str:
    """Execute a shell command and return its output.

    command: The shell command to run.
    timeout: Maximum seconds to wait (default 30).
    working_dir: Optional working directory.
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=working_dir or None,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Error: command timed out after {timeout}s"

        output = stdout.decode("utf-8", errors="replace").strip()
        rc = proc.returncode
        if rc != 0:
            return f"Exit code {rc}:\n{output}"
        return output or "(no output)"
    except Exception as exc:
        return f"Error: {exc}"


def get_builtin_tools() -> list[Tool]:
    """Return all built-in tools."""
    return [read_file, write_file, list_dir, exec_shell]  # type: ignore[list-item]
