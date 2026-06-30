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


# ── Token estimation (Phase 28: BPE tokenizer) ─────────────────────────

# Module-level cache for the tiktoken encoding — loaded once on first use.
_bpe_encoding = None
_bpe_init_error: Exception | None = None


def _get_bpe_encoding():
    """Lazy-load the cl100k_base encoding. Cached after first successful load."""
    global _bpe_encoding, _bpe_init_error
    if _bpe_encoding is not None:
        return _bpe_encoding
    if _bpe_init_error is not None:
        raise _bpe_init_error
    try:
        import tiktoken
        _bpe_encoding = tiktoken.get_encoding("cl100k_base")
        return _bpe_encoding
    except Exception as e:
        _bpe_init_error = e
        raise


def estimate_tokens(messages: list[dict], chars_per_token: float = 3.5) -> int:
    """Precise BPE token counting using cl100k_base encoding.

    Falls back to character-based heuristic if tiktoken is unavailable.
    Anthropic message structure overhead is included (~4 tokens/message
    for role metadata, +3 for conversation framing).
    """
    try:
        enc = _get_bpe_encoding()
    except Exception:
        return _fallback_estimate_tokens(messages, chars_per_token)

    num_tokens = 0
    for message in messages:
        # Anthropic message structure overhead (~4 tokens per message)
        num_tokens += 4

        role = message.get("role", "")
        content = message.get("content", "")

        num_tokens += len(enc.encode(role))

        if isinstance(content, str):
            num_tokens += len(enc.encode(content))
        elif isinstance(content, list):
            # Structured content blocks (text, tool_use, tool_result, etc.)
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    num_tokens += len(enc.encode(block_type))

                    if block_type == "text":
                        num_tokens += len(enc.encode(block.get("text", "")))
                    elif block_type == "tool_use":
                        num_tokens += len(enc.encode(block.get("name", "")))
                        import json as _json
                        num_tokens += len(enc.encode(
                            _json.dumps(block.get("input", {}))
                        ))
                    elif block_type == "tool_result":
                        num_tokens += len(enc.encode(
                            block.get("tool_use_id", "")
                        ))
                        result_content = block.get("content", "")
                        if isinstance(result_content, str):
                            num_tokens += len(enc.encode(result_content))
                        else:
                            num_tokens += len(enc.encode(str(result_content)))
                    else:
                        import json as _json
                        num_tokens += len(enc.encode(_json.dumps(block)))
                else:
                    num_tokens += len(enc.encode(str(block)))
        else:
            num_tokens += len(enc.encode(str(content)))

    # Conversation framing overhead (~3 tokens)
    num_tokens += 3
    return num_tokens


def _fallback_estimate_tokens(
    messages: list[dict], chars_per_token: float = 3.5,
) -> int:
    """Character-based fallback when tiktoken is unavailable."""
    import json as _json
    total = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            total += len(_json.dumps(content))
        else:
            total += len(str(content))
    return int(total / chars_per_token)
