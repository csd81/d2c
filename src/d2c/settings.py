"""Layered settings: managed > user > project > local > env/defaults (Phase 60).

Mirrors the CLAUDE.md 4-level hierarchy (memory.py) for governance settings
instead of prompt content: permission_mode, sandbox_enabled, permission_rules
(allow/deny), hooks.

Loading order:
  1. Managed  (/etc/d2c/settings.yaml, or D2C_MANAGED_SETTINGS_PATH override)
  2. User     (~/.d2c/settings.yaml)
  3. Project  (.d2c/settings.yaml)          -- only when trusted
  4. Local    (.d2c/settings.local.yaml)    -- only when trusted, gitignored

Precedence:
  - Scalar fields (permission_mode, sandbox_enabled): the FIRST scope, in
    managed-to-local order, that sets the field wins outright — lower scopes
    cannot override it. This is the "managed policy lock": once managed (or
    any higher scope) sets a value, later scopes setting the same field are
    recorded as blocked override attempts, not applied.
  - List fields (permission_rules, hooks): unioned across all scopes that
    define them. This is deliberate, not an oversight — PermissionEngine's
    deny-first evaluation already guarantees a deny rule from any scope
    beats an allow rule from any other scope regardless of order, so a
    managed deny rule can never be defeated by a lower scope's allow rule
    as long as both rules survive into the merged list.
  - Malformed files (bad YAML, wrong shape, invalid rule/hook entries) are
    reported as load errors and skipped — never raised. A broken project
    settings.yaml must not crash the session.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SettingsScope(Enum):
    MANAGED = 1
    USER = 2
    PROJECT = 3
    LOCAL = 4


SCOPE_ORDER = [
    SettingsScope.MANAGED,
    SettingsScope.USER,
    SettingsScope.PROJECT,
    SettingsScope.LOCAL,
]

_SCALAR_FIELDS = ("permission_mode", "sandbox_enabled")
_LIST_FIELDS = ("permission_rules", "hooks")
_VALID_PERMISSION_MODES = {"plan", "default", "acceptEdits", "dontAsk", "auto", "bypass"}
_VALID_RULE_TYPES = {"deny", "allow"}
_VALID_HOOK_TYPES = {"command", "prompt"}  # "callback" is SDK-internal, not settable via YAML


# ── Types ────────────────────────────────────────────────────────────


@dataclass
class SettingsLoadError:
    path: Path
    scope: SettingsScope
    message: str

    def __str__(self) -> str:
        return f"[{self.scope.name.lower()}] {self.path}: {self.message}"


@dataclass
class SettingsFile:
    path: Path
    scope: SettingsScope
    data: dict[str, Any]


@dataclass
class OverrideAttempt:
    """A lower scope tried to set a field a higher scope already locked."""

    field: str
    locked_by: SettingsScope
    attempted_scope: SettingsScope
    attempted_value: Any

    def __str__(self) -> str:
        return (
            f"'{self.field}' is locked by {self.locked_by.name.lower()} settings; "
            f"{self.attempted_scope.name.lower()} settings' value "
            f"({self.attempted_value!r}) was ignored"
        )


@dataclass
class MergedSettings:
    permission_mode: str | None = None
    sandbox_enabled: bool | None = None
    permission_rules: list[dict] = field(default_factory=list)
    hooks: list[dict] = field(default_factory=list)
    sources: dict[str, SettingsScope] = field(default_factory=dict)
    loaded_files: list[SettingsFile] = field(default_factory=list)
    errors: list[SettingsLoadError] = field(default_factory=list)
    overridden_attempts: list[OverrideAttempt] = field(default_factory=list)

    def warnings(self) -> list[str]:
        """Human-readable messages for Config.validate()/doctor output."""
        return [str(e) for e in self.errors] + [str(a) for a in self.overridden_attempts]


# ── File locations ───────────────────────────────────────────────────


def managed_settings_path() -> Path:
    override = os.environ.get("D2C_MANAGED_SETTINGS_PATH", "").strip()
    if override:
        return Path(override)
    return Path("/etc/d2c/settings.yaml")


def user_settings_path() -> Path:
    return Path.home() / ".d2c" / "settings.yaml"


def project_settings_path(cwd: Path) -> Path:
    return cwd / ".d2c" / "settings.yaml"


def local_settings_path(cwd: Path) -> Path:
    return cwd / ".d2c" / "settings.local.yaml"


# ── Discovery + parsing ──────────────────────────────────────────────


def _read_yaml_mapping(
    path: Path, scope: SettingsScope
) -> tuple[dict[str, Any] | None, SettingsLoadError | None]:
    """Read+parse one settings file. Returns (data, error) — never raises.

    (None, None) means the file doesn't exist (not an error).
    """
    if not path.exists():
        return None, None
    if not path.is_file():
        return None, SettingsLoadError(path=path, scope=scope, message="not a regular file")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, SettingsLoadError(path=path, scope=scope, message=f"cannot read file: {e}")

    import yaml

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return None, SettingsLoadError(path=path, scope=scope, message=f"invalid YAML: {e}")

    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return None, SettingsLoadError(
            path=path,
            scope=scope,
            message="settings file must be a YAML mapping (dict) at the top level",
        )
    return data, None


def discover_settings_files(
    cwd: Path, trusted: bool
) -> tuple[list[SettingsFile], list[SettingsLoadError]]:
    """Discover and parse settings files in scope precedence order.

    Managed and user settings always load (same as the CLAUDE.md managed/user
    tiers). Project/local only load when the workspace is trusted — same
    trust gating as .env and CLAUDE.md project/local tiers.
    """
    files: list[SettingsFile] = []
    errors: list[SettingsLoadError] = []

    candidates: list[tuple[Path, SettingsScope, bool]] = [
        (managed_settings_path(), SettingsScope.MANAGED, True),
        (user_settings_path(), SettingsScope.USER, True),
        (project_settings_path(cwd), SettingsScope.PROJECT, trusted),
        (local_settings_path(cwd), SettingsScope.LOCAL, trusted),
    ]
    for path, scope, allowed in candidates:
        if not allowed:
            continue
        data, err = _read_yaml_mapping(path, scope)
        if err is not None:
            errors.append(err)
            continue
        if data is not None:
            files.append(SettingsFile(path=path, scope=scope, data=data))
    return files, errors


# ── Merging ──────────────────────────────────────────────────────────


def _merge_scalars(by_scope: dict[SettingsScope, SettingsFile], merged: MergedSettings) -> None:
    for field_name in _SCALAR_FIELDS:
        winner_scope: SettingsScope | None = None
        winner_value: Any = None
        for scope in SCOPE_ORDER:
            sf = by_scope.get(scope)
            if sf is None or field_name not in sf.data:
                continue
            value = sf.data[field_name]
            if winner_scope is None:
                winner_scope, winner_value = scope, value
            else:
                merged.overridden_attempts.append(
                    OverrideAttempt(
                        field=field_name,
                        locked_by=winner_scope,
                        attempted_scope=scope,
                        attempted_value=value,
                    )
                )
        if winner_scope is not None:
            setattr(merged, field_name, winner_value)
            merged.sources[field_name] = winner_scope

    # A settings-supplied permission_mode must be one of the real modes —
    # governance config should fail loudly (as a load error), not silently
    # produce an engine that rejects the mode later.
    if merged.permission_mode is not None and merged.permission_mode not in _VALID_PERMISSION_MODES:
        scope = merged.sources.pop("permission_mode")
        sf = by_scope.get(scope)
        merged.errors.append(
            SettingsLoadError(
                path=sf.path if sf else Path("<unknown>"),
                scope=scope,
                message=(
                    f"invalid permission_mode '{merged.permission_mode}'; "
                    f"expected one of {sorted(_VALID_PERMISSION_MODES)}"
                ),
            )
        )
        merged.permission_mode = None

    if merged.sandbox_enabled is not None and not isinstance(merged.sandbox_enabled, bool):
        scope = merged.sources.pop("sandbox_enabled")
        sf = by_scope.get(scope)
        merged.errors.append(
            SettingsLoadError(
                path=sf.path if sf else Path("<unknown>"),
                scope=scope,
                message=f"'sandbox_enabled' must be true/false, got {merged.sandbox_enabled!r}",
            )
        )
        merged.sandbox_enabled = None


def _validate_rule(entry: Any) -> str | None:
    """Return an error message if entry isn't a valid permission-rule dict."""
    if not isinstance(entry, dict):
        return "each permission rule must be a mapping"
    rule_type = entry.get("type", entry.get("rule_type"))
    if rule_type not in _VALID_RULE_TYPES:
        return f"rule 'type' must be one of {sorted(_VALID_RULE_TYPES)}, got {rule_type!r}"
    pattern = entry.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return "rule 'pattern' must be a non-empty string"
    return None


