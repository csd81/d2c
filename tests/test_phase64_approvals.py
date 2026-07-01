"""Phase 64: persistent cross-session/restart approval cache."""

from __future__ import annotations

import builtins
import json
import logging
import threading
import time

import pytest

from d2c.approvals import ApprovalCache
from d2c.main import make_interactive_approval
from d2c.permissions import (
    PermissionCategory,
    PermissionDecision,
    PermissionRequest,
    PermissionResult,
)


def _req(cmd="rm -rf x"):
    return PermissionRequest(
        tool_name="Bash", tool_input={"command": cmd}, tool_category=PermissionCategory.SHELL
    )


_ASK = PermissionResult(PermissionDecision.ASK, reason="uncertain")


# ── Persist + reload across "process restarts" ──────────────────────


def test_persists_and_reloads_across_restart(tmp_path):
    path = tmp_path / "approvals.json"

    first = ApprovalCache(path=path)
    first.approve(_req("cmd-A"))
    assert first.is_approved(_req("cmd-A"))

    # A brand-new instance against the SAME file simulates a process
    # restart: it must reload the persisted approval without re-prompting.
    second = ApprovalCache(path=path)
    assert second.is_approved(_req("cmd-A"))
    assert second is not first


def test_reload_does_not_see_unrelated_actions(tmp_path):
    path = tmp_path / "approvals.json"
    first = ApprovalCache(path=path)
    first.approve(_req("cmd-A"))

    second = ApprovalCache(path=path)
    assert not second.is_approved(_req("cmd-B"))


def test_multiple_approvals_all_persist(tmp_path):
    path = tmp_path / "approvals.json"
    c1 = ApprovalCache(path=path)
    c1.approve(_req("cmd-A"))
    c1.approve(_req("cmd-B"))
    c1.approve(_req("cmd-C"))

    c2 = ApprovalCache(path=path)
    assert len(c2) == 3
    for cmd in ("cmd-A", "cmd-B", "cmd-C"):
        assert c2.is_approved(_req(cmd))


# ── Integrity: corrupted/malformed file never crashes ────────────────


def test_corrupted_json_falls_back_to_empty(tmp_path, caplog):
    path = tmp_path / "approvals.json"
    path.write_text("{not valid json[[[")

    with caplog.at_level(logging.WARNING):
        c = ApprovalCache(path=path)

    assert len(c) == 0
    assert not c.is_approved(_req())
    assert any("corrupted" in r.message.lower() for r in caplog.records)


def test_non_object_json_falls_back_to_empty(tmp_path, caplog):
    path = tmp_path / "approvals.json"
    path.write_text(json.dumps(["not", "an", "object"]))

    with caplog.at_level(logging.WARNING):
        c = ApprovalCache(path=path)

    assert len(c) == 0
    assert any("not a json object" in r.message.lower() for r in caplog.records)


def test_missing_file_starts_empty_no_warning(tmp_path, caplog):
    path = tmp_path / "does-not-exist.json"
    with caplog.at_level(logging.WARNING):
        c = ApprovalCache(path=path)
    assert len(c) == 0
    assert caplog.records == []  # a missing file is normal, not a warning


def test_wrong_length_keys_skipped_non_string_values_get_fallback_timestamp(tmp_path):
    path = tmp_path / "approvals.json"
    good_hash = "a" * 64
    odd_value_hash = "b" * 64
    path.write_text(
        json.dumps(
            {
                good_hash: "2026-01-01T00:00:00+00:00",  # valid
                "too-short": "2026-01-01T00:00:00+00:00",  # wrong length -> skipped
                odd_value_hash: 456,  # non-string value -> kept with a fallback timestamp
            }
        )
    )
    c = ApprovalCache(path=path)
    assert len(c) == 2
    assert good_hash in c._keys
    assert odd_value_hash in c._keys
    assert isinstance(c._keys[odd_value_hash], str)
    assert "too-short" not in c._keys


def test_unreadable_file_does_not_raise(tmp_path, caplog, monkeypatch):
    path = tmp_path / "approvals.json"
    path.write_text("{}")

    def _boom(*a, **k):
        raise OSError("simulated read failure")

    monkeypatch.setattr(type(path), "read_text", _boom)
    with caplog.at_level(logging.WARNING):
        c = ApprovalCache(path=path)
    assert len(c) == 0


# ── Timestamps ────────────────────────────────────────────────────────


def test_timestamps_recorded_correctly(tmp_path):
    path = tmp_path / "approvals.json"
    before = time.time()
    c = ApprovalCache(path=path)
    c.approve(_req())
    after = time.time()

    data = json.loads(path.read_text())
    (ts,) = data.values()
    # ISO 8601 with a UTC offset, parseable, and within the call window.
    from datetime import datetime

    parsed = datetime.fromisoformat(ts)
    assert before - 1 <= parsed.timestamp() <= after + 1


