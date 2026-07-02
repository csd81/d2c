"""Configuration and settings resolution.

Paper Section 4.1 step 1: immutable parameters resolved at startup.
All subsequent phases extend this module.

Phase 10: DeepSeek wiring — .env loading, model name mapping, API key validation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from d2c.permissions import PermissionRule
from d2c.tools.pool import Rule

# ── DeepSeek model name mapping ─────────────────────────────────────────

# Canonical model IDs that DeepSeek's Anthropic-compatible API accepts.
# Short aliases are mapped to full model IDs for user convenience.
DEEPSEEK_MODEL_ALIASES: dict[str, str] = {
    # Short aliases → canonical IDs
    "flash": "deepseek-v4-flash",
    "v4-flash": "deepseek-v4-flash",
    "pro": "deepseek-v4-pro",
    "v4": "deepseek-v4-pro",
    "v4-pro": "deepseek-v4-pro",
    # Canonical IDs pass through
    "deepseek-v4-flash": "deepseek-v4-flash",
    "deepseek-v4-pro": "deepseek-v4-pro",
}

# Model-specific parameter defaults. deepseek-v4-flash first (the default).
DEEPSEEK_MODEL_DEFAULTS: dict[str, dict] = {
    "deepseek-v4-flash": {
        "max_tokens": 8192,
        "context_window": 128_000,
    },
    "deepseek-v4-pro": {
        "max_tokens": 8192,
        "context_window": 128_000,
    },
}


def resolve_model(model: str) -> str:
    """Resolve a model alias to its canonical DeepSeek model ID.

    Returns the original string if no mapping found (allows custom model names).
    """
    return DEEPSEEK_MODEL_ALIASES.get(model.lower(), model)


# ── DeepSeek thinking control (Phase 82) ────────────────────────────────

# Preset → thinking token budget. "off" sends no thinking payload at all.
# Budgets follow the plan's conservative defaults; override the whole feature
# per-call by picking a different preset (D2C_THINKING / --thinking).
THINKING_BUDGETS: dict[str, int | None] = {
    "off": None,
    "low": 4096,
    "medium": 8192,
    "high": 16384,
}
VALID_THINKING_MODES = tuple(THINKING_BUDGETS.keys())


def thinking_budget(mode: str | None) -> int | None:
    """The budget_tokens for a thinking preset, or None for 'off'/unknown
    (unknown falls back to off — validation surfaces the bad value separately)."""
    return THINKING_BUDGETS.get((mode or "off").strip().lower())


def get_model_defaults(model: str) -> dict:
    """Get default parameters for a resolved model."""
    resolved = resolve_model(model)
    return DEEPSEEK_MODEL_DEFAULTS.get(resolved, {"max_tokens": 8192, "context_window": 128_000})


# ── .env loading ───────────────────────────────────────────────────────


def _load_home_dotenv() -> None:
    """Load ~/.d2c/.env only. Always safe — the user controls their home dir."""
    home_env = Path.home() / ".d2c" / ".env"
    if home_env.exists():
        _parse_env_file(home_env)


def _load_project_dotenv(cwd: Path) -> None:
    """Load project .env and parent .env files. Only called if workspace is trusted."""
    env_files: list[Path] = []

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
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]

        # Don't override existing env vars
        if key not in os.environ:
            os.environ[key] = value


# ── Config dataclass ───────────────────────────────────────────────────


@dataclass
class Config:
    """Central configuration. Loaded once at session start and treated as immutable."""

    # --- Model ---
    model: str = "deepseek-v4-flash"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com/anthropic"
    # Phase 82: DeepSeek thinking preset — off (default) | low | medium | high.
    thinking: str = "off"

    # --- Session ---
    cwd: Path = field(default_factory=Path.cwd)
    max_turns: int = 25

    # --- Permission (Phase 3) ---
    permission_mode: str = "default"
    # PermissionEngine.from_config() accepts any mix of these three shapes;
    # Phase 60 settings loading always produces plain dicts.
    permission_rules: list[Rule | PermissionRule | dict] = field(default_factory=list)

    # --- Scoped settings (Phase 60) ---
    settings_warnings: list[str] = field(default_factory=list)

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

    # --- Prompt Caching (Phase 26) ---
    prompt_caching_enabled: bool = True  # Anthropic prompt caching with 4 breakpoints

    # --- Shell sandboxing (Phase 34; Phase 62: OS-level backend) ---
    sandbox_enabled: bool = False  # gate BashTool execution through SandboxExecutor
    sandbox_backend: str = "process"  # "process" | "bubblewrap" | "docker"
    sandbox_allow_network: bool = False  # network inside the sandbox (bubblewrap/docker)
    sandbox_fallback: bool = False  # if an OS backend is unavailable, fall back to process

    # --- WebSearch (Phase 39; Phase 58: base_url for self-hosted providers) ---
    websearch_provider: str = ""  # e.g. "tavily", "brave", "searxng"; empty = unconfigured
    websearch_api_key: str | None = None
    websearch_base_url: str = ""  # e.g. SearXNG instance URL; not needed by tavily/brave

    # --- Observability (Phase 44) ---
    log_level: str = "INFO"
    audit_log_enabled: bool = False  # opt-in via D2C_AUDIT_LOG=1
    audit_log_path: str = ""  # default computed in load() if empty
    log_prompts: bool = False  # log full prompts (privacy: off)
    log_tool_outputs: bool = False  # log full tool outputs (privacy: off)

    # --- OS ---
    os: str = field(default="")

    def __post_init__(self) -> None:
        import platform

        if not self.os:
            self.os = platform.system()

        # Resolve model alias to canonical name
        self.model = resolve_model(self.model)

        # Phase 82: normalize the thinking preset (validated in validate()).
        self.thinking = (self.thinking or "off").strip().lower()

        # Apply model-specific defaults for context window
        defaults = get_model_defaults(self.model)
        if self.context_window_tokens == 128_000:
            self.context_window_tokens = defaults.get("context_window", 128_000)

    @classmethod
    def load(cls, cwd: Path | None = None) -> "Config":
        """Load configuration from environment, .env files, and project files.

        Resolution order (later wins), Phase 60 folds in above env/defaults:
        1. Hardcoded defaults
        2. Home .d2c/.env
        3. Project .env (walked up to root)
        4. Shell environment variables (DEEPSEEK_API_KEY, etc.)
        5. Layered settings (managed > user > project > local) for
           permission_mode/sandbox_enabled/permission_rules/hooks — a scalar
           set by any settings scope overrides its env/default value; a
           managed-set scalar cannot be overridden by a lower settings scope.
        """
        project_dir = cwd or Path.cwd()

        # Always load home .env
        _load_home_dotenv()

        # Only load project .env if workspace is trusted
        from d2c.trust import get_trust_gate

        trusted = get_trust_gate().is_project_trusted
        if trusted:
            _load_project_dotenv(project_dir)

        # Read from environment
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")
        model = os.environ.get("D2C_MODEL", "deepseek-v4-flash")
        thinking = os.environ.get("D2C_THINKING", "off")
        sandbox_enabled = os.environ.get("D2C_SANDBOX", "").lower() in ("1", "true", "yes", "on")
        sandbox_backend = (
            os.environ.get("D2C_SANDBOX_BACKEND", "process").strip().lower() or "process"
        )
        websearch_provider = os.environ.get("D2C_WEBSEARCH_PROVIDER", "").strip().lower()
        websearch_api_key = os.environ.get("D2C_WEBSEARCH_API_KEY") or None
        websearch_base_url = os.environ.get("D2C_WEBSEARCH_BASE_URL", "").strip()

        def _flag(name: str) -> bool:
            return os.environ.get(name, "").lower() in ("1", "true", "yes", "on")

        sandbox_allow_network = _flag("D2C_SANDBOX_NETWORK")
        sandbox_fallback = _flag("D2C_SANDBOX_FALLBACK")

        log_level = os.environ.get("D2C_LOG_LEVEL", "INFO").upper()
        audit_log_enabled = _flag("D2C_AUDIT_LOG")
        audit_log_path = os.environ.get("D2C_AUDIT_LOG_PATH", "") or str(
            Path.home() / ".d2c" / "logs" / "audit.jsonl"
        )

        # Phase 60: layered settings (managed > user > project > local) sit
        # above env/defaults. Malformed files never raise — they surface as
        # settings_warnings via validate().
        from d2c.settings import load_settings

        merged_settings = load_settings(project_dir, trusted)
        permission_mode = merged_settings.permission_mode or "default"
        if merged_settings.sandbox_enabled is not None:
            sandbox_enabled = merged_settings.sandbox_enabled

        return cls(
            model=model,
            thinking=thinking,
            deepseek_api_key=api_key,
            deepseek_base_url=base_url,
            cwd=project_dir,
            permission_mode=permission_mode,
            permission_rules=list(merged_settings.permission_rules),
            hooks=list(merged_settings.hooks),
            sandbox_enabled=sandbox_enabled,
            sandbox_backend=sandbox_backend,
            sandbox_allow_network=sandbox_allow_network,
            sandbox_fallback=sandbox_fallback,
            websearch_provider=websearch_provider,
            websearch_api_key=websearch_api_key,
            websearch_base_url=websearch_base_url,
            log_level=log_level,
            audit_log_enabled=audit_log_enabled,
            audit_log_path=audit_log_path,
            log_prompts=_flag("D2C_LOG_PROMPTS"),
            log_tool_outputs=_flag("D2C_LOG_TOOL_OUTPUTS"),
            settings_warnings=merged_settings.warnings(),
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

        if self.thinking not in VALID_THINKING_MODES:
            issues.append(
                f"Thinking mode '{self.thinking}' is invalid; "
                f"expected one of {', '.join(VALID_THINKING_MODES)}. Treating as 'off'."
            )

        # Phase 60: malformed settings files / blocked managed-lock override
        # attempts surface here rather than raising during load().
        issues.extend(f"Settings: {w}" for w in self.settings_warnings)

        return issues
