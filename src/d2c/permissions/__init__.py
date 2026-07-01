"""Deny-first permission evaluation with 4 modes and authorization pipeline.

Paper Section 5: Permission system with deny-first rules, graduated trust spectrum,
and the authorization pipeline (PreToolUse hook → rules → handler).

Core invariant: DENY rules ALWAYS win, even under dontAsk mode.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from d2c.tools import PermissionCategory
from d2c.tools.pool import Rule as PoolRule
from d2c.tools.pool import RuleType as PoolRuleType

if TYPE_CHECKING:
    from d2c.config import Config
    from d2c.path_rules import PathScopedRules


# ── Types ────────────────────────────────────────────────────────────


class PermissionMode(Enum):
    PLAN = "plan"
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    DONT_ASK = "dontAsk"
    AUTO = "auto"  # ML classifier: 2-stage fast-filter + CoT
    BYPASS = "bypass"  # Trust operator: skip prompts, safety-critical checks remain


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
    Supports 6 modes: plan, default, acceptEdits, dontAsk, auto, bypass.
    """

    def __init__(
        self,
        mode: PermissionMode,
        rules: list[PermissionRule] | None = None,
        classifier: Any | None = None,  # AutoClassifier for AUTO mode
    ):
        self.mode = mode
        self.rules = rules or []
        self._classifier = classifier
        self._path_rules: "PathScopedRules | None" = None  # Phase 21

    def set_path_rules(self, path_rules: "PathScopedRules | None") -> None:
        """Phase 21: Attach path-scoped rules for dynamic rule lookup.

        When set, evaluate() and evaluate_async() consult path-scoped rules
        applicable to the file in the request (via _path_scoped_decision),
        loading each directory's .d2c/rules/*.md lazily on first access.
        """
        self._path_rules = path_rules

    def _path_scoped_decision(self, request: "PermissionRequest") -> "PermissionResult | None":
        """Phase 34: consult path-scoped rules for the file in this request.

        Returns a DENY/ALLOW result if a path rule matches, else None (so the
        caller falls through to mode defaults). Deny wins over allow. The
        directory is loaded lazily on first access, so newly-entered
        directories contribute their rules mid-conversation.
        """
        if not self._path_rules:
            return None
        p = (
            request.tool_input.get("file_path")
            or request.tool_input.get("path")
            or request.tool_input.get("notebook_path")
        )
        if not p:
            return None
        from pathlib import Path

        fp = Path(str(p))
        try:
            self._path_rules.on_directory_accessed(fp.parent)
            dyn_rules = self._path_rules.get_rules_for_path(fp)
        except Exception:
            return None
        for rule in dyn_rules:
            if rule.rule_type == RuleType.DENY and rule.matches(
                request.tool_name, request.tool_input
            ):
                return PermissionResult(
                    PermissionDecision.DENY,
                    reason=rule.reason or f"Denied by path rule: {rule.pattern}",
                )
        for rule in dyn_rules:
            if rule.rule_type == RuleType.ALLOW and rule.matches(
                request.tool_name, request.tool_input
            ):
                return PermissionResult(
                    PermissionDecision.ALLOW,
                    reason=rule.reason or f"Allowed by path rule: {rule.pattern}",
                )
        return None

    def add_rules(self, new_rules: list[PermissionRule]) -> None:
        """Phase 21: Dynamically add rules mid-conversation.

        Used by path-scoped rule loading when new directories are accessed.
        """
        self.rules.extend(new_rules)

    def evaluate(self, request: PermissionRequest) -> PermissionResult:
        # Step 1: Deny rules first (ALWAYS)
        for rule in self.rules:
            if rule.rule_type == RuleType.DENY and rule.matches(
                request.tool_name, request.tool_input
            ):
                return PermissionResult(
                    PermissionDecision.DENY,
                    reason=rule.reason or f"Denied by rule: {rule.pattern}",
                )

        # Step 2: Allow rules
        for rule in self.rules:
            if rule.rule_type == RuleType.ALLOW and rule.matches(
                request.tool_name, request.tool_input
            ):
                return PermissionResult(
                    PermissionDecision.ALLOW,
                    reason=rule.reason or f"Allowed by rule: {rule.pattern}",
                )

        # Step 2b: Path-scoped rules (Phase 21/34) — lazily loaded per directory
        path_result = self._path_scoped_decision(request)
        if path_result is not None:
            return path_result

        # Step 3: Mode-based defaults
        return self._mode_default(request)

    async def evaluate_async(self, request: PermissionRequest) -> PermissionResult:
        """Async evaluation with classifier support for AUTO mode."""
        # Step 1: Deny rules first (ALWAYS)
        for rule in self.rules:
            if rule.rule_type == RuleType.DENY and rule.matches(
                request.tool_name, request.tool_input
            ):
                return PermissionResult(
                    PermissionDecision.DENY,
                    reason=rule.reason or f"Denied by rule: {rule.pattern}",
                )

        # Step 2: Allow rules
        for rule in self.rules:
            if rule.rule_type == RuleType.ALLOW and rule.matches(
                request.tool_name, request.tool_input
            ):
                return PermissionResult(
                    PermissionDecision.ALLOW,
                    reason=rule.reason or f"Allowed by rule: {rule.pattern}",
                )

        # Step 2b: Path-scoped rules (Phase 21/34) — lazily loaded per directory
        path_result = self._path_scoped_decision(request)
        if path_result is not None:
            return path_result

        # Step 3: Mode-based defaults (async for AUTO)
        if self.mode == PermissionMode.AUTO and self._classifier:
            try:
                return await self._classifier.evaluate(request)
            except Exception:
                # Classifier failure → fall back to DEFAULT (ask)
                return PermissionResult(PermissionDecision.ASK, reason="classifier unavailable")
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

        if self.mode == PermissionMode.BYPASS:
            # Trust operator: auto-approve, but safety-critical checks remain
            if self._is_safety_critical(request):
                return PermissionResult(
                    PermissionDecision.ASK,
                    reason="Safety-critical operation despite bypass mode",
                )
            return PermissionResult(PermissionDecision.ALLOW)

        if self.mode == PermissionMode.AUTO:
            # AUTO without classifier → fall back to DEFAULT
            if self._classifier is None:
                return PermissionResult(PermissionDecision.ASK, reason="auto mode: no classifier")

        # DEFAULT: ask for everything not explicitly allowed
        return PermissionResult(PermissionDecision.ASK)

    def _check_safe_shell(self, request: PermissionRequest) -> PermissionResult:
        """acceptEdits shell policy (Phase 38).

        Structural, not first-word-only: a command is auto-approved only if
        every statement is read-only / create-only / a test-lint-format tool.
        Clearly-destructive commands (rm, mv, sed -i, find -delete,
        pipe-to-shell, interpreter -c, chmod, sudo, ...) are DENIED — since the
        executor only blocks on DENY, ASK would let them through — and
        everything uncertain requires explicit approval.
        """
        from d2c.permissions.classifier import classify_accept_edits_shell

        verdict = classify_accept_edits_shell(request.tool_input.get("command", ""))
        if verdict == "allow":
            return PermissionResult(PermissionDecision.ALLOW)
        if verdict == "deny":
            return PermissionResult(
                PermissionDecision.DENY,
                reason="acceptEdits: destructive shell command requires explicit approval",
            )
        return PermissionResult(PermissionDecision.ASK)

    def _is_safety_critical(self, request: PermissionRequest) -> bool:
        """Check if an operation is safety-critical even under BYPASS mode.

        Paper: "BypassPermissions: Skips most prompts but safety-critical
        checks remain."

        Safety-critical operations:
        - Destructive bash commands (rm -rf, format, dd, chmod 777, etc.)
        - Agent/subagent invocations
        - Operations outside the project directory
        """
        if request.tool_name == "Bash":
            cmd = request.tool_input.get("command", "").strip().lower()
            # Destructive patterns
            destructive = [
                "rm -rf /",
                "rm -rf ~",
                "rm -rf .",
                "dd if=",
                "mkfs.",
                ":(){ :|:& };:",  # fork bomb
                "chmod 777 /",
                "chmod -R 777 /",
                "> /dev/sda",
                "format c:",
            ]
            for pattern in destructive:
                if pattern in cmd:
                    return True
            # Pattern-based checks
            if cmd.startswith("rm -rf ") and not cmd.startswith("rm -rf ./"):
                if "/" in cmd.split("rm -rf ")[-1].lstrip():
                    return True

        if request.tool_name in ("Agent", "Task", "Skill"):
            # Agent operations could spawn subprocesses — safety check
            return True

        if request.tool_category == PermissionCategory.WRITE:
            # Write outside project directory is safety-critical
            file_path = request.tool_input.get("file_path", "")
            if file_path and ".." in file_path:
                return True

        return False

    @classmethod
    def from_config(cls, config: "Config") -> "PermissionEngine":
        mode = PermissionMode(config.permission_mode)
        rules = []
        for r in config.permission_rules:
            if isinstance(r, PermissionRule):
                rules.append(r)
            elif isinstance(r, PoolRule):
                rule_type = RuleType.DENY if r.rule_type == PoolRuleType.DENY else RuleType.ALLOW
                rules.append(
                    PermissionRule(rule_type=rule_type, pattern=r.pattern, reason=r.reason)
                )
            elif isinstance(r, dict):
                rules.append(
                    PermissionRule(
                        rule_type=RuleType(r.get("type", r.get("rule_type", "deny"))),
                        pattern=r.get("pattern", ""),
                        reason=r.get("reason", ""),
                    )
                )
        engine = cls(mode=mode, rules=rules)
        # Phase 34: attach path-scoped rules so .d2c/rules/*.md are enforced.
        try:
            from d2c.path_rules import PathScopedRules

            engine.set_path_rules(PathScopedRules())
        except Exception:
            pass
        return engine


