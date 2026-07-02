"""Auto-mode safety classifier — 2-stage fast-filter + chain-of-thought.

Paper Section 5: "Auto mode uses a two-stage fast-filter and chain-of-thought
evaluation of tool safety to balance autonomy with safety."

Stage 1 (fast-filter): Heuristic rules for clearly safe or unsafe operations.
  - Always safe: Read/Glob/Grep on text files
  - Always unsafe: Destructive shell commands (rm -rf /, format, etc.)

Stage 2 (CoT): For ambiguous cases, calls a fast model with structured
  safety evaluation prompt. The model classifies as safe/unsafe/review.

Phase 27: ShellCommandAnalyzer — robust shell parsing with AST-style safety
analysis replacing naive string checks. Resolves wrappers, detects command
chaining bypasses, and applies deep inspection rules per-command.
"""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from d2c.tools import PermissionCategory

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Fast-filter: known-safe tool + input patterns
SAFE_READ_TOOLS = frozenset(
    {"Read", "Glob", "Grep", "FileRead", "WebFetch", "WebSearch", "TaskList"}
)
SAFE_EDIT_TOOLS = frozenset({"Edit", "FileEdit", "Write", "FileWrite"})

# Destructive shell command patterns (always deny, even in auto mode)
DESTRUCTIVE_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "rm -rf .",
    "dd if=",
    "mkfs.",
    ":(){ :|:& };:",  # fork bomb
    "chmod 777 /",
    "chmod -R 777 /",
    "> /dev/",
    "format c:",
    "shutdown",
    "reboot",
    "del /f /s",
    "rd /s /q",
]

# Known-safe shell commands (read-only or non-destructive)
SAFE_SHELL_COMMANDS = frozenset(
    {
        "ls",
        "dir",
        "cat",
        "type",
        "echo",
        "pwd",
        "cd",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "find",
        "grep",
        "git",
        "python",
        "node",
        "npm",
        "npx",
        "cargo",
        "go",
        "which",
        "where",
        "whoami",
        "hostname",
        "date",
        "time",
        "mkdir",
        "touch",
        "cp",
        "mv",
    }
)

# Phase 27: Wrapper commands that modify execution context but are not the
# actual payload. Stripped recursively to reveal the underlying command.
_WRAPPER_COMMANDS = frozenset({"env", "sudo", "nohup", "time", "exec", "eval"})

# Phase 27: Shell interpreters — piping into these is always dangerous.
_SHELL_INTERPRETERS = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "dash",
        "fish",
        "python",
        "python3",
        "perl",
        "ruby",
        "node",
        "powershell",
        "pwsh",
        "cmd",
    }
)

# Phase 27: Destructive file operation commands.
_DESTRUCTIVE_FILE_CMDS = frozenset({"rm", "trash", "del", "rmdir", "rd"})

# Phase 27: Permission-changing commands.
_PERMISSION_CMDS = frozenset({"chmod", "chown", "chgrp", "icacls", "cacls"})

# Phase 27: Network download commands — SSRF risk.
_NETWORK_CMDS = frozenset({"curl", "wget", "fetch", "Invoke-WebRequest"})

# Phase 27: Recursive flags that make file operations dangerous at scale.
_RECURSIVE_FLAGS = frozenset({"-r", "-R", "-rf", "-rF", "-fr", "-fR", "/s", "/S", "-Recurse"})

# Phase 27: SSRF / local-network targets.
_SSRF_PATTERNS = re.compile(
    r"(localhost|127\.0\.0\.\d+|169\.254\.\d+\.\d+|\[::1\]|0\.0\.0\.0|"
    r"10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|"
    r"192\.168\.\d+\.\d+)",
    re.IGNORECASE,
)

# Phase 27: Statement delimiters used by split_logical_statements.
_STATEMENT_DELIMITERS = re.compile(r"(;|&&|\|\||\|&|&\||\n)")

# Phase 27: Variable reference pattern for detecting unresolvable targets.
_VARIABLE_PATTERN = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")


# ── Phase 27: Shell Command Parser ──────────────────────────────────────


@dataclass
class ParsedStatement:
    """A single parsed shell statement with resolved command and context."""

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    redirects: list[str] = field(default_factory=list)


