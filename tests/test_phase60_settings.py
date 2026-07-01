"""Phase 60: layered settings (managed > user > project > local > env/defaults)."""

from __future__ import annotations

import pytest
import yaml

import d2c.settings as settings_mod
from d2c.settings import (
    OverrideAttempt,
    SettingsFile,
    SettingsLoadError,
    SettingsScope,
    discover_settings_files,
    load_settings,
    merge_settings,
)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))


@pytest.fixture(autouse=True)
def _isolate_locations(tmp_dir, monkeypatch):
    """Point every scope's file location at tmp_dir so tests never touch
    real /etc/d2c or ~/.d2c, and never race each other."""
    monkeypatch.setattr(settings_mod, "managed_settings_path", lambda: tmp_dir / "managed.yaml")
    monkeypatch.setattr(settings_mod, "user_settings_path", lambda: tmp_dir / "user.yaml")
    return tmp_dir


# ── Precedence order (scalars) ──────────────────────────────────────────


def test_precedence_managed_wins_over_all(tmp_dir):
    _write(tmp_dir / "managed.yaml", {"permission_mode": "plan"})
    _write(tmp_dir / "user.yaml", {"permission_mode": "bypass"})
    _write(tmp_dir / ".d2c" / "settings.yaml", {"permission_mode": "dontAsk"})
    _write(tmp_dir / ".d2c" / "settings.local.yaml", {"permission_mode": "acceptEdits"})

    merged = load_settings(tmp_dir, trusted=True)
    assert merged.permission_mode == "plan"
    assert merged.sources["permission_mode"] == SettingsScope.MANAGED
    assert not merged.errors


def test_precedence_user_wins_when_managed_silent(tmp_dir):
    _write(tmp_dir / "user.yaml", {"permission_mode": "bypass"})
    _write(tmp_dir / ".d2c" / "settings.yaml", {"permission_mode": "dontAsk"})

    merged = load_settings(tmp_dir, trusted=True)
    assert merged.permission_mode == "bypass"
    assert merged.sources["permission_mode"] == SettingsScope.USER


def test_precedence_project_wins_when_managed_and_user_silent(tmp_dir):
    _write(tmp_dir / ".d2c" / "settings.yaml", {"permission_mode": "dontAsk"})
    _write(tmp_dir / ".d2c" / "settings.local.yaml", {"permission_mode": "acceptEdits"})

    merged = load_settings(tmp_dir, trusted=True)
    assert merged.permission_mode == "dontAsk"
    assert merged.sources["permission_mode"] == SettingsScope.PROJECT


def test_no_settings_files_means_unset(tmp_dir):
    merged = load_settings(tmp_dir, trusted=True)
    assert merged.permission_mode is None
    assert merged.sandbox_enabled is None
    assert merged.permission_rules == []
    assert merged.hooks == []
    assert merged.loaded_files == []


# ── Managed lock cannot be overridden ───────────────────────────────────


def test_managed_lock_records_blocked_override_attempts(tmp_dir):
    _write(tmp_dir / "managed.yaml", {"permission_mode": "plan", "sandbox_enabled": True})
    _write(tmp_dir / "user.yaml", {"permission_mode": "bypass"})
    _write(tmp_dir / ".d2c" / "settings.yaml", {"sandbox_enabled": False})

    merged = load_settings(tmp_dir, trusted=True)
    assert merged.permission_mode == "plan"  # managed wins
    assert merged.sandbox_enabled is True  # managed wins
    assert len(merged.overridden_attempts) == 2
    fields_blocked = {a.field for a in merged.overridden_attempts}
    assert fields_blocked == {"permission_mode", "sandbox_enabled"}
    for a in merged.overridden_attempts:
        assert a.locked_by == SettingsScope.MANAGED
    # Human-readable, so it can be surfaced in Config.validate()/doctor.
    assert "locked by managed" in str(merged.overridden_attempts[0])


def test_managed_lock_survives_into_config_validate(tmp_dir, monkeypatch, trusted_gate):
    from d2c.config import Config

    _write(tmp_dir / "managed.yaml", {"permission_mode": "plan"})
    _write(tmp_dir / "user.yaml", {"permission_mode": "bypass"})
    monkeypatch.setattr(settings_mod, "managed_settings_path", lambda: tmp_dir / "managed.yaml")
    monkeypatch.setattr(settings_mod, "user_settings_path", lambda: tmp_dir / "user.yaml")

    config = Config.load(cwd=tmp_dir)
    assert config.permission_mode == "plan"
    assert any("locked by managed" in w for w in config.settings_warnings)
    assert any("locked by managed" in i for i in config.validate())


# ── Trust-aware loading ──────────────────────────────────────────────────