def _validate_hook(entry: Any) -> str | None:
    """Return an error message if entry isn't a valid hook dict."""
    if not isinstance(entry, dict):
        return "each hook must be a mapping"
    if "event" not in entry:
        return "hook is missing required 'event'"
    hook_type = entry.get("type", "command")
    if hook_type not in _VALID_HOOK_TYPES:
        return (
            f"hook 'type' must be one of {sorted(_VALID_HOOK_TYPES)}, got {hook_type!r} "
            "('callback' hooks are SDK-internal and cannot be set via settings)"
        )
    try:
        from d2c.hooks import HookEvent

        HookEvent(entry["event"])
    except ValueError:
        return f"unknown hook event {entry['event']!r}"
    return None


def _merge_lists(by_scope: dict[SettingsScope, SettingsFile], merged: MergedSettings) -> None:
    validators = {"permission_rules": _validate_rule, "hooks": _validate_hook}
    for field_name in _LIST_FIELDS:
        combined: list[dict] = []
        validate = validators[field_name]
        for scope in SCOPE_ORDER:
            sf = by_scope.get(scope)
            if sf is None:
                continue
            values = sf.data.get(field_name)
            if not values:
                continue
            if not isinstance(values, list):
                merged.errors.append(
                    SettingsLoadError(
                        path=sf.path, scope=scope, message=f"'{field_name}' must be a list"
                    )
                )
                continue
            for entry in values:
                err = validate(entry)
                if err:
                    merged.errors.append(SettingsLoadError(path=sf.path, scope=scope, message=err))
                    continue
                combined.append(entry)
        if combined:
            setattr(merged, field_name, combined)


def merge_settings(files: list[SettingsFile]) -> MergedSettings:
    """Merge discovered settings files by scope precedence.

    Duplicate files for the same scope (shouldn't happen via
    discover_settings_files, but merge_settings doesn't assume it was the
    caller) keep the last one seen for that scope.
    """
    merged = MergedSettings(loaded_files=list(files))
    by_scope: dict[SettingsScope, SettingsFile] = {}
    for sf in files:
        by_scope[sf.scope] = sf

    _merge_scalars(by_scope, merged)
    _merge_lists(by_scope, merged)
    return merged


def load_settings(cwd: Path, trusted: bool) -> MergedSettings:
    """Discover, parse, and merge layered settings for this session."""
    files, discovery_errors = discover_settings_files(cwd, trusted)
    merged = merge_settings(files)
    merged.errors = discovery_errors + merged.errors
    return merged
