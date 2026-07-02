"""Subagent capability profiles (Phase 61).

YAML-defined named subagent profiles loaded from a TRUSTED workspace's
``.d2c/agents/*.yaml`` (and ``*.yml``). A profile declares the subagent's
model, permission mode, tool allow/deny boundaries, optional worktree
isolation, and instructions (system prompt).

Profiles are project-local, executable-ish config — they set a subagent's
custom system prompt, its permission mode, and its tool boundaries — so
they load ONLY when the workspace is trusted, the same gate applied to
``.env`` / CLAUDE.md / skills / MCP. Legacy ``.d2c/agents/*.md`` frontmatter
agents (scalar-only) continue to load via ``subagent.load_subagent_definition``.

Example (``.d2c/agents/security-reviewer.yaml``):

    name: security-reviewer
    model: deepseek-v4-pro
    permission_mode: plan
    tools:
      allow: [Read, Grep, Glob, GitDiff]
      deny: [Write, Edit, Bash]
    isolation: worktree
    instructions: |
      Review for security vulnerabilities.

Malformed profiles are reported (as error strings) and skipped — one bad
profile never crashes subagent loading or the session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from d2c.subagent import SubagentDefinition, SubagentType

# Kept in sync with permissions.PermissionMode / settings._VALID_PERMISSION_MODES.
_VALID_PERMISSION_MODES = {"plan", "default", "acceptEdits", "dontAsk", "auto", "bypass"}
_VALID_ISOLATION = {"default", "worktree"}
_PROFILE_GLOBS = ("*.yaml", "*.yml")


class SubagentProfileError(ValueError):
    """A capability profile failed validation."""


def profiles_dir(cwd: Path) -> Path:
    return cwd / ".d2c" / "agents"


def _as_str_list(value: Any, field_name: str) -> list[str] | None:
    """Coerce a YAML scalar/list into ``list[str]`` (or None). Raises on a
    wrong shape. A bare string is treated as a comma/space-separated list for
    convenience (``tools: "Read, Grep"``)."""
    if value is None:
        return None
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace(",", " ").split()]
        return parts or None
    if isinstance(value, list):
        if not all(isinstance(x, str) for x in value):
            raise SubagentProfileError(f"'{field_name}' must be a list of tool-name strings")
        return list(value) or None
    raise SubagentProfileError(f"'{field_name}' must be a string or list of strings")


def parse_profile(data: Any, source: str) -> SubagentDefinition:
    """Validate one profile mapping and build a SubagentDefinition.

    Raises SubagentProfileError with an actionable message on any invalid
    field, rather than producing a subtly-broken definition.
    """
    if not isinstance(data, dict):
        raise SubagentProfileError(f"{source}: profile must be a YAML mapping")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SubagentProfileError(
            f"{source}: profile 'name' is required and must be a non-empty string"
        )
    name = name.strip()

    # tools: nested {allow, deny}, or a flat allowlist (list/string).
    allow: list[str] | None = None
    deny: list[str] | None = None
    tools = data.get("tools")
    if isinstance(tools, dict):
        allow = _as_str_list(tools.get("allow"), "tools.allow")
        deny = _as_str_list(tools.get("deny"), "tools.deny")
    elif tools is not None:
        allow = _as_str_list(tools, "tools")
    if deny is None:
        deny = _as_str_list(
            data.get("disallowed_tools", data.get("disallowedTools")), "disallowed_tools"
        )

    permission_mode = data.get("permission_mode", data.get("permissionMode"))
    if permission_mode is not None and (
        not isinstance(permission_mode, str) or permission_mode not in _VALID_PERMISSION_MODES
    ):
        raise SubagentProfileError(
            f"{source}: invalid permission_mode {permission_mode!r}; "
            f"expected one of {sorted(_VALID_PERMISSION_MODES)}"
        )

    isolation = data.get("isolation", "default")
    # isinstance guard first: an unhashable value (list/dict) would make the
    # `not in` membership test raise TypeError rather than SubagentProfileError.
    if not isinstance(isolation, str) or isolation not in _VALID_ISOLATION:
        raise SubagentProfileError(
            f"{source}: invalid isolation {isolation!r}; expected one of {sorted(_VALID_ISOLATION)}"
        )

    model = data.get("model")
    if model is not None and not isinstance(model, str):
        raise SubagentProfileError(f"{source}: 'model' must be a string")

    max_turns = data.get("max_turns", data.get("maxTurns", 25))
    if not isinstance(max_turns, int) or isinstance(max_turns, bool) or max_turns < 1:
        raise SubagentProfileError(f"{source}: 'max_turns' must be a positive integer")

    background = data.get("background", False)
    if not isinstance(background, bool):
        raise SubagentProfileError(f"{source}: 'background' must be true/false")

    instructions = data.get("instructions")
    if instructions is None:
        instructions = data.get("system_prompt", "")
    if not isinstance(instructions, str):
        raise SubagentProfileError(f"{source}: 'instructions' must be a string")

    description = data.get("description", "")
    if not isinstance(description, str):
        raise SubagentProfileError(f"{source}: 'description' must be a string")

    return SubagentDefinition(
        name=name,
        description=description,
        system_prompt=instructions,
        subagent_type=SubagentType.CUSTOM,
        tools=allow,
        disallowed_tools=deny,
        model=model,
        permission_mode=permission_mode,
        max_turns=max_turns,
        background=background,
        isolation=isolation,
    )


def load_profiles(cwd: Path, trusted: bool) -> tuple[dict[str, SubagentDefinition], list[str]]:
    """Discover + parse YAML capability profiles under ``.d2c/agents/``.

    Returns ``({name: definition}, [error strings])``. These are project-local
    definitions, so nothing is loaded when the workspace is untrusted. Never
    raises: unreadable files, invalid YAML, and invalid profiles are recorded
    as error strings and skipped individually.
    """
    if not trusted:
        return {}, []
    directory = profiles_dir(cwd)
    if not directory.is_dir():
        return {}, []

    import yaml

    profiles: dict[str, SubagentDefinition] = {}
    errors: list[str] = []
    seen: set[Path] = set()
    for glob in _PROFILE_GLOBS:
        try:
            matches = sorted(directory.glob(glob))
        except OSError as e:
            # A permission error while scanning the directory must not raise
            # out of the loader (contract: never raises).
            errors.append(f"{directory}: cannot list {glob}: {e}")
            continue
        for path in matches:
            if path in seen:
                continue
            seen.add(path)
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError as e:
                errors.append(f"{path}: cannot read: {e}")
                continue
            try:
                data = yaml.safe_load(raw)
            except yaml.YAMLError as e:
                errors.append(f"{path}: invalid YAML: {e}")
                continue
            try:
                definition = parse_profile(data, source=str(path))
            except SubagentProfileError as e:
                errors.append(str(e))
                continue
            profiles[definition.name] = definition
    return profiles, errors