def test_untrusted_workspace_skips_project_and_local(tmp_dir):
    _write(tmp_dir / ".d2c" / "settings.yaml", {"permission_mode": "dontAsk"})
    _write(tmp_dir / ".d2c" / "settings.local.yaml", {"permission_mode": "acceptEdits"})

    merged = load_settings(tmp_dir, trusted=False)
    assert merged.permission_mode is None
    assert merged.loaded_files == []


def test_untrusted_workspace_still_loads_managed_and_user(tmp_dir):
    _write(tmp_dir / "managed.yaml", {"permission_mode": "plan"})
    _write(tmp_dir / "user.yaml", {"sandbox_enabled": True})

    merged = load_settings(tmp_dir, trusted=False)
    assert merged.permission_mode == "plan"
    assert merged.sandbox_enabled is True
    scopes = {f.scope for f in merged.loaded_files}
    assert scopes == {SettingsScope.MANAGED, SettingsScope.USER}


def test_trusted_workspace_loads_all_four_scopes(tmp_dir):
    _write(tmp_dir / "managed.yaml", {})
    _write(tmp_dir / "user.yaml", {})
    _write(tmp_dir / ".d2c" / "settings.yaml", {})
    _write(tmp_dir / ".d2c" / "settings.local.yaml", {})

    files, errors = discover_settings_files(tmp_dir, trusted=True)
    assert not errors
    assert {f.scope for f in files} == {
        SettingsScope.MANAGED,
        SettingsScope.USER,
        SettingsScope.PROJECT,
        SettingsScope.LOCAL,
    }


# ── Deny rules dominate allow rules (list-field union + engine semantics) ─


def test_permission_rules_union_across_scopes(tmp_dir):
    _write(
        tmp_dir / "managed.yaml",
        {"permission_rules": [{"type": "deny", "pattern": "Bash", "reason": "no shell"}]},
    )
    _write(
        tmp_dir / ".d2c" / "settings.yaml",
        {"permission_rules": [{"type": "allow", "pattern": "Read"}]},
    )
    merged = load_settings(tmp_dir, trusted=True)
    patterns = {(r["type"], r["pattern"]) for r in merged.permission_rules}
    assert patterns == {("deny", "Bash"), ("allow", "Read")}


def test_managed_deny_rule_beats_lower_scope_allow_rule_in_engine(tmp_dir):
    """A managed deny rule for a tool must win even if a lower scope tries
    to allow the exact same tool — PermissionEngine checks all deny rules
    before any allow rule, regardless of which scope contributed them."""
    from d2c.permissions import (
        PermissionCategory,
        PermissionDecision,
        PermissionEngine,
        PermissionRequest,
    )

    _write(
        tmp_dir / "managed.yaml",
        {"permission_rules": [{"type": "deny", "pattern": "Bash", "reason": "managed lockdown"}]},
    )
    _write(
        tmp_dir / ".d2c" / "settings.yaml",
        {"permission_rules": [{"type": "allow", "pattern": "Bash"}]},
    )
    merged = load_settings(tmp_dir, trusted=True)

    from d2c.config import Config

    config = Config(cwd=tmp_dir, permission_rules=merged.permission_rules)
    engine = PermissionEngine.from_config(config)
    request = PermissionRequest(
        tool_name="Bash", tool_input={"command": "ls"}, tool_category=PermissionCategory.SHELL
    )
    result = engine.evaluate(request)
    assert result.decision == PermissionDecision.DENY


# ── Malformed settings error reporting ──────────────────────────────────


def test_malformed_yaml_reported_not_raised(tmp_dir):
    (tmp_dir / "managed.yaml").parent.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "managed.yaml").write_text("not: valid: yaml: [")

    merged = load_settings(tmp_dir, trusted=True)
    assert len(merged.errors) == 1
    assert merged.errors[0].scope == SettingsScope.MANAGED
    assert "invalid YAML" in merged.errors[0].message


def test_non_mapping_yaml_reported(tmp_dir):
    _write(tmp_dir / "managed.yaml", ["not", "a", "mapping"])
    merged = load_settings(tmp_dir, trusted=True)
    assert len(merged.errors) == 1
    assert "mapping" in merged.errors[0].message


def test_invalid_permission_mode_reported_and_not_applied(tmp_dir):
    _write(tmp_dir / "managed.yaml", {"permission_mode": "sudo-mode"})
    merged = load_settings(tmp_dir, trusted=True)
    assert merged.permission_mode is None
    assert any("invalid permission_mode" in str(e) for e in merged.errors)


def test_invalid_sandbox_enabled_type_reported(tmp_dir):
    _write(tmp_dir / "managed.yaml", {"sandbox_enabled": "yes-please"})
    merged = load_settings(tmp_dir, trusted=True)
    assert merged.sandbox_enabled is None
    assert any("sandbox_enabled" in str(e) for e in merged.errors)


