"""Phase 52: session-scoped persistent approvals."""

import asyncio
import builtins
import json
import threading
import time
from functools import partial

import pytest

import d2c.main as main
from d2c.approvals import ApprovalCache
from d2c.config import Config
from d2c.main import ReplState, SlashCommand, handle_slash_command, make_interactive_approval
from d2c.observability import AuditLogger, set_audit_logger
from d2c.permissions import (
    PermissionCategory,
    PermissionDecision,
    PermissionRequest,
    PermissionResult,
)
from d2c.persistence import SessionManager


@pytest.fixture(autouse=True)
def _reset_logger():
    yield
    set_audit_logger(None)


def _req(cmd="rm -rf x"):
    return PermissionRequest(
        tool_name="Bash", tool_input={"command": cmd}, tool_category=PermissionCategory.SHELL
    )


_ASK = PermissionResult(PermissionDecision.ASK, reason="uncertain")


# ── Cache primitives ──────────────────────────────────────────────────


def test_approve_stores_exact_action():
    c = ApprovalCache()
    assert not c.is_approved(_req())
    c.approve(_req())
    assert c.is_approved(_req())  # same exact action
    assert not c.is_approved(_req("rm -rf y"))  # different command


def test_cache_stores_hashes_not_plaintext():
    c = ApprovalCache()
    c.approve(_req("curl http://x?token=supersecretvalue123"))
    assert "supersecretvalue123" not in str(c._keys)
    assert all(len(k) == 64 for k in c._keys)  # sha256 hex
    assert not hasattr(c, "save") and not hasattr(c, "to_dict")  # no persistence api


# ── Interactive callback with [y/N/a] ─────────────────────────────────


@pytest.mark.asyncio
async def test_always_then_no_prompt_on_repeat(monkeypatch):
    cache = ApprovalCache()
    cb = make_interactive_approval(cache)

    monkeypatch.setattr(builtins, "input", lambda *a: "a")  # user picks "always"
    assert await cb(_req(), _ASK) is True
    assert cache.is_approved(_req())

    # Second identical action: input must NOT be called (cache hit).
    def _boom(*a):
        raise AssertionError("should not prompt on cache hit")

    monkeypatch.setattr(builtins, "input", _boom)
    assert await cb(_req(), _ASK) is True


@pytest.mark.asyncio
async def test_different_action_still_prompts(monkeypatch):
    cache = ApprovalCache()
    cb = make_interactive_approval(cache)
    monkeypatch.setattr(builtins, "input", lambda *a: "a")
    await cb(_req("cmd-A"), _ASK)  # approve A

    monkeypatch.setattr(builtins, "input", lambda *a: "n")  # B is a new prompt → deny
    assert await cb(_req("cmd-B"), _ASK) is False


@pytest.mark.asyncio
async def test_y_once_does_not_cache(monkeypatch):
    cache = ApprovalCache()
    cb = make_interactive_approval(cache)
    monkeypatch.setattr(builtins, "input", lambda *a: "y")
    assert await cb(_req(), _ASK) is True
    assert not cache.is_approved(_req())  # "y" is one-shot, not cached


# ── Cached approval audit event ───────────────────────────────────────


@pytest.mark.asyncio
async def test_cached_approval_logs_event(tmp_dir, monkeypatch):
    cache = ApprovalCache()
    cache.approve(_req())  # pre-approved
    path = tmp_dir / "audit.jsonl"
    set_audit_logger(AuditLogger(path=path, enabled=True))

    cb = make_interactive_approval(cache)
    monkeypatch.setattr(builtins, "input", lambda *a: (_ for _ in ()).throw(AssertionError()))
    assert await cb(_req(), _ASK) is True

    events = {json.loads(x)["event"] for x in path.read_text().splitlines() if x.strip()}
    assert "permission_approved_cached" in events


# ── Session switch clears the cache ───────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd,args", [("/clear", []), ("/resume", ["SID"]), ("/fork", ["SID"])])
async def test_session_switch_clears_approvals(tmp_dir, monkeypatch, cmd, args):
    monkeypatch.setattr(main, "SessionManager", partial(SessionManager, base_dir=tmp_dir))
    mgr = SessionManager(base_dir=tmp_dir)
    src = mgr.create_session(tmp_dir)
    real_args = [src.session_id] if args else []

    state = ReplState(config=Config(cwd=tmp_dir), session_store=src, conversation=[])
    state.approvals.approve(_req())
    assert len(state.approvals) == 1

    await handle_slash_command(SlashCommand(name=cmd, args=real_args), state)
    assert len(state.approvals) == 0  # cleared on session switch


def test_new_replstate_has_empty_cache(tmp_dir):
    # Process restart == a fresh ReplState → empty cache (nothing persisted).
    s1 = ReplState(config=Config(cwd=tmp_dir), session_store=None)
    s1.approvals.approve(_req())
    s2 = ReplState(config=Config(cwd=tmp_dir), session_store=None)
    assert len(s2.approvals) == 0
    assert s1.approvals is not s2.approvals


# ── Phase 59 fix: concurrent approval prompts must not interleave ──────


@pytest.mark.asyncio
async def test_concurrent_prompts_are_serialized_not_interleaved(monkeypatch):
    """Two tools needing approval in the same turn (e.g. concurrent-safe
    reads) must not have their prompts/input() calls race on stdin — the
    prompt lock in make_interactive_approval() must serialize them."""
    cache = ApprovalCache()
    cb = make_interactive_approval(cache)

    lock = threading.Lock()
    concurrent = 0
    max_concurrent = 0

    def _input(prompt=""):
        nonlocal concurrent, max_concurrent
        with lock:
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
        time.sleep(0.05)  # widen the race window so a real bug would show
        with lock:
            concurrent -= 1
        return "y"

    monkeypatch.setattr(builtins, "input", _input)

    results = await asyncio.gather(
        cb(_req("cmd-A"), _ASK),
        cb(_req("cmd-B"), _ASK),
    )
    assert results == [True, True]
    assert max_concurrent == 1  # never two input() calls in flight at once


@pytest.mark.asyncio
async def test_concurrent_identical_action_only_prompts_once(monkeypatch):
    """If two concurrent tool calls need approval for the EXACT same action
    and the user picks "always" on the first, the second must resolve from
    cache after acquiring the lock — not re-prompt."""
    cache = ApprovalCache()
    cb = make_interactive_approval(cache)

    prompt_count = 0

    def _input(prompt=""):
        nonlocal prompt_count
        prompt_count += 1
        time.sleep(0.05)
        return "a"  # always allow

    monkeypatch.setattr(builtins, "input", _input)

    results = await asyncio.gather(
        cb(_req("same-cmd"), _ASK),
        cb(_req("same-cmd"), _ASK),
    )
    assert results == [True, True]
    assert prompt_count == 1  # second call resolved from cache, not a prompt