def _split_logical_statements(cmd_str: str) -> list[str]:
    """Split a shell command string by logical statement operators.

    Handles: ``;``, ``&&``, ``||``, ``|`` (pipe), ``&`` (background),
    and newlines. Each resulting segment is a logical statement that
    should be analyzed independently.
    """
    # Use regex split to capture delimiters, then rebuild statements
    parts = _STATEMENT_DELIMITERS.split(cmd_str)
    statements: list[str] = []
    current: list[str] = []

    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part in (";", "&&", "||"):
            if current:
                statements.append(" ".join(current))
                current = []
            # The delimiter itself is discarded; next part starts a new statement
        elif part == "|":
            # Pipe: keep as part of the same statement for analysis
            # (pipes chain within one logical line; we detect | bash as a hazard)
            current.append(part)
        elif part in ("|&", "&|"):
            current.append(part)
        elif part == "&":
            # Background: end current statement, start new one
            if current:
                statements.append(" ".join(current))
                current = []
        elif part.strip().startswith("\n") or part == "\n":
            if current:
                statements.append(" ".join(current))
                current = []
        else:
            current.append(part)

    if current:
        statements.append(" ".join(current))

    return statements


def _extract_command_name(raw: str) -> str:
    """Extract the command name from a raw part, handling paths and .exe."""
    name = raw.replace("\\", "/").split("/")[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower()


def parse_shell_command(cmd_str: str) -> list[ParsedStatement]:
    """Parse a shell command string into a list of resolved statements.

    Resolution steps:
    1. Split by logical separators (``;``, ``&&``, ``||``, pipe, ``&``)
    2. Tokenize each statement with ``shlex``
    3. Extract environment variables (``KEY=value``) and redirects
    4. Strip wrapper commands (``env``, ``sudo``, ``nohup``, etc.) recursively
    5. Return ``ParsedStatement`` for each resolved command
    """
    raw_statements = _split_logical_statements(cmd_str)
    parsed: list[ParsedStatement] = []

    for stmt in raw_statements:
        stmt = stmt.strip()
        if not stmt:
            continue

        try:
            parts = shlex.split(stmt)
        except ValueError:
            # Fallback split if quotes are unbalanced
            parts = stmt.split()

        if not parts:
            continue

        env: dict[str, str] = {}
        redirects: list[str] = []
        clean_parts: list[str] = []

        for p in parts:
            if "=" in p and not p.startswith("-") and not p.startswith("--"):
                # Check if this looks like a variable assignment (not a flag like --key=val)
                eq_pos = p.index("=")
                k = p[:eq_pos]
                # Variable names: start with letter/underscore, contain alphanumeric/underscore
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                    env[k] = p[eq_pos + 1 :]
                    continue
            if p.startswith(">") or p.startswith("<"):
                redirects.append(p)
            else:
                clean_parts.append(p)

        # Strip wrapper commands recursively
        while clean_parts:
            cmd_name = _extract_command_name(clean_parts[0])
            if cmd_name in _WRAPPER_COMMANDS:
                clean_parts = clean_parts[1:]
                # Re-extract env vars from wrapper args (e.g., env VAR=1)
                remaining = list(clean_parts)
                clean_parts = []
                for p in remaining:
                    if "=" in p and not p.startswith("-"):
                        eq_pos = p.index("=")
                        k = p[:eq_pos]
                        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                            env[k] = p[eq_pos + 1 :]
                            continue
                    clean_parts.append(p)
            else:
                break

        if clean_parts:
            parsed.append(
                ParsedStatement(
                    command=clean_parts[0],
                    args=clean_parts[1:],
                    env=env,
                    redirects=redirects,
                )
            )

    return parsed


def _has_recursive_flag(args: list[str]) -> bool:
    """Check if args contain a recursive/destructive flag."""
    for a in args:
        if a.lower() in _RECURSIVE_FLAGS:
            return True
        # Handle combined flags like -rf, -fr
        if a.startswith("-") and not a.startswith("--"):
            flags = a[1:].lower()
            if "r" in flags or "R" in flags:
                return True
    return False


def _has_variable_targets(args: list[str], redirects: list[str]) -> bool:
    """Check if any argument or redirect target contains a shell variable."""
    for a in args + redirects:
        if _VARIABLE_PATTERN.search(a):
            return True
    return False


def _is_system_path_static(path: str) -> bool:
    """Check if a path is a system-critical location (static check)."""
    if not path:
        return False
    system_prefixes = [
        "/etc",
        "/sys",
        "/proc",
        "/dev",
        "/boot",
        "/root",
        "/var/log",
        "C:\\Windows",
        "C:\\Program Files",
        "~/.ssh",
        "~/.gnupg",
        ".git/config",
        ".env",
    ]
    normalized = path.replace("\\", "/").lower()
    for prefix in system_prefixes:
        if normalized.startswith(prefix.replace("\\", "/").lower()):
            return True
    # Also check for root paths
    if normalized in ("/", "~", "c:", "c:\\", "d:", "d:\\"):
        return True
    return False


def _analyze_shell_command(cmd_str: str) -> Any | None:
    """Deep safety analysis of a shell command string.

    Returns:
        PermissionResult(ALLOW) if clearly safe,
        PermissionResult(DENY) if clearly dangerous,
        None if ambiguous (should fall through to CoT).
    """
    from d2c.permissions import PermissionDecision, PermissionResult

    # Check for pipe-to-shell pattern in the raw command (before splitting)
    # Pattern: anything | bash/sh/python/...
    pipe_parts = cmd_str.split("|")
    if len(pipe_parts) > 1:
        # Check the last consumer in the pipe chain
        for part in pipe_parts[1:]:
            first_word = part.strip().split()[0] if part.strip() else ""
            if _extract_command_name(first_word) in _SHELL_INTERPRETERS:
                return PermissionResult(
                    PermissionDecision.DENY,
                    reason="auto (deep): pipe to shell interpreter blocked",
                )

    try:
        statements = parse_shell_command(cmd_str)
    except Exception:
        # Parse error → can't determine safety, fall through to CoT
        return None

    if not statements:
        return None

    has_ambiguous = False

    for stmt in statements:
        cmd_name = _extract_command_name(stmt.command)
        is_handled = False  # True if a specific check resolved this command

        # Check for variable targets (unresolvable → ASK)
        if _has_variable_targets(stmt.args, stmt.redirects):
            has_ambiguous = True
            continue

        # --- Destructive file operations ---
        if cmd_name in _DESTRUCTIVE_FILE_CMDS:
            if _has_recursive_flag(stmt.args):
                # Check target paths for system locations
                for arg in stmt.args:
                    if arg.startswith("-"):
                        continue
                    if _is_system_path_static(arg):
                        return PermissionResult(
                            PermissionDecision.DENY,
                            reason=f"auto (deep): destructive {cmd_name} on system path '{arg}'",
                        )
                # Recursive but on non-system path → ambiguous
                has_ambiguous = True
                continue
            # Non-recursive rm/del → safe
            is_handled = True

        # --- Permission-changing commands ---
        if cmd_name in _PERMISSION_CMDS:
            if _has_recursive_flag(stmt.args):
                for arg in stmt.args:
                    if arg.startswith("-"):
                        continue
                    if _is_system_path_static(arg):
                        return PermissionResult(
                            PermissionDecision.DENY,
                            reason=f"auto (deep): recursive {cmd_name} on system path",
                        )
                has_ambiguous = True
                continue
            is_handled = True

        # --- Network commands: SSRF check ---
        if cmd_name in _NETWORK_CMDS:
            ssrf_found = False
            for arg in stmt.args:
                if arg.startswith("-"):
                    continue
                if _SSRF_PATTERNS.search(arg):
                    has_ambiguous = True
                    ssrf_found = True
                    break
            if not ssrf_found:
                is_handled = True  # External URL → safe

        # --- Redirect to sensitive paths ---
        for redir in stmt.redirects:
            target = redir.lstrip("<>")
            if target.startswith("&"):
                continue  # fd redirect like 2>&1
            target = target.strip()
            if _is_system_path_static(target):
                has_ambiguous = True  # Suspicious, needs CoT review

        # --- Nested shell: bash -c "...", sh -c "...", etc. ---
        if cmd_name in ("bash", "sh", "zsh", "powershell", "pwsh", "cmd"):
            inner_safe = False
            inspect_next = False
            for arg in stmt.args:
                if inspect_next:
                    inner_result = _analyze_shell_command(arg)
                    if inner_result is not None:
                        from d2c.permissions import PermissionDecision

                        if inner_result.decision == PermissionDecision.DENY:
                            return PermissionResult(
                                PermissionDecision.DENY,
                                reason="auto (deep): nested shell has destructive command",
                            )
                        elif inner_result.decision == PermissionDecision.ALLOW:
                            inner_safe = True
                        elif inner_result.decision == PermissionDecision.ASK:
                            has_ambiguous = True
                    inspect_next = False
                if arg in ("-c", "-Command", "/C", "/c"):
                    inspect_next = True
            if inner_safe:
                is_handled = True

        # --- Known-safe commands ---
        if is_handled:
            continue
        if cmd_name in SAFE_SHELL_COMMANDS:
            continue  # This statement is safe

        # --- Unknown command → ambiguous ---
        has_ambiguous = True

    if has_ambiguous:
        return None  # Fall through to CoT

    # All statements passed safety checks
    from d2c.permissions import PermissionDecision, PermissionResult

    return PermissionResult(
        PermissionDecision.ALLOW,
        reason="auto (deep): all statements pass safety analysis",
    )


# ── Phase 38: strict acceptEdits shell policy ─────────────────────────
#
# Unlike the AUTO classifier above (which is permissive and backed by a CoT
# model), acceptEdits must decide structurally with NO model call. It uses a
# tight allowlist: only read-only / create-only / test-lint-format commands
# are auto-approved. Anything that deletes, moves, changes permissions, or runs
# arbitrary code is denied; everything else asks. First-word matching is never
# used to allow.

_AE_READONLY = frozenset(
    {
        "ls",
        "dir",
        "cat",
        "type",
        "echo",
        "pwd",
        "cd",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "which",
        "where",
        "whoami",
        "hostname",
        "date",
        "printf",
        "true",
        "diff",
        "tree",
        "stat",
        "file",
        "du",
        "df",
        "basename",
        "dirname",
        "grep",
        "rg",
        "mkdir",
        "touch",
    }
)
_AE_DEV = frozenset(
    {
        "pytest",
        "ruff",
        "mypy",
        "black",
        "isort",
        "flake8",
        "pylint",
        "prettier",
        "eslint",
        "tsc",
    }
)
_AE_GIT_READONLY_SUB = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "branch",
        "remote",
        "fetch",
        "ls-files",
        "rev-parse",
        "describe",
        "blame",
        "tag",
        "config",
    }
)
_AE_DESTRUCTIVE = frozenset(
    {
        "rm",
        "rmdir",
        "trash",
        "del",
        "rd",
        "shred",
        "unlink",
        "mv",
        "move",
        "dd",
        "mkfs",
        "chmod",
        "chown",
        "chgrp",
        "icacls",
        "cacls",
        "sudo",
        "su",
        "kill",
        "pkill",
        "reboot",
        "shutdown",
        "systemctl",
        "service",
    }
)
_AE_INTERPRETERS = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "dash",
        "fish",
        "python",
        "python3",
        "perl",
        "ruby",
        "node",
        "powershell",
        "pwsh",
        "cmd",
    }
)
_AE_CODE_FLAGS = frozenset({"-c", "-e", "--command", "-Command", "/C", "/c"})
_AE_FIND_DESTRUCTIVE = frozenset(
    {
        "-delete",
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",
        "-fprint",
        "-fprintf",
    }
)


