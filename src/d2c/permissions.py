"""Deny-first permission evaluation with 4 modes and authorization pipeline.

Paper Section 5: Permission system with deny-first rules, graduated trust spectrum,
and the authorization pipeline (PreToolUse hook → rules → handler).

Core invariant: DENY rules ALWAYS win, even under dontAsk mode.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

from d2c.tools import PermissionCategory, Tool
from d2c.tools.pool import Rule as PoolRule, RuleType as PoolRuleType

if TYPE_CHECKING:
    from d2c.config import Config


# ── Types ────────────────────────────────────────────────────────────

class PermissionMode(Enum):
    PLAN = "plan"
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    DONT_ASK = "dontAsk"


class PermissionDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class RuleType(Enum):
    DENY = "deny"
    ALLOW = "allow"


@dataclass
class PermissionRule:
    """Paper: permission rules match by tool name patterns."""
    rule_type: RuleType
    pattern: str
    reason: str = ""

    def matches(self, tool_name: str, tool_input: dict | None = None) -> bool:
        """Match tool name against pattern. Supports wildcard *."""
        pt = self.pattern
        if pt.endswith(":*"):
            prefix = pt[:-2]
            return tool_name == prefix or tool_name.startswith(prefix + "__")
        if pt.endswith("*"):
            return tool_name.startswith(pt[:-1])
        return tool_name == pt


@dataclass
class PermissionRequest:
    tool_name: str
    tool_input: dict
    tool_category: PermissionCategory
    session_id: str = ""


@dataclass
class PermissionResult:
    decision: PermissionDecision
    reason: str = ""
    modified_input: dict | None = None


# ── Permission engine ────────────────────────────────────────────────

class PermissionEngine:
    """Deny-first rule evaluation (paper Section 5.1).

    Core invariant: DENY rules ALWAYS win, even under dontAsk mode.
    """

    def __init__(
        self,
        mode: PermissionMode,
        rules: list[PermissionRule] | None = None,
    ):
        self.mode = mode
        self.rules = rules or []

    def evaluate(self, request: PermissionRequest) -> PermissionResult:
        # Step 1: Deny rules first (ALWAYS)
        for rule in self.rules:
            if rule.rule_type == RuleType.DENY and rule.matches(request.tool_name, request.tool_input):
                return PermissionResult(
                    PermissionDecision.DENY,
                    reason=rule.reason or f"Denied by rule: {rule.pattern}",
                )

        # Step 2: Allow rules
        for rule in self.rules:
            if rule.rule_type == RuleType.ALLOW and rule.matches(request.tool_name, request.tool_input):
                return PermissionResult(
                    PermissionDecision.ALLOW,
                    reason=rule.reason or f"Allowed by rule: {rule.pattern}",
                )

        # Step 3: Mode-based defaults
        return self._mode_default(request)

    def _mode_default(self, request: PermissionRequest) -> PermissionResult:
        if self.mode == PermissionMode.DONT_ASK:
            return PermissionResult(PermissionDecision.ALLOW)

        if self.mode == PermissionMode.PLAN:
            return PermissionResult(
                PermissionDecision.ASK,
                reason="Plan mode: awaiting plan approval",
            )

        if self.mode == PermissionMode.ACCEPT_EDITS:
            # Auto-approve: reads + writes + safe shell commands
            if request.tool_category in (PermissionCategory.READ, PermissionCategory.WRITE):
                return PermissionResult(PermissionDecision.ALLOW)
            if request.tool_category == PermissionCategory.SHELL:
                return self._check_safe_shell(request)
            if request.tool_category == PermissionCategory.META:
                return PermissionResult(PermissionDecision.ASK)
            return PermissionResult(PermissionDecision.ALLOW)

        # DEFAULT: ask for everything not explicitly allowed
        return PermissionResult(PermissionDecision.ASK)

    def _check_safe_shell(self, request: PermissionRequest) -> PermissionResult:
        """Paper Section 5: acceptEdits auto-approves safe shell commands."""
        SAFE_COMMANDS = {"mkdir", "rmdir", "touch", "rm", "mv", "cp", "sed", "ls", "cat", "echo", "pwd", "find", "grep", "head", "tail", "wc", "sort", "uniq"}
        cmd = request.tool_input.get("command", "").strip()
        if not cmd:
            return PermissionResult(PermissionDecision.ASK)

        first_word = cmd.split()[0] if cmd else ""
        if first_word in SAFE_COMMANDS:
            return PermissionResult(PermissionDecision.ALLOW)
        return PermissionResult(PermissionDecision.ASK)

    @classmethod
    def from_config(cls, config: "Config") -> "PermissionEngine":
        mode = PermissionMode(config.permission_mode)
        rules = []
        for r in config.permission_rules:
            if isinstance(r, PermissionRule):
                rules.append(r)
            elif isinstance(r, PoolRule):
                rule_type = RuleType.DENY if r.rule_type == PoolRuleType.DENY else RuleType.ALLOW
                rules.append(PermissionRule(rule_type=rule_type, pattern=r.pattern, reason=r.reason))
            elif isinstance(r, dict):
                rules.append(PermissionRule(
                    rule_type=RuleType(r.get("type", r.get("rule_type", "deny"))),
                    pattern=r.get("pattern", ""),
                    reason=r.get("reason", ""),
                ))
        return cls(mode=mode, rules=rules)


# ── Authorization pipeline ───────────────────────────────────────────

async def authorize(
    request: PermissionRequest,
    engine: PermissionEngine,
    interactive_callback: Callable | None = None,
) -> PermissionResult:
    """Full authorization pipeline (paper Section 5.2).

    PreToolUse hook → deny-first rules → permission handler.
    PreToolUse hooks are handled in Phase 7.
    """

    # Stage 1: Deny-first rule evaluation
    result = engine.evaluate(request)

    # Stage 2: If ASK and interactive, prompt user
    if result.decision == PermissionDecision.ASK and interactive_callback:
        user_decision = await interactive_callback(request)
        return user_decision

    return result


# ── Interactive permission handler ───────────────────────────────────

async def interactivePermissionCallback(request: PermissionRequest) -> PermissionResult:
    """Paper Section 5.2: standard user approval dialog."""
    print(f"\n  === Permission Required ===")
    print(f"  Tool: {request.tool_name}")
    print(f"  Category: {request.tool_category.value}")
    print(f"  Input: {_format_input(request.tool_input)}")

    while True:
        choice = input("  Allow? [y]es / [n]o / [a]lways: ").strip().lower()
        if choice in ("y", "yes", ""):
            return PermissionResult(PermissionDecision.ALLOW)
        elif choice in ("n", "no"):
            return PermissionResult(PermissionDecision.DENY, reason="Denied by user")
        elif choice in ("a", "always"):
            return PermissionResult(PermissionDecision.ALLOW, reason="Always allowed by user")


def _format_input(tool_input: dict) -> str:
    """Format tool input for display, truncating long values."""
    parts = []
    for k, v in tool_input.items():
        s = json.dumps(v) if not isinstance(v, str) else v
        if len(s) > 100:
            s = s[:100] + "..."
        parts.append(f"    {k}: {s}")
    return "\n".join(parts)
