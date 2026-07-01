"""Phase 61: subagent capability profiles.

Covers YAML profile parsing/validation, trust-gated loading, allow/deny
tool boundaries, permission-mode + isolation threading through AgentTool,
and invalid-profile error reporting.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml

from d2c.config import Config
from d2c.subagent import (
    SubagentType,
    build_subagent_tool_pool,
    load_subagent_definition,
)
from d2c.subagent_profiles import (
    SubagentProfileError,
    load_profiles,
    parse_profile,
)
from d2c.tools import PermissionCategory

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def sample_tools():
    class MockTool:
        def __init__(self, name, category, concurrent=True):
            self.name = name
            self.category = category
            self.is_concurrent_safe = concurrent

    return [
        MockTool("Read", PermissionCategory.READ, True),
        MockTool("Write", PermissionCategory.WRITE, False),
        MockTool("Edit", PermissionCategory.WRITE, False),
        MockTool("Bash", PermissionCategory.SHELL, False),
        MockTool("Glob", PermissionCategory.READ, True),
        MockTool("Grep", PermissionCategory.READ, True),
        MockTool("GitDiff", PermissionCategory.READ, True),
        MockTool("Agent", PermissionCategory.META, True),
    ]


def _write_profile(cwd, filename, data):
    agents = cwd / ".d2c" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / filename).write_text(yaml.safe_dump(data) if isinstance(data, dict) else data)


_SECURITY_REVIEWER = {
    "name": "security-reviewer",
    "model": "deepseek-reasoner",
    "permission_mode": "plan",
    "tools": {"allow": ["Read", "Grep", "Glob", "GitDiff"], "deny": ["Write", "Edit", "Bash"]},
    "isolation": "worktree",
    "instructions": "Review for security vulnerabilities.\n",
}


# ── Profile parsing ──────────────────────────────────────────────────


def test_parse_full_profile():
    d = parse_profile(_SECURITY_REVIEWER, source="test")
    assert d.name == "security-reviewer"
    assert d.subagent_type == SubagentType.CUSTOM
    assert d.model == "deepseek-reasoner"
    assert d.permission_mode == "plan"
    assert d.tools == ["Read", "Grep", "Glob", "GitDiff"]
    assert d.disallowed_tools == ["Write", "Edit", "Bash"]
    assert d.isolation == "worktree"
    assert d.system_prompt.strip() == "Review for security vulnerabilities."


def test_parse_flat_tools_list_is_allowlist():
    d = parse_profile({"name": "a", "tools": ["Read", "Grep"]}, source="t")
    assert d.tools == ["Read", "Grep"]
    assert d.disallowed_tools is None


def test_parse_comma_string_tools():
    d = parse_profile({"name": "a", "tools": "Read, Grep"}, source="t")
    assert d.tools == ["Read", "Grep"]


def test_parse_legacy_denylist_key():
    d = parse_profile({"name": "a", "disallowed_tools": ["Bash"]}, source="t")
    assert d.disallowed_tools == ["Bash"]


def test_parse_camelcase_aliases():
    d = parse_profile(
        {"name": "a", "permissionMode": "dontAsk", "maxTurns": 7, "disallowedTools": ["Bash"]},
        source="t",
    )
    assert d.permission_mode == "dontAsk"
    assert d.max_turns == 7
    assert d.disallowed_tools == ["Bash"]


def test_parse_defaults():
    d = parse_profile({"name": "minimal"}, source="t")
    assert d.isolation == "default"
    assert d.max_turns == 25
    assert d.background is False
    assert d.permission_mode is None
    assert d.tools is None
    assert d.system_prompt == ""


def test_parse_instructions_or_system_prompt():
    d1 = parse_profile({"name": "a", "instructions": "hi"}, source="t")
    d2 = parse_profile({"name": "a", "system_prompt": "hi"}, source="t")
    assert d1.system_prompt == "hi"
    assert d2.system_prompt == "hi"


# ── Invalid profile errors ───────────────────────────────────────────


def test_parse_missing_name_raises():
    with pytest.raises(SubagentProfileError, match="name"):
        parse_profile({"model": "x"}, source="t")


def test_parse_non_mapping_raises():
    with pytest.raises(SubagentProfileError, match="mapping"):
        parse_profile(["not", "a", "mapping"], source="t")


def test_parse_invalid_permission_mode_raises():
    with pytest.raises(SubagentProfileError, match="permission_mode"):
        parse_profile({"name": "a", "permission_mode": "sudo"}, source="t")


def test_parse_invalid_isolation_raises():
    with pytest.raises(SubagentProfileError, match="isolation"):
        parse_profile({"name": "a", "isolation": "vm"}, source="t")


@pytest.mark.parametrize("bad", [["a", "b"], {"a": "b"}, 3])
def test_parse_unhashable_or_nonstring_isolation_raises_clean_error(bad):
    # Regression: an unhashable isolation value (list/dict) must raise
    # SubagentProfileError, not a raw TypeError from the `in` membership test.
    with pytest.raises(SubagentProfileError, match="isolation"):
        parse_profile({"name": "a", "isolation": bad}, source="t")


def test_parse_invalid_max_turns_raises():
    with pytest.raises(SubagentProfileError, match="max_turns"):
        parse_profile({"name": "a", "max_turns": 0}, source="t")
    with pytest.raises(SubagentProfileError, match="max_turns"):
        parse_profile({"name": "a", "max_turns": "lots"}, source="t")


def test_parse_invalid_tools_shape_raises():
    with pytest.raises(SubagentProfileError, match="tools"):
        parse_profile({"name": "a", "tools": [1, 2, 3]}, source="t")


def test_parse_invalid_background_raises():
    with pytest.raises(SubagentProfileError, match="background"):
        parse_profile({"name": "a", "background": "yes"}, source="t")


# ── load_profiles: discovery + trust + malformed reporting ──────────


def test_load_profiles_reads_yaml(tmp_dir):
    _write_profile(tmp_dir, "sec.yaml", _SECURITY_REVIEWER)
    profiles, errors = load_profiles(tmp_dir, trusted=True)
    assert errors == []
    assert "security-reviewer" in profiles
    assert profiles["security-reviewer"].isolation == "worktree"


def test_load_profiles_reads_yml_extension(tmp_dir):
    _write_profile(tmp_dir, "sec.yml", {"name": "yml-agent"})
    profiles, _ = load_profiles(tmp_dir, trusted=True)
    assert "yml-agent" in profiles


def test_load_profiles_untrusted_returns_empty(tmp_dir):
    _write_profile(tmp_dir, "sec.yaml", _SECURITY_REVIEWER)
    profiles, errors = load_profiles(tmp_dir, trusted=False)
    assert profiles == {}
    assert errors == []


def test_load_profiles_no_dir_returns_empty(tmp_dir):
    profiles, errors = load_profiles(tmp_dir, trusted=True)
    assert profiles == {} and errors == []


def test_load_profiles_malformed_yaml_reported_not_raised(tmp_dir):
    _write_profile(tmp_dir, "good.yaml", {"name": "good"})
    _write_profile(tmp_dir, "bad.yaml", "not: valid: yaml: [")
    profiles, errors = load_profiles(tmp_dir, trusted=True)
    assert "good" in profiles  # the good one still loads
    assert len(errors) == 1
    assert "invalid YAML" in errors[0]


def test_load_profiles_invalid_profile_reported_not_raised(tmp_dir):
    _write_profile(tmp_dir, "good.yaml", {"name": "good"})
    _write_profile(tmp_dir, "bad.yaml", {"permission_mode": "sudo"})  # missing name
    profiles, errors = load_profiles(tmp_dir, trusted=True)
    assert "good" in profiles
    assert len(errors) == 1


def test_load_profiles_unhashable_isolation_does_not_poison_others(tmp_dir):
    # A single file with an unhashable isolation value must not raise or
    # prevent the good profiles from loading.
    _write_profile(tmp_dir, "good.yaml", {"name": "good"})
    _write_profile(tmp_dir, "bad.yaml", {"name": "bad", "isolation": ["a", "b"]})
    profiles, errors = load_profiles(tmp_dir, trusted=True)
    assert "good" in profiles
    assert "bad" not in profiles
    assert len(errors) == 1


# ── load_subagent_definition: trust gating through the real gate ────


def test_load_definition_finds_profile_when_trusted(tmp_dir, trusted_gate):
    _write_profile(tmp_dir, "sec.yaml", _SECURITY_REVIEWER)
    d = load_subagent_definition("security-reviewer", cwd=tmp_dir)
    assert d.name == "security-reviewer"
    assert d.permission_mode == "plan"


def test_load_definition_skips_profile_when_untrusted(tmp_dir, untrusted_gate):
    _write_profile(tmp_dir, "sec.yaml", _SECURITY_REVIEWER)
    with pytest.raises(ValueError, match="Unknown subagent type"):
        load_subagent_definition("security-reviewer", cwd=tmp_dir)


def test_load_definition_builtins_available_even_untrusted(tmp_dir, untrusted_gate):
    # Built-in types must still resolve — the trust gate only guards
    # project-local definitions.
    d = load_subagent_definition("Explore", cwd=tmp_dir)
    assert d.subagent_type == SubagentType.EXPLORE


# ── Allow/deny tool boundaries (through a parsed profile) ───────────


def test_profile_allowlist_restricts_pool(sample_tools):
    d = parse_profile({"name": "a", "tools": {"allow": ["Read", "Grep"]}}, source="t")
    pool = build_subagent_tool_pool(d, sample_tools)
    assert {t.name for t in pool} == {"Read", "Grep"}


def test_profile_denylist_removes_tools(sample_tools):
    d = parse_profile({"name": "a", "tools": {"deny": ["Write", "Edit", "Bash"]}}, source="t")
    pool = build_subagent_tool_pool(d, sample_tools)
    names = {t.name for t in pool}
    assert "Write" not in names and "Edit" not in names and "Bash" not in names
    assert "Read" in names and "GitDiff" in names


def test_profile_allow_takes_precedence_over_deny_when_both(sample_tools):
    # build_subagent_tool_pool applies allowlist first (elif denylist), so an
    # allow list narrows the pool; deny is only consulted when no allow given.
    d = parse_profile(
        {"name": "a", "tools": {"allow": ["Read", "GitDiff"], "deny": ["Bash"]}}, source="t"
    )
    pool = build_subagent_tool_pool(d, sample_tools)
    assert {t.name for t in pool} == {"Read", "GitDiff"}


# ── AgentTool integration: mode + isolation threading ───────────────


@pytest.mark.asyncio
async def test_agent_tool_applies_profile_permission_mode(tmp_dir, trusted_gate):
    from d2c.subagent import SubagentResult
    from d2c.tools.agent_tool import AgentTool

    _write_profile(tmp_dir, "sec.yaml", _SECURITY_REVIEWER)
    config = Config(cwd=tmp_dir, deepseek_api_key="test-key")
    tool = AgentTool(config=config)

    with patch("d2c.subagent.spawn_subagent") as mock_spawn:
        mock_spawn.return_value = SubagentResult(summary="ok", success=True)
        await tool.execute(
            description="review", prompt="review it", subagent_type="security-reviewer"
        )

    call_def = mock_spawn.call_args[1]["definition"]
    assert call_def.permission_mode == "plan"
    assert call_def.model == "deepseek-reasoner"
    # profile isolation flows through as isolation_mode
    assert mock_spawn.call_args[1]["isolation_mode"] == "worktree"


@pytest.mark.asyncio
async def test_agent_tool_isolation_input_overrides_profile(tmp_dir, trusted_gate):
    from d2c.subagent import SubagentResult
    from d2c.tools.agent_tool import AgentTool

    _write_profile(tmp_dir, "sec.yaml", _SECURITY_REVIEWER)  # isolation: worktree
    config = Config(cwd=tmp_dir, deepseek_api_key="test-key")
    tool = AgentTool(config=config)

    with patch("d2c.subagent.spawn_subagent") as mock_spawn:
        mock_spawn.return_value = SubagentResult(summary="ok", success=True)
        await tool.execute(
            description="review",
            prompt="review it",
            subagent_type="security-reviewer",
            isolation="default",  # explicit override wins
        )

    assert mock_spawn.call_args[1]["isolation_mode"] == "default"
    assert mock_spawn.call_args[1]["definition"].isolation == "default"


@pytest.mark.asyncio
async def test_agent_tool_isolation_input_on_builtin():
    from d2c.subagent import SubagentResult
    from d2c.tools.agent_tool import AgentTool

    config = Config.load()
    config.deepseek_api_key = "test-key"
    tool = AgentTool(config=config)

    with patch("d2c.subagent.spawn_subagent") as mock_spawn:
        mock_spawn.return_value = SubagentResult(summary="ok", success=True)
        await tool.execute(
            description="explore",
            prompt="look around",
            subagent_type="Explore",
            isolation="worktree",
        )

    assert mock_spawn.call_args[1]["isolation_mode"] == "worktree"


@pytest.mark.asyncio
async def test_agent_tool_invalid_isolation_input_errors():
    from d2c.tools.agent_tool import AgentTool

    config = Config.load()
    tool = AgentTool(config=config)
    result = await tool.execute(
        description="x", prompt="y", subagent_type="Explore", isolation="vm"
    )
    assert result.error is True
    assert "invalid isolation" in result.output


@pytest.mark.asyncio
async def test_agent_tool_does_not_mutate_shared_builtin():
    """Per-call overrides must not leak into the shared BUILTIN_SUBAGENTS
    object (dataclasses.replace builds an independent copy)."""
    from d2c.subagent import BUILTIN_SUBAGENTS, SubagentResult
    from d2c.tools.agent_tool import AgentTool

    config = Config.load()
    config.deepseek_api_key = "test-key"
    tool = AgentTool(config=config)

    with patch("d2c.subagent.spawn_subagent") as mock_spawn:
        mock_spawn.return_value = SubagentResult(summary="ok", success=True)
        await tool.execute(
            description="x",
            prompt="y",
            subagent_type="Explore",
            permission_mode_override="dontAsk",
            isolation="worktree",
            max_turns=99,
        )

    builtin = BUILTIN_SUBAGENTS["Explore"]
    assert builtin.permission_mode is None
    assert builtin.isolation == "default"
    assert builtin.max_turns == 25