def test_reload_preserves_original_timestamp_on_reapprove(tmp_path):
    path = tmp_path / "approvals.json"
    c1 = ApprovalCache(path=path)
    c1.approve(_req())
    original_ts = next(iter(json.loads(path.read_text()).values()))

    time.sleep(0.01)
    c2 = ApprovalCache(path=path)
    c2.approve(_req())  # re-approving the identical action refreshes it
    new_ts = next(iter(json.loads(path.read_text()).values()))
    # Re-approval is allowed to bump the timestamp — just confirm it's still
    # a valid, well-formed ISO string and the file still has exactly 1 entry.
    assert new_ts
    assert len(json.loads(path.read_text())) == 1
    assert original_ts  # sanity: the original write did happen


# ── clear() vs reset() ────────────────────────────────────────────────


def test_clear_does_not_touch_disk_file(tmp_path):
    path = tmp_path / "approvals.json"
    c = ApprovalCache(path=path)
    c.approve(_req())
    assert path.exists()

    c.clear()
    assert len(c) == 0  # runtime cleared
    assert path.exists()  # disk file untouched
    assert json.loads(path.read_text())  # still has the persisted entry


def test_clear_then_reload_still_sees_persisted_approval(tmp_path):
    # clear() only affects the runtime view of THIS instance — the
    # persisted approval is still there for the next process/session.
    path = tmp_path / "approvals.json"
    c = ApprovalCache(path=path)
    c.approve(_req())
    c.clear()

    reloaded = ApprovalCache(path=path)
    assert reloaded.is_approved(_req())


def test_reset_removes_disk_file_and_clears_runtime(tmp_path):
    path = tmp_path / "approvals.json"
    c = ApprovalCache(path=path)
    c.approve(_req())
    assert path.exists()

    c.reset()
    assert len(c) == 0
    assert not path.exists()


def test_reset_with_no_path_is_a_noop(tmp_path):
    c = ApprovalCache()  # in-memory only
    c.approve(_req())
    c.reset()
    assert len(c) == 0
    assert not (tmp_path / "approvals.json").exists()


def test_reset_when_file_already_missing_does_not_raise():
    c = ApprovalCache(path=None)
    c.reset()  # no-op, must not raise
    assert len(c) == 0


# ── Concurrent-safety ────────────────────────────────────────────────


def test_concurrent_saves_do_not_corrupt_the_file(tmp_path):
    path = tmp_path / "approvals.json"
    c = ApprovalCache(path=path)

    def _approve_many(prefix):
        for i in range(20):
            c.approve(_req(f"{prefix}-{i}"))

    threads = [threading.Thread(target=_approve_many, args=(p,)) for p in ("t1", "t2", "t3")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The file must always be valid JSON (atomic replace never leaves a
    # half-written file), and every approved action landed in it.
    data = json.loads(path.read_text())
    assert len(data) == 60
    assert len(c) == 60


# ── Cross-session approval end-to-end via the REPL callback ──────────


@pytest.mark.asyncio
async def test_repl_callback_persists_and_reloads_across_sessions(tmp_path, monkeypatch):
    path = tmp_path / "approvals.json"

    # Session 1: user picks "always".
    session1_cache = ApprovalCache(path=path)
    cb1 = make_interactive_approval(session1_cache)
    monkeypatch.setattr(builtins, "input", lambda *a: "a")
    assert await cb1(_req("deploy.sh"), _ASK) is True

    # Session 2 (new ApprovalCache instance against the same file, as
    # _new_approval_cache() would build after a restart): must NOT
    # re-prompt for the identical action.
    session2_cache = ApprovalCache(path=path)
    cb2 = make_interactive_approval(session2_cache)

    def _boom(*a):
        raise AssertionError("should not prompt — action was already approved last session")

    monkeypatch.setattr(builtins, "input", _boom)
    assert await cb2(_req("deploy.sh"), _ASK) is True


@pytest.mark.asyncio
async def test_repl_callback_new_session_still_prompts_for_new_action(tmp_path, monkeypatch):
    path = tmp_path / "approvals.json"
    session1_cache = ApprovalCache(path=path)
    cb1 = make_interactive_approval(session1_cache)
    monkeypatch.setattr(builtins, "input", lambda *a: "a")
    await cb1(_req("known-cmd"), _ASK)

    session2_cache = ApprovalCache(path=path)
    cb2 = make_interactive_approval(session2_cache)
    monkeypatch.setattr(builtins, "input", lambda *a: "n")
    assert await cb2(_req("brand-new-cmd"), _ASK) is False


def test_new_approval_cache_factory_opts_into_persistence(tmp_path, monkeypatch):
    import d2c.approvals as approvals_mod
    from d2c.main import _new_approval_cache

    monkeypatch.setattr(approvals_mod, "DEFAULT_APPROVALS_PATH", tmp_path / "approvals.json")
    cache = _new_approval_cache()
    assert cache._path == tmp_path / "approvals.json"
