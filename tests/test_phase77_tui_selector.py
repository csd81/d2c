"""Phase 77: --tui selector, resolution precedence, and the non-interactive
boundary. No live UI is started — only the pure resolution/decision logic and
the CLI parser are exercised.
"""

from __future__ import annotations

import inspect

import pytest

import d2c.main as main
from d2c.tui import resolve_ui, ui_decision

# ── resolve_ui precedence: CLI > D2C_TUI > default(classic) ──────────


def test_resolve_ui_cli_wins(monkeypatch):
    monkeypatch.delenv("D2C_TUI", raising=False)
    assert resolve_ui("classic") == "classic"
    assert resolve_ui("textual") == "textual"


def test_resolve_ui_cli_overrides_env(monkeypatch):
    monkeypatch.setenv("D2C_TUI", "textual")
    assert resolve_ui("classic") == "classic"  # CLI beats env
    monkeypatch.setenv("D2C_TUI", "classic")
    assert resolve_ui("textual") == "textual"


def test_resolve_ui_auto_honors_env_then_default(monkeypatch):
    # Phase 79: the project default is now "textual".
    monkeypatch.delenv("D2C_TUI", raising=False)
    assert resolve_ui("auto") == "textual"  # project default
    assert resolve_ui(None) == "textual"
    monkeypatch.setenv("D2C_TUI", "classic")
    assert resolve_ui("auto") == "classic"  # env can still force classic
    monkeypatch.setenv("D2C_TUI", "textual")
    assert resolve_ui("auto") == "textual"
    monkeypatch.setenv("D2C_TUI", "garbage")
    assert resolve_ui("auto") == "textual"  # unrecognized env → default (textual)


# ── ui_decision: launch vs fallback ─────────────────────────────────


def test_ui_decision_textual_available(monkeypatch):
    monkeypatch.delenv("D2C_TUI", raising=False)
    assert ui_decision("textual", available=True) == "textual"


def test_ui_decision_textual_unavailable_falls_back(monkeypatch):
    monkeypatch.delenv("D2C_TUI", raising=False)
    assert ui_decision("textual", available=False) == "classic-fallback"
    # Phase 79: default (auto) is textual, so it also falls back when unavailable.
    assert ui_decision("auto", available=False) == "classic-fallback"
    assert ui_decision(None, available=False) == "classic-fallback"
    monkeypatch.setenv("D2C_TUI", "textual")
    assert ui_decision("auto", available=False) == "classic-fallback"


def test_ui_decision_auto_is_textual_by_default(monkeypatch):
    # Phase 79: default flipped to textual.
    monkeypatch.delenv("D2C_TUI", raising=False)
    assert ui_decision("auto", available=True) == "textual"
    assert ui_decision(None, available=True) == "textual"


def test_ui_decision_classic(monkeypatch):
    monkeypatch.delenv("D2C_TUI", raising=False)
    assert ui_decision("classic", available=True) == "classic"
    # --tui classic and D2C_TUI=classic still force classic over the new default.
    assert ui_decision("classic", available=True) == "classic"
    monkeypatch.setenv("D2C_TUI", "classic")
    assert ui_decision("auto", available=True) == "classic"


# ── CLI parser ──────────────────────────────────────────────────────


def _parse(argv, monkeypatch):
    monkeypatch.setattr("sys.argv", ["d2c", *argv])
    return main.parse_args()


def test_tui_flag_parses(monkeypatch):
    assert _parse(["--tui", "classic"], monkeypatch).tui == "classic"
    assert _parse(["--tui", "textual"], monkeypatch).tui == "textual"
    assert _parse([], monkeypatch).tui == "auto"  # default


def test_invalid_tui_value_errors(monkeypatch):
    monkeypatch.setattr("sys.argv", ["d2c", "--tui", "bogus"])
    with pytest.raises(SystemExit):
        main.parse_args()


# ── non-interactive boundary ────────────────────────────────────────


def test_headless_has_no_textual_code_path():
    # Headless must never select/launch Textual, even with D2C_TUI=textual: the
    # selector lives only in run_interactive. This guards that structurally.
    src = inspect.getsource(main.run_headless)
    assert "textual" not in src.lower()
    assert "run_textual_app" not in src
    assert "ui_decision" not in src
    assert "resolve_ui" not in src