def _classify_ae_statement(stmt: "ParsedStatement") -> str:
    """'allow' | 'deny' | 'ask' for a single acceptEdits shell statement."""
    name = _extract_command_name(stmt.command)
    args = stmt.args

    # Output redirection can overwrite files → not auto-safe (input/fd-dup ok).
    for redir in stmt.redirects:
        r = redir.strip()
        if r.startswith("<"):
            continue
        target = r.lstrip("<>").strip()
        if target.startswith("&"):
            continue
        return "ask"

    if name in _AE_DESTRUCTIVE:
        return "deny"

    if name == "sed":
        return "deny" if any(a == "-i" or a.startswith("-i") for a in args) else "ask"

    if name == "find":
        return "deny" if any(a in _AE_FIND_DESTRUCTIVE for a in args) else "allow"

    if name in _AE_INTERPRETERS:
        # Deny inline-code flags, including combined POSIX short-flag clusters
        # like `bash -lc '...'` / `sh -ic '...'` (Phase 46).
        def _is_inline_code_flag(a: str) -> bool:
            if a in _AE_CODE_FLAGS:
                return True
            return a.startswith("-") and not a.startswith("--") and ("c" in a[1:] or "e" in a[1:])

        if any(_is_inline_code_flag(a) for a in args):
            return "deny"  # inline arbitrary code
        if name in ("python", "python3") and "-m" in args:
            return "allow"  # e.g. python -m pytest
        return "ask"

    if name == "git":
        sub = next((a for a in args if not a.startswith("-")), None)
        return "allow" if sub in _AE_GIT_READONLY_SUB else "ask"

    if name in _AE_READONLY or name in _AE_DEV:
        return "allow"

    return "ask"


