"""Configuration and settings resolution.

Paper Section 4.1 step 1: immutable parameters resolved at startup.
All subsequent phases extend this module.

Phase 10: DeepSeek wiring — .env loading, model name mapping, API key validation.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from d2c.tools.pool import Rule


# ── DeepSeek model name mapping ─────────────────────────────────────────

# Canonical model IDs that DeepSeek's Anthropic-compatible API accepts.
# Short aliases are mapped to full model IDs for user convenience.
DEEPSEEK_MODEL_ALIASES: dict[str, str] = {
    # Short aliases → canonical IDs
    "v4": "deepseek-v4-pro",
    "v4-pro": "deepseek-v4-pro",
    "v3": "deepseek-chat",
    "chat": "deepseek-chat",
    "r1": "deepseek-reasoner",
    "reasoner": "deepseek-reasoner",
    # Canonical IDs pass through
    "deepseek-v4-pro": "deepseek-v4-pro",
    "deepseek-chat": "deepseek-chat",
    "deepseek-reasoner": "deepseek-reasoner",
}

# Model-specific parameter defaults
DEEPSEEK_MODEL_DEFAULTS: dict[str, dict] = {
    "deepseek-v4-pro": {
        "max_tokens": 8192,
        "context_window": 128_000,
    },
    "deepseek-chat": {
        "max_tokens": 8192,
        "context_window": 128_000,
    },
    "deepseek-reasoner": {
        "max_tokens": 8192,
        "context_window": 128_000,
    },
}


def resolve_model(model: str) -> str:
    """Resolve a model alias to its canonical DeepSeek model ID.

    Returns the original string if no mapping found (allows custom model names).
    """
    return DEEPSEEK_MODEL_ALIASES.get(model.lower(), model)


def get_model_defaults(model: str) -> dict:
    """Get default parameters for a resolved model."""
    resolved = resolve_model(model)
    return DEEPSEEK_MODEL_DEFAULTS.get(resolved, {"max_tokens": 8192, "context_window": 128_000})


# ── .env loading ───────────────────────────────────────────────────────

def _load_dotenv(cwd: Path) -> None:
    """Load .env file from project directory and home directory.

    Project .env overrides home .env. Does NOT override existing env vars.
    """
    env_files = []

    # Home directory .env
    home_env = Path.home() / ".d2c" / ".env"
    if home_env.exists():
        env_files.append(home_env)

    # Project directory .env
    project_env = cwd / ".env"
    if project_env.exists():
        env_files.append(project_env)

    # Walk up from cwd looking for .env files
    current = cwd.resolve()
    root = Path(current.anchor)
    dirs = []
    while current != root.parent:
        dirs.append(current)
        current = current.parent
    for d in reversed(dirs):
        env = d / ".env"
        if env.exists() and env not in env_files:
            env_files.append(env)

    for env_file in env_files:
        _parse_env_file(env_file)


def _parse_env_file(path: Path) -> None:
    """Parse a .env file and set os.environ for keys not already set.

    Handles: KEY=value, KEY="value", KEY='value', comments (#), blank lines.
    Does NOT override existing environment variables (shell takes precedence).
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Handle export KEY=value
        if line.startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Strip surrounding quotes
        if len(value) >= 2:
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]

        # Don't override existing env vars
        if key not in os.environ:
            os.environ[key] = value


# ── Config dataclass ───────────────────────────────────────────────────

@dataclass
class Config:
    """Central configuration. Loaded once at session start and treated as immutable."""

    # --- Model ---
    model: str = "deepseek-v4-pro"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com/anthropic"

    # --- Session ---
    cwd: Path = field(default_factory=Path.cwd)
    max_turns: int = 25

    # --- Permission (Phase 3) ---
    permission_mode: str = "default"
    permission_rules: list[Rule] = field(default_factory=list)

    # --- Compaction (Phase 5) ---
    tool_result_max_chars: int = 30_000
    pressure_threshold: float = 0.85
    context_window_tokens: int = 128_000

    # --- Memory (Phase 6) ---
    claude_md_files: list[Path] = field(default_factory=list)

    # --- Hooks (Phase 7) ---
    hooks: list[dict] = field(default_factory=list)

    # --- KAIROS (Phase 25) ---
    kairos_enabled: bool = False  # Feature-gated, off by default

    # --- OS ---
    os: str = field(default="")

    def __post_init__(self) -> None:
        import platform
        if not self.os:
            self.os = platform.system()

        # Resolve model alias to canonical name
        self.model = resolve_model(self.model)

        # Apply model-specific defaults for context window
        defaults = get_model_defaults(self.model)
        if self.context_window_tokens == 128_000:
            self.context_window_tokens = defaults.get("context_window", 128_000)

    @classmethod
    def load(cls, cwd: Path | None = None) -> "Config":
        """Load configuration from environment, .env files, and project files.

        Resolution order (later wins):
        1. Hardcoded defaults
        2. Home .d2c/.env
        3. Project .env (walked up to root)
        4. Shell environment variables (DEEPSEEK_API_KEY, etc.)
        """
        project_dir = cwd or Path.cwd()

        # Load .env files (won't override existing env vars)
        _load_dotenv(project_dir)

        # Read from environment
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")
        model = os.environ.get("D2C_MODEL", "deepseek-v4-pro")

        return cls(
            model=model,
            deepseek_api_key=api_key,
            deepseek_base_url=base_url,
            cwd=project_dir,
        )

    def validate(self) -> list[str]:
        """Validate configuration. Returns list of warnings/errors."""
        issues: list[str] = []

        if not self.deepseek_api_key:
            issues.append(
                "DEEPSEEK_API_KEY is not set. Set it via environment variable or .env file.\n"
                "  export DEEPSEEK_API_KEY=sk-..."
            )

        resolved = resolve_model(self.model)
        if resolved not in DEEPSEEK_MODEL_DEFAULTS:
            issues.append(
                f"Model '{self.model}' is not a recognized DeepSeek model. "
                f"Known models: {', '.join(DEEPSEEK_MODEL_DEFAULTS.keys())}"
            )

        return issues