def test_malformed_permission_rule_entry_skipped_not_crashing(tmp_dir):
    _write(
        tmp_dir / "managed.yaml",
        {
            "permission_rules": [
                {"type": "deny", "pattern": "Bash"},  # valid
                {"type": "nuke", "pattern": "Bash"},  # invalid type
                {"pattern": ""},  # empty pattern
                "not-even-a-dict",  # wrong shape entirely
            ]
        },
    )
    merged = load_settings(tmp_dir, trusted=True)
    assert merged.permission_rules == [{"type": "deny", "pattern": "Bash"}]
    assert len(merged.errors) == 3


def test_malformed_hook_entry_skipped_not_crashing(tmp_dir):
    _write(
        tmp_dir / "managed.yaml",
        {
            "hooks": [
                {"event": "SessionStart", "type": "command", "command": "echo hi"},  # valid
                {"event": "NotARealEvent"},  # invalid event
                {"event": "Stop", "type": "callback"},  # callback not settable via YAML
                {"missing": "event key"},
            ]
        },
    )
    merged = load_settings(tmp_dir, trusted=True)
    assert len(merged.hooks) == 1
    assert merged.hooks[0]["event"] == "SessionStart"
    assert len(merged.errors) == 3


def test_permission_rules_not_a_list_reported(tmp_dir):
    _write(tmp_dir / "managed.yaml", {"permission_rules": "not-a-list"})
    merged = load_settings(tmp_dir, trusted=True)
    assert merged.permission_rules == []
    assert any("must be a list" in str(e) for e in merged.errors)


def test_unreadable_file_does_not_raise(tmp_dir):
    # A directory where a file is expected — read fails distinctly from a
    # missing file, and must still be reported cleanly.
    (tmp_dir / "managed.yaml").mkdir(parents=True)
    merged = load_settings(tmp_dir, trusted=True)
    assert len(merged.errors) == 1


# ── merge_settings() unit-level behavior ─────────────────────────────────


def test_merge_settings_last_file_wins_per_scope_on_duplicate():
    a = SettingsFile(path=None, scope=SettingsScope.MANAGED, data={"permission_mode": "plan"})
    b = SettingsFile(path=None, scope=SettingsScope.MANAGED, data={"permission_mode": "bypass"})
    merged = merge_settings([a, b])
    assert merged.permission_mode == "bypass"


def test_settings_load_error_and_override_attempt_str_are_readable():
    err = SettingsLoadError(path="x.yaml", scope=SettingsScope.PROJECT, message="bad")
    assert "project" in str(err) and "bad" in str(err)
    attempt = OverrideAttempt(
        field="permission_mode",
        locked_by=SettingsScope.MANAGED,
        attempted_scope=SettingsScope.USER,
        attempted_value="bypass",
    )
    assert "permission_mode" in str(attempt)
    assert "managed" in str(attempt)
    assert "user" in str(attempt)


# ── Doctor diagnostics ────────────────────────────────────────────────


def test_doctor_reports_settings_pass_when_clean(tmp_dir):
    from d2c.doctor import check_settings

    _write(tmp_dir / "managed.yaml", {"permission_mode": "plan"})
    result = check_settings(tmp_dir, trusted=True)
    assert result.status == "pass"
    assert "managed" in result.message


def test_doctor_reports_settings_fail_on_malformed_file(tmp_dir):
    from d2c.doctor import check_settings

    (tmp_dir / "managed.yaml").write_text("not: valid: yaml: [")
    result = check_settings(tmp_dir, trusted=True)
    assert result.status == "fail"
    assert "malformed" in result.message.lower()


def test_doctor_reports_settings_warn_on_blocked_override(tmp_dir):
    from d2c.doctor import check_settings

    _write(tmp_dir / "managed.yaml", {"permission_mode": "plan"})
    _write(tmp_dir / "user.yaml", {"permission_mode": "bypass"})
    result = check_settings(tmp_dir, trusted=True)
    assert result.status == "warn"
    assert "blocked" in result.message.lower()


def test_doctor_reports_no_settings_files_found(tmp_dir):
    from d2c.doctor import check_settings

    result = check_settings(tmp_dir, trusted=True)
    assert result.status == "pass"
    assert "no settings files found" in result.message


def test_doctor_settings_check_included_in_run_doctor(tmp_dir, trusted_gate):
    from d2c.config import Config
    from d2c.doctor import run_doctor

    config = Config.load(cwd=tmp_dir)
    results = run_doctor(config, cwd=tmp_dir, trusted=True)
    assert any(r.name == "Settings" for r in results)