# ── Phase 43: interactive ASK resolution ──────────────────────────────

# An approval callback decides an ASK: True → run the tool, False → deny.
# Raising → deny (fail safe).
ApprovalCallback = Callable[["PermissionRequest", "PermissionResult"], Awaitable[bool]]

# Sentinel reason used when an ASK can't be resolved (no approval channel).
PERMISSION_REQUIRED_REASON = (
    "Permission required: interactive approval is not available in this mode."
)


async def resolve_permission_decision(
    request: "PermissionRequest",
    result: "PermissionResult | None",
    approval_callback: "ApprovalCallback | None",
) -> "PermissionResult | None":
    """Collapse an evaluated PermissionResult to ALLOW/DENY.

    - result is None (no engine wired) → return None; the caller executes.
    - ALLOW / DENY → returned unchanged.
    - ASK + callback approves → ALLOW; rejects → DENY.
    - ASK + no callback → DENY (permission-required, fail safe).
    - ASK + callback raises → DENY (fail safe).

    ASK therefore never falls through to automatic execution.
    """
    if result is None:
        return None
    if result.decision in (PermissionDecision.ALLOW, PermissionDecision.DENY):
        return result
    # ASK
    if approval_callback is None:
        return PermissionResult(PermissionDecision.DENY, reason=PERMISSION_REQUIRED_REASON)
    try:
        approved = await approval_callback(request, result)
    except Exception as e:
        return PermissionResult(
            PermissionDecision.DENY,
            reason=f"Permission approval error ({type(e).__name__}); denied for safety.",
        )
    if approved:
        return PermissionResult(PermissionDecision.ALLOW, reason="approved by user")
    return PermissionResult(PermissionDecision.DENY, reason="denied by user")


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
    print("\n  === Permission Required ===")
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
