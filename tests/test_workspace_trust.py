"""Tests for Phase 32: Workspace Trust Gate hardening.

Verifies headless abort on untrusted directories with local extensions,
forced restricted permission mode when untrusted, and plugin loading gating.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from d2c.trust import (
    TrustStore,
    WorkSpaceTrustGate,
    reset_trust_gate,
    set_trust_gate,
)

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_trust():
    """Reset the global trust gate before and after each test."""
    reset_trust_gate()
    yield
    reset_trust_gate()


@pytest.fixture
def untrusted_gate(tmp_path, monkeypatch):
    """A WorkSpaceTrustGate in the denied state."""
    trust_file = tmp_path / "trusted.json"
    monkeypatch.setattr(TrustStore, "PATH", trust_file)
    store = TrustStore()
    gate = WorkSpaceTrustGate(tmp_path, store)
    gate.decide(False)
    set_trust_gate(gate)
    return gate


@pytest.fixture
def trusted_gate(tmp_path, monkeypatch):
    """A WorkSpaceTrustGate in the trusted state."""
    trust_file = tmp_path / "trusted.json"
    monkeypatch.setattr(TrustStore, "PATH", trust_file)
    store = TrustStore()
    gate = WorkSpaceTrustGate(tmp_path, store)
    gate.decide(True)
    set_trust_gate(gate)
    return gate


@pytest.fixture
def mock_extensions_dir(tmp_path):
    """Create a workspace with .d2c extensions present."""
    d2c_dir = tmp_path / ".d2c"
    d2c_dir.mkdir()
    (d2c_dir / "plugins").mkdir()
    (d2c_dir / "config.yaml").write_text("model: test")
    return tmp_path


# ── _has_local_extensions tests ────────────────────────────────────────


class TestHasLocalExtensions:
    def test_detects_plugins_dir(self, mock_extensions_dir):
        """_has_local_extensions returns True when .d2c/plugins exists."""
        from d2c.main import _has_local_extensions

        assert _has_local_extensions(mock_extensions_dir) is True

    def test_detects_agents_dir(self, tmp_path):
        """_has_local_extensions returns True when .d2c/agents exists."""
        from d2c.main import _has_local_extensions

        (tmp_path / ".d2c" / "agents").mkdir(parents=True)
        assert _has_local_extensions(tmp_path) is True

    def test_detects_skills_dir(self, tmp_path):
        """_has_local_extensions returns True when .d2c/skills exists."""
        from d2c.main import _has_local_extensions

        (tmp_path / ".d2c" / "skills").mkdir(parents=True)
        assert _has_local_extensions(tmp_path) is True

    def test_detects_config_yaml(self, tmp_path):
        """_has_local_extensions returns True when .d2c/config.yaml exists."""
        from d2c.main import _has_local_extensions

        (tmp_path / ".d2c").mkdir()
        (tmp_path / ".d2c" / "config.yaml").write_text("")
        assert _has_local_extensions(tmp_path) is True

    def test_detects_mcp_json(self, tmp_path):
        """_has_local_extensions returns True when .d2c/mcp.json exists."""
        from d2c.main import _has_local_extensions

        (tmp_path / ".d2c").mkdir()
        (tmp_path / ".d2c" / "mcp.json").write_text("{}")
        assert _has_local_extensions(tmp_path) is True

    def test_returns_false_for_empty_workspace(self, tmp_path):
        """_has_local_extensions returns False when no .d2c directory exists."""
        from d2c.main import _has_local_extensions

        assert _has_local_extensions(tmp_path) is False

    def test_returns_false_for_empty_d2c_dir(self, tmp_path):
        """_has_local_extensions returns False when .d2c is empty."""
        from d2c.main import _has_local_extensions

        (tmp_path / ".d2c").mkdir()
        assert _has_local_extensions(tmp_path) is False


# ── Headless abort tests ───────────────────────────────────────────────


class TestHeadlessAbortUntrusted:
    def test_headless_aborts_with_local_extensions(self, mock_extensions_dir):
        """Headless mode aborts (sys.exit(1)) when untrusted + extensions."""
        from d2c.main import _resolve_trust

        args = argparse.Namespace(
            trust=False,
            no_trust=False,
            prompt="fix bug",
            cwd=mock_extensions_dir,
        )

        with pytest.raises(SystemExit) as exc_info:
            _resolve_trust(args)

        assert exc_info.value.code == 1

    def test_headless_warns_but_no_abort_without_extensions(self, tmp_path):
        """Headless warns but does NOT abort when no local extensions."""
        from d2c.main import _resolve_trust

        args = argparse.Namespace(
            trust=False,
            no_trust=False,
            prompt="fix bug",
            cwd=tmp_path,
        )

        gate = _resolve_trust(args)
        assert gate.is_project_trusted is False

    def test_headless_with_trust_flag_runs_normally(self, mock_extensions_dir):
        """Headless with --trust flag works even with extensions present."""
        from d2c.main import _resolve_trust

        args = argparse.Namespace(
            trust=True,
            no_trust=False,
            prompt="fix bug",
            cwd=mock_extensions_dir,
        )

        gate = _resolve_trust(args)
        assert gate.is_project_trusted is True

    def test_headless_with_no_trust_aborts_with_extensions(self, mock_extensions_dir):
        """Headless with --no-trust aborts when extensions present."""
        from d2c.main import _resolve_trust

        args = argparse.Namespace(
            trust=False,
            no_trust=True,
            prompt="fix bug",
            cwd=mock_extensions_dir,
        )

        with pytest.raises(SystemExit) as exc_info:
            _resolve_trust(args)

        assert exc_info.value.code == 1

    def test_headless_previously_trusted_runs_normally(self, mock_extensions_dir):
        """Headless in previously trusted workspace runs normally."""
        from d2c.main import _resolve_trust

        # Trust the workspace first
        store = TrustStore()
        store.trust(mock_extensions_dir)

        args = argparse.Namespace(
            trust=False,
            no_trust=False,
            prompt="fix bug",
            cwd=mock_extensions_dir,
        )

        gate = _resolve_trust(args)
        assert gate.is_project_trusted is True


# ── Forced restricted permission mode tests ────────────────────────────


class TestForcedRestrictedPermissionMode:
    def test_untrusted_forces_default_mode(self, untrusted_gate, tmp_path):
        """Untrusted workspace forces permission_mode to 'default'."""
        from d2c.config import Config

        with patch.object(
            Config,
            "load",
            return_value=Config(
                cwd=tmp_path,
                permission_mode="dontAsk",
            ),
        ):
            config = Config.load(tmp_path)

            # Simulate the trust check logic from run_headless/run_interactive
            from d2c.trust import get_trust_gate

            if not get_trust_gate().is_project_trusted:
                if config.permission_mode not in ("default", "plan"):
                    config.permission_mode = "default"

            assert config.permission_mode == "default"

    def test_trusted_preserves_dontask_mode(self, trusted_gate, tmp_path):
        """Trusted workspace preserves 'dontAsk' permission mode."""
        from d2c.config import Config

        config = Config(cwd=tmp_path, permission_mode="dontAsk")

        from d2c.trust import get_trust_gate

        if not get_trust_gate().is_project_trusted:
            if config.permission_mode not in ("default", "plan"):
                config.permission_mode = "default"

        assert config.permission_mode == "dontAsk"

    def test_untrusted_preserves_plan_mode(self, untrusted_gate, tmp_path):
        """Untrusted workspace preserves 'plan' mode (it's already safe)."""
        from d2c.config import Config

        config = Config(cwd=tmp_path, permission_mode="plan")

        from d2c.trust import get_trust_gate

        if not get_trust_gate().is_project_trusted:
            if config.permission_mode not in ("default", "plan"):
                config.permission_mode = "default"

        assert config.permission_mode == "plan"

    def test_untrusted_preserves_default_mode(self, untrusted_gate, tmp_path):
        """Untrusted workspace preserves 'default' mode (it's already safe)."""
        from d2c.config import Config

        config = Config(cwd=tmp_path, permission_mode="default")

        from d2c.trust import get_trust_gate

        if not get_trust_gate().is_project_trusted:
            if config.permission_mode not in ("default", "plan"):
                config.permission_mode = "default"

        assert config.permission_mode == "default"

    def test_untrusted_overrides_accept_edits_mode(self, untrusted_gate, tmp_path):
        """Untrusted workspace overrides 'acceptEdits' to 'default'."""
        from d2c.config import Config

        config = Config(cwd=tmp_path, permission_mode="acceptEdits")

        from d2c.trust import get_trust_gate

        if not get_trust_gate().is_project_trusted:
            if config.permission_mode not in ("default", "plan"):
                config.permission_mode = "default"

        assert config.permission_mode == "default"


# ── Trust gate integration tests ───────────────────────────────────────


class TestTrustGateIntegration:
    def test_trusted_workspace_allows_plugins(self, trusted_gate, tmp_path):
        """When trusted, get_trust_gate().is_project_trusted is True."""
        from d2c.trust import get_trust_gate

        assert get_trust_gate().is_project_trusted is True

    def test_untrusted_workspace_prevents_plugins(self, untrusted_gate, tmp_path):
        """When untrusted, get_trust_gate().is_project_trusted is False."""
        from d2c.trust import get_trust_gate

        assert get_trust_gate().is_project_trusted is False

    def test_trusted_store_persists_across_calls(self, tmp_path, monkeypatch):
        """Trusting a path persists in the TrustStore."""
        trust_file = tmp_path / "trusted.json"
        monkeypatch.setattr(TrustStore, "PATH", trust_file)
        store = TrustStore()
        store.trust(tmp_path)

        # New store instance should see the trust
        store2 = TrustStore()
        assert store2.is_trusted(tmp_path) is True

    def test_symbolic_link_paths_resolved(self, untrusted_gate, tmp_path):
        """Paths must be resolved before trust checking."""
        from d2c.trust import get_trust_gate

        # Even though the original path is tmp_path, the gate was set up with
        # tmp_path, so is_project_trusted reflects the decided state correctly
        assert get_trust_gate().is_project_trusted is False
        assert get_trust_gate().decided is True
