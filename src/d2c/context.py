"""Context assembly — system prompt, environment info, CLAUDE.md.

Paper Section 7.1: context assembly is a memoized state loader, not a routing hub.
getSystemContext() computes session-level system context including git status,
getUserContext() loads CLAUDE.md and date. Both are cached for reuse.
System context is appended to the system prompt; user context is prepended as a user message.
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from d2c.config import Config


# ── System prompt ────────────────────────────────────────────────────

def getSystemPrompt() -> str:
    """Base system prompt. Paper: assembly via asSystemPrompt()."""
    return (
        "You are d2c, an interactive CLI agent that helps users with software engineering tasks.\n"
        "Use the tools available to you to assist the user.\n\n"
        "## Environment\n"
        "You have been invoked in the following environment:\n"
        "- Primary working directory: {cwd}\n"
        "- Platform: {platform}\n"
        "- Shell: bash (use Unix shell syntax)\n\n"
        "## Using your tools\n"
        "- Read files with the Read tool. Do not use Bash for reading files.\n"
        "- Edit files with the Edit tool for exact string replacements.\n"
        "- Write new files with the Write tool.\n"
        "- Run shell commands with the Bash tool.\n\n"
        "## Tone and style\n"
        "- Be concise and direct.\n"
        "- Do not use emojis.\n"
        "- Reference code paths as file_path:line_number.\n"
    )


# ── System context ───────────────────────────────────────────────────

@dataclass
class SystemContext:
    git_status: str | None
    platform: str
    cwd: str
    date: str

    def format(self) -> str:
        parts = [
            f"Working directory: {self.cwd}",
            f"Platform: {self.platform}",
            f"Date: {self.date}",
        ]
        if self.git_status:
            parts.append(f"Git: {self.git_status}")
        return "\n".join(parts)


_system_context_cache: tuple[str, SystemContext] | None = None


def getSystemContext(config: "Config") -> SystemContext:
    """Memoized — paper: git status and env cached, not recomputed every turn."""
    global _system_context_cache
    cache_key = str(config.cwd)

    if _system_context_cache is None or _system_context_cache[0] != cache_key:
        git = _get_git_status(config.cwd)
        ctx = SystemContext(
            git_status=git,
            platform=platform.system(),
            cwd=str(config.cwd),
            date=datetime.now().strftime("%Y-%m-%d"),
        )
        _system_context_cache = (cache_key, ctx)

    return _system_context_cache[1]


def _get_git_status(cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            branch = result.stdout.strip()
            return f"on branch '{branch}'"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


# ── User context ─────────────────────────────────────────────────────

def getUserContext(config: "Config") -> str:
    """Load CLAUDE.md hierarchy + current date.

    Phase 6: 4-level memory hierarchy loaded eagerly at session start.
    Phase 7: Managed/user policies added here.
    """
    from d2c.memory import loadClaudeMdHierarchy

    parts = [f"Today's date: {datetime.now().strftime('%Y-%m-%d')}"]
    cwd = getattr(config, 'cwd', Path.cwd())
    memory = loadClaudeMdHierarchy(cwd)
    if memory:
        parts.append(memory)
    return "\n\n".join(parts)


# ── Message assembly ─────────────────────────────────────────────────

def assembleMessages(
    system_prompt: str,
    system_context: SystemContext,
    user_context: str,
    history: list[dict],
) -> tuple[str, list[dict]]:
    """Return (full_system_prompt, messages_for_api).

    Paper: system context is appended to the system prompt;
    user context is prepended to the message array as a user-context message.
    """
    full_prompt = appendSystemContext(system_prompt, system_context)
    messages = prependUserContext(history, user_context)
    return full_prompt, messages


def appendSystemContext(prompt: str, ctx: SystemContext) -> str:
    """Append system context to the base prompt."""
    return prompt.format(
        cwd=ctx.cwd,
        platform=ctx.platform,
    ) + f"\n\n{ctx.format()}"


def prependUserContext(messages: list[dict], user_context: str) -> list[dict]:
    """Paper: CLAUDE.md is user-context message, not system-prompt content."""
    return [{"role": "user", "content": user_context}] + messages


# ── Token estimation ─────────────────────────────────────────────────

def estimate_tokens(messages: list[dict], chars_per_token: float = 3.5) -> int:
    """Rough token estimate: total chars / chars_per_token."""
    import json
    total = 0
    for m in messages:
        if isinstance(m.get("content"), list):
            total += len(json.dumps(m["content"]))
        else:
            total += len(str(m.get("content", "")))
    return int(total / chars_per_token)
