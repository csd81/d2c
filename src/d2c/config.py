"""Configuration and settings resolution.

Paper Section 4.1 step 1: immutable parameters resolved at startup.
All subsequent phases extend this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from d2c.tools.pool import Rule


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

    # --- OS ---
    os: str = field(default="")

    def __post_init__(self) -> None:
        import platform
        if not self.os:
            self.os = platform.system()

    @classmethod
    def load(cls, cwd: Path | None = None) -> "Config":
        """Load configuration from environment and project files."""
        project_dir = cwd or Path.cwd()

        api_key = os.environ.get("DEEPSEEK_API_KEY")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/anthropic")
        model = os.environ.get("D2C_MODEL", "deepseek-v4-pro")

        return cls(
            model=model,
            deepseek_api_key=api_key,
            deepseek_base_url=base_url,
            cwd=project_dir,
        )
