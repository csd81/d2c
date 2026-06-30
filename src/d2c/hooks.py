"""Hook registry & lifecycle events. Paper Section 6.1.

8 core hook events implemented (paper defines 27):
  SessionStart, UserPromptSubmit, PreToolUse, PostToolUse,
  PostToolUseFailure, PermissionDenied, Stop, PreCompact, SubagentStop

Hook types: command (shell), prompt (LLM), callback (SDK/internal).
Hooks consume zero context — they run externally or as lightweight callbacks.
"""

from __future__ import annotations

import asyncio
import json
import shlex
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from d2c.config import Config


# ── Event types ───────────────────────────────────────────────────────

class HookEvent(Enum):
    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    PERMISSION_DENIED = "PermissionDenied"
    STOP = "Stop"
    PRE_COMPACT = "PreCompact"
    SUBAGENT_STOP = "SubagentStop"


class HookType(Enum):
    COMMAND = "command"    # shell command, stdin JSON → stdout JSON
    PROMPT = "prompt"      # LLM-based hook
    CALLBACK = "callback"  # SDK/internal only (not persistable)


# ── Hook definition & result ──────────────────────────────────────────

@dataclass
class HookDefinition:
    """A configured hook from settings or plugins."""
    event: HookEvent
    hook_type: HookType
    command: str | None = None         # for COMMAND type
    prompt: str | None = None          # for PROMPT type
    callback: Callable | None = None   # for CALLBACK type
    source: str = "settings"           # "settings" | "plugin" | "managed" | "skill"
    timeout_ms: int = 30_000


@dataclass
class HookResult:
    """Merged result from firing all hooks for an event."""
    decision: str | None = None          # "allow" | "deny" | "ask"
    updated_input: dict | None = None    # PreToolUse: modified tool input
    updated_output: str | None = None    # PostToolUse: modified tool output
    additional_context: str | None = None  # injected into conversation
    veto: bool = False                   # Stop/SubagentStop: prevent stopping
    error: str | None = None


# ── Hook registry ─────────────────────────────────────────────────────

class HookRegistry:
    """Paper Section 6.1: hooks from settings.json, plugins, and managed policy.

    Zero context cost: hooks run as external processes or callbacks,
    not as part of the model's context window.
    """

    def __init__(self):
        self._hooks: dict[HookEvent, list[HookDefinition]] = {
            event: [] for event in HookEvent
        }

    def register(self, definition: HookDefinition) -> None:
        """Register a hook for an event."""
        self._hooks[definition.event].append(definition)

    def unregister(self, definition: HookDefinition) -> None:
        """Remove a hook."""
        if definition in self._hooks[definition.event]:
            self._hooks[definition.event].remove(definition)

    @classmethod
    def from_config(cls, config: "Config") -> "HookRegistry":
        """Load hooks from configuration."""
        registry = cls()
        for hook_cfg in getattr(config, 'hooks', []):
            if isinstance(hook_cfg, dict):
                definition = HookDefinition(
                    event=HookEvent(hook_cfg["event"]),
                    hook_type=HookType(hook_cfg.get("type", "callback")),
                    command=hook_cfg.get("command"),
                    prompt=hook_cfg.get("prompt"),
                    source=hook_cfg.get("source", "settings"),
                    timeout_ms=hook_cfg.get("timeout", 30_000),
                )
                registry.register(definition)
        return registry

    async def fire(self, event: HookEvent, context: dict | None = None) -> HookResult:
        """Fire all hooks for an event. Results are merged.

        Merge rules (paper Section 6.1):
        - If any hook denies → overall deny
        - If any hook vetoes → veto=True
        - Updated input/output from first hook that provides one wins
        - Additional context from all hooks is concatenated
        """
        merged = HookResult()
        for hook in self._hooks[event]:
            try:
                result = await self._execute_hook(hook, context or {})
                merged = self._merge_results(merged, result)
            except Exception as e:
                # Hook errors are non-fatal (paper: hooks fail gracefully)
                merged.error = str(e)
        return merged

    async def _execute_hook(
        self, hook: HookDefinition, context: dict,
    ) -> HookResult:
        if hook.hook_type == HookType.COMMAND and hook.command:
            return await self._execute_command_hook(hook, context)
        elif hook.hook_type == HookType.PROMPT and hook.prompt:
            return await self._execute_prompt_hook(hook, context)
        elif hook.hook_type == HookType.CALLBACK and hook.callback:
            return await hook.callback(context)
        return HookResult()

    async def _execute_command_hook(
        self, hook: HookDefinition, context: dict,
    ) -> HookResult:
        """Paper: shell command hooks receive JSON on stdin, return JSON on stdout."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *shlex.split(hook.command),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdin_data = json.dumps(context).encode()
            timeout = hook.timeout_ms / 1000

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(stdin_data),
                timeout=timeout,
            )

            if proc.returncode != 0:
                return HookResult(error=f"Hook failed (exit {proc.returncode}): {stderr.decode()}")

            try:
                data = json.loads(stdout)
                return HookResult(**{k: v for k, v in data.items()
                    if k in HookResult.__dataclass_fields__})
            except json.JSONDecodeError:
                return HookResult(additional_context=stdout.decode())

        except asyncio.TimeoutError:
            return HookResult(error="Hook timed out")
        except FileNotFoundError:
            return HookResult(error=f"Hook command not found: {hook.command}")

    async def _execute_prompt_hook(
        self, hook: HookDefinition, context: dict,
    ) -> HookResult:
        """Paper: LLM prompt hooks evaluate context and return structured result."""
        # Prompt hooks are called via the model; here we pass through
        # the prompt as additional_context for the model to process
        return HookResult(
            additional_context=f"[Hook: {hook.prompt}]\nContext: {json.dumps(context)[:1000]}",
        )

    def _merge_results(self, a: HookResult, b: HookResult) -> HookResult:
        """Merge: deny wins, first updated_input wins, contexts concatenate."""
        decision = a.decision
        if b.decision == "deny":
            decision = "deny"
        elif b.decision == "allow" and a.decision != "deny":
            decision = "allow"

        return HookResult(
            decision=decision,
            updated_input=b.updated_input or a.updated_input,
            updated_output=b.updated_output or a.updated_output,
            additional_context=_concat(a.additional_context, b.additional_context),
            veto=a.veto or b.veto,
            error=b.error or a.error,
        )


# ── Helpers ───────────────────────────────────────────────────────────

def _concat(a: str | None, b: str | None) -> str | None:
    if a and b:
        return f"{a}\n{b}"
    return a or b
