"""Auto-mode safety classifier — 2-stage fast-filter + chain-of-thought.

Paper Section 5: "Auto mode uses a two-stage fast-filter and chain-of-thought
evaluation of tool safety to balance autonomy with safety."

Stage 1 (fast-filter): Heuristic rules for clearly safe or unsafe operations.
  - Always safe: Read/Glob/Grep on text files
  - Always unsafe: Destructive shell commands (rm -rf /, format, etc.)

Stage 2 (CoT): For ambiguous cases, calls a fast model with structured
  safety evaluation prompt. The model classifies as safe/unsafe/review.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from d2c.tools import PermissionCategory

if TYPE_CHECKING:
    from d2c.permissions import PermissionRequest, PermissionResult

logger = logging.getLogger(__name__)

# Fast-filter: known-safe tool + input patterns
SAFE_READ_TOOLS = frozenset({"Read", "Glob", "Grep", "FileRead", "WebFetch", "WebSearch", "TaskList"})
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
SAFE_SHELL_COMMANDS = frozenset({
    "ls", "dir", "cat", "type", "echo", "pwd", "cd",
    "head", "tail", "wc", "sort", "uniq", "find", "grep",
    "git", "python", "node", "npm", "npx", "cargo", "go",
    "which", "where", "whoami", "hostname", "date", "time",
    "mkdir", "touch", "cp", "mv",
})


class AutoClassifier:
    """Two-stage safety classifier for AUTO permission mode.

    Stage 1: Fast heuristic filter (no API call).
    Stage 2: Chain-of-thought model evaluation (API call).
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.deepseek.com/anthropic",
        model: str = "deepseek-chat",  # Fast/cheap model for CoT
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

        # Shell commands: check for destructive patterns
        if tool_name == "Bash":
            cmd = tool_input.get("command", "").strip()

            # Destructive → always deny
            cmd_lower = cmd.lower()
            for pattern in DESTRUCTIVE_PATTERNS:
                if pattern in cmd_lower:
                    return PermissionResult(
                        PermissionDecision.DENY,
                        reason=f"auto (fast-filter): destructive command blocked",
                    )

            # Known-safe command
            first_word = cmd.split()[0] if cmd else ""
            if first_word and first_word.lower() in SAFE_SHELL_COMMANDS:
                return PermissionResult(
                    PermissionDecision.ALLOW,
                    reason=f"auto (fast-filter): safe shell command '{first_word}'",
                )

            # Unknown shell command → ambiguous, needs CoT
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
        import json

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
        except Exception as e:
            raise  # Let caller handle fallback

    def _is_system_path(self, path: str) -> bool:
        """Check if a path is a system-critical location."""
        if not path:
            return False
        system_prefixes = [
            "/etc", "/sys", "/proc", "/dev",
            "/boot", "/root", "/var/log",
            "C:\\Windows", "C:\\Program Files",
            "~/.ssh", "~/.gnupg",
            ".git/config", ".env",
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
            json_str = text[start:end + 1]
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