def classify_accept_edits_shell(command: str) -> str:
    """Structural safety verdict for a shell command under acceptEdits.

    Returns 'allow' (auto-approve), 'deny' (block outright), or 'ask'
    (require explicit approval). Every statement must be independently safe
    for an 'allow'; a single destructive statement forces 'deny'.
    """
    from d2c.permissions import PermissionDecision

    cmd = command.strip()
    if not cmd:
        return "ask"

    # Reuse the deep analyzer only to catch clearly-dangerous DENY patterns
    # (pipe-to-shell, rm -rf on system paths, nested-shell payloads).
    deep = _analyze_shell_command(cmd)
    if deep is not None and deep.decision == PermissionDecision.DENY:
        return "deny"

    try:
        statements = parse_shell_command(cmd)
    except Exception:
        return "ask"
    if not statements:
        return "ask"

    verdict = "allow"
    for stmt in statements:
        s = _classify_ae_statement(stmt)
        if s == "deny":
            return "deny"
        if s == "ask":
            verdict = "ask"
    return verdict


class AutoClassifier:
    """Two-stage safety classifier for AUTO permission mode.

    Stage 1: Fast heuristic filter (no API call).
    Stage 2: Chain-of-thought model evaluation (API call).
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.deepseek.com/anthropic",
        model: str = "deepseek-v4-flash",  # Fast/cheap model for CoT
        timeout_ms: int = 10_000,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._timeout_ms = timeout_ms

    async def evaluate(self, request: Any) -> Any:
        """Evaluate a permission request. Returns PermissionResult."""
        from d2c.permissions import PermissionDecision, PermissionResult

        # Stage 1: Fast-filter
        fast = self._fast_filter(request)
        if fast is not None:
            return fast

        # Stage 2: Chain-of-thought classification
        try:
            return await self._cot_classify(request)
        except Exception as e:
            logger.warning("AutoClassifier CoT failed: %s. Falling back to ASK.", e)
            return PermissionResult(
                PermissionDecision.ASK,
                reason=f"auto classifier unavailable: {e}",
            )

    def _fast_filter(self, request: Any) -> Any | None:
        """Stage 1: Heuristic safety check. Returns None if ambiguous."""
        from d2c.permissions import PermissionDecision, PermissionResult

        tool_name = request.tool_name
        category = request.tool_category
        tool_input = request.tool_input

        # Always safe: read operations on text content
        if category == PermissionCategory.READ:
            return PermissionResult(
                PermissionDecision.ALLOW,
                reason=f"auto (fast-filter): {tool_name} is safe read",
            )

        # Safe edit tools on non-system paths
        if tool_name in SAFE_EDIT_TOOLS:
            file_path = tool_input.get("file_path", "")
            if not self._is_system_path(file_path):
                return PermissionResult(
                    PermissionDecision.ALLOW,
                    reason=f"auto (fast-filter): safe edit on {file_path}",
                )

        # Shell commands: destructive patterns + deep analysis (Phase 27)
        if tool_name == "Bash":
            cmd = tool_input.get("command", "").strip()

            # Quick pre-check: destructive patterns (string-match for speed)
            cmd_lower = cmd.lower()
            for pattern in DESTRUCTIVE_PATTERNS:
                if pattern in cmd_lower:
                    return PermissionResult(
                        PermissionDecision.DENY,
                        reason="auto (fast-filter): destructive command blocked",
                    )

            # Phase 27: Deep AST-style safety analysis
            result = _analyze_shell_command(cmd)
            if result is not None:
                return result

            # Unknown / ambiguous shell command → needs CoT
            return None

        # Meta operations (Skill, Agent) → needs CoT evaluation
        if category == PermissionCategory.META:
            return None

        # Write operations on system paths → ambiguous
        if category == PermissionCategory.WRITE:
            file_path = tool_input.get("file_path", "")
            if self._is_system_path(file_path):
                return None  # Needs CoT

        return None  # Ambiguous, needs CoT

    async def _cot_classify(self, request: Any) -> Any:
        """Stage 2: Chain-of-thought model evaluation.

        Calls a fast model with the tool name, input, and category,
        asking it to classify safety.
        """
        import asyncio

        from d2c.permissions import PermissionDecision, PermissionResult

        if not self._api_key:
            return PermissionResult(
                PermissionDecision.ASK,
                reason="auto classifier: no API key configured",
            )

        prompt = _build_cot_prompt(request)
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(
                api_key=self._api_key,
                base_url=self._base_url,
            )
            response = await asyncio.wait_for(
                client.messages.create(
                    model=self._model,
                    max_tokens=256,
                    system="You are a security classifier. Evaluate tool safety and respond with a JSON object.",
                    messages=[{"role": "user", "content": prompt}],
                ),
                timeout=self._timeout_ms / 1000.0,
            )
            classification = _parse_cot_response(response)
            return classification
        except asyncio.TimeoutError:
            # Timeout → safety conservative: ASK
            return PermissionResult(
                PermissionDecision.ASK,
                reason="auto classifier: timed out",
            )
        except Exception:
            raise  # Let caller handle fallback

    def _is_system_path(self, path: str) -> bool:
        """Check if a path is a system-critical location."""
        if not path:
            return False
        system_prefixes = [
            "/etc",
            "/sys",
            "/proc",
            "/dev",
            "/boot",
            "/root",
            "/var/log",
            "C:\\Windows",
            "C:\\Program Files",
            "~/.ssh",
            "~/.gnupg",
            ".git/config",
            ".env",
        ]
        normalized = path.replace("\\", "/").lower()
        for prefix in system_prefixes:
            if normalized.startswith(prefix.replace("\\", "/").lower()):
                return True
        return False


def _build_cot_prompt(request: Any) -> str:
    """Build a structured prompt for the CoT classifier."""
    tool_input_summary = _summarize_input(request.tool_input)
    return (
        f"Classify the safety of this tool use:\n\n"
        f"Tool: {request.tool_name}\n"
        f"Category: {request.tool_category.value}\n"
        f"Input: {tool_input_summary}\n\n"
        f"Respond with a JSON object:\n"
        f'{{"decision": "safe"|"unsafe"|"review", "reason": "..."}}\n\n'
        f"Rules:\n"
        f"- 'safe': The operation is clearly safe and non-destructive.\n"
        f"- 'unsafe': The operation is destructive, accesses sensitive data, "
        f"or operates outside the project scope.\n"
        f"- 'review': Unclear — a human should review this.\n"
    )


def _summarize_input(tool_input: dict) -> str:
    """Create a safe summary of tool input, truncating long values."""
    import json

    parts = []
    for k, v in tool_input.items():
        s = json.dumps(v) if not isinstance(v, str) else v
        if len(s) > 200:
            s = s[:200] + "..."
        parts.append(f"  {k}: {s}")
    return "\n".join(parts) if parts else "(empty)"


def _parse_cot_response(response: Any) -> Any:
    """Parse the CoT model response into a PermissionResult."""
    import json

    from d2c.permissions import PermissionDecision, PermissionResult

    # Extract text from response
    content = getattr(response, "content", "")
    if isinstance(content, str):
        text = content
    elif hasattr(content, "__iter__"):
        texts = []
        for block in content:
            if hasattr(block, "text"):
                texts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        text = "\n".join(texts)
    else:
        text = str(content)

    # Try to parse JSON from the response
    try:
        # Find JSON object in the response
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            json_str = text[start : end + 1]
            data = json.loads(json_str)
            decision = data.get("decision", "review").lower()
            reason = data.get("reason", "model classification")
            if decision == "safe":
                return PermissionResult(PermissionDecision.ALLOW, reason=f"auto (CoT): {reason}")
            elif decision == "unsafe":
                return PermissionResult(PermissionDecision.DENY, reason=f"auto (CoT): {reason}")
            else:
                return PermissionResult(PermissionDecision.ASK, reason=f"auto (CoT): {reason}")
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: treat as ASK
    return PermissionResult(
        PermissionDecision.ASK,
        reason="auto (CoT): could not parse classification",
    )
