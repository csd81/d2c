"""Phase 70: approval-management UX (/approvals + ApprovalCache introspection).

Covers cache-state reporting, session-vs-persistent clearing, and reset — all
against a temporary approvals path so the real ~/.d2c/approvals.json is never
touched. Verifies output exposes counts/path only, never hashes or tool input.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from d2c.approvals import ApprovalCache
from d2c.config import Config
from d2c.main import ReplState, SlashCommand, handle_slash_command


def _req(command: str) -> SimpleNamespace:
    """A minimal permission-request stand-in (ApprovalCache reads these attrs)."""
    return SimpleNamespace(
        tool_name="Bash",
        tool_category="SHELL",
        tool_input={"command": command},
    )


def _state(tmp_dir, cache: ApprovalCache) -> ReplState:
    return ReplState(
        config=Config(cwd=tmp_dir),
        session_store=None,
        conversation=[],
        approvals=cache,
    )


# ── ApprovalCache introspection ─────────────────────────────────────


def test_counts_split_session_and_persistent(tmp_dir):
    cache = ApprovalCache(path=tmp_dir / "approvals.json")
    cache.approve(_req("git status"), persist=True)  # persistent -> disk + memory
    cache.approve(_req("ls -la"), persist=False)  # session-only -> memory

    assert cache.persistent_count() == 1
    assert cache.session_count() == 1
    assert cache.runtime_count() == 2
    assert cache.path() == tmp_dir / "approvals.json"


def test_clear_session_keeps_persistent(tmp_dir):
    p = tmp_dir / "approvals.json"
    cache = ApprovalCache(path=p)
    persistent = _req("git status")
    session = _req("ls -la")
    cache.approve(persistent, persist=True)
    cache.approve(session, persist=False)

    removed = cache.clear_session()

    assert removed == 1
    assert cache.session_count() == 0
    assert cache.persistent_count() == 1
    assert cache.is_approved(persistent) is True  # still active this session
    assert cache.is_approved(session) is False
    assert p.exists()  # disk untouched


def test_reset_clears_runtime_and_disk(tmp_dir):
    p = tmp_dir / "approvals.json"
    cache = ApprovalCache(path=p)
    cache.approve(_req("git status"), persist=True)
    assert p.exists()

    cache.reset()

    assert cache.runtime_count() == 0
    assert cache.persistent_count() == 0
    assert not p.exists()


def test_in_memory_only_cache_counts(tmp_dir):
    cache = ApprovalCache()  # no path
    cache.approve(_req("ls"), persist=False)
    assert cache.path() is None
    assert cache.persistent_count() == 0
    assert cache.session_count() == 1
    assert cache.clear_session() == 1
    assert cache.session_count() == 0


# ── /approvals dispatch ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approvals_status_reports_counts_and_path(tmp_dir, capsys):
    cache = ApprovalCache(path=tmp_dir / "approvals.json")
    cache.approve(_req("git status"), persist=True)
    cache.approve(_req("ls -la"), persist=False)
    state = _state(tmp_dir, cache)

    cont = await handle_slash_command(SlashCommand(name="/approvals"), state)
    out = capsys.readouterr().out

    assert cont is True
    assert "session approvals:    1" in out
    assert "persistent approvals: 1" in out
    assert "approvals.json" in out


@pytest.mark.asyncio
async def test_approvals_status_leaks_no_plaintext_or_hashes(tmp_dir, capsys):
    cache = ApprovalCache(path=tmp_dir / "approvals.json")
    cache.approve(_req("rm -rf /secret/path"), persist=True)
    state = _state(tmp_dir, cache)

    await handle_slash_command(SlashCommand(name="/approvals"), state)
    out = capsys.readouterr().out

    assert "rm -rf" not in out  # no original command
    assert "/secret/path" not in out
    # no 64-char sha256 hash on any line
    assert not any(
        len(tok) == 64 and all(c in "0123456789abcdef" for c in tok) for tok in out.split()
    )


@pytest.mark.asyncio
async def test_approvals_clear_session_reports_and_keeps_persistent(tmp_dir, capsys):
    p = tmp_dir / "approvals.json"
    cache = ApprovalCache(path=p)
    cache.approve(_req("git status"), persist=True)
    cache.approve(_req("ls -la"), persist=False)
    state = _state(tmp_dir, cache)

    await handle_slash_command(SlashCommand(name="/approvals", args=["clear-session"]), state)
    out = capsys.readouterr().out

    assert "Cleared 1 session approval(s)." in out
    assert cache.session_count() == 0
    assert cache.persistent_count() == 1
    assert p.exists()


@pytest.mark.asyncio
async def test_approvals_reset_clears_disk(tmp_dir, capsys):
    p = tmp_dir / "approvals.json"
    cache = ApprovalCache(path=p)
    cache.approve(_req("git status"), persist=True)
    state = _state(tmp_dir, cache)

    await handle_slash_command(SlashCommand(name="/approvals", args=["reset"]), state)
    out = capsys.readouterr().out

    assert "Reset persistent approval cache" in out
    assert not p.exists()
    assert cache.persistent_count() == 0


@pytest.mark.asyncio
async def test_approvals_unknown_subcommand_is_nonmutating(tmp_dir, capsys):
    p = tmp_dir / "approvals.json"
    cache = ApprovalCache(path=p)
    cache.approve(_req("git status"), persist=True)
    cache.approve(_req("ls -la"), persist=False)
    state = _state(tmp_dir, cache)

    cont = await handle_slash_command(SlashCommand(name="/approvals", args=["nuke"]), state)
    out = capsys.readouterr().out

    assert cont is True
    assert "Usage: /approvals" in out
    # state unchanged
    assert cache.session_count() == 1
    assert cache.persistent_count() == 1
    assert p.exists()
