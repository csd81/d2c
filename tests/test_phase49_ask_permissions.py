"""Phase 49: true interactive ASK permission handling.

Re-verifies the ASK invariant end-to-end and covers the granular permission
audit events (permission_ask / approved / denied / required / approval_error)
correlated by tool_call_id, with no secret leakage.
"""

import builtins
import json

import pytest

from d2c.config import Config
from d2c.loop import _execute_one_tool
from d2c.observability import AuditLogger, set_audit_logger
from d2c.permissions import (
    PermissionDecision,
    PermissionEngine,
    PermissionMode,
    PermissionRequest,
    PermissionResult,
    classify_permission_event,
)
from d2c.streaming_executor import StreamingToolExecutor
from d2c.tools import PermissionCategory, Tool, ToolResult, ToolUse


class SideEffectTool(Tool):
    name = "SideEffect"
    description = "records whether it executed"
    input_schema = {"type": "object", "properties": {}, "required": []}
    category = PermissionCategory.SHELL  # DEFAULT mode → ASK
    is_concurrent_safe = False

    def __init__(self):
        self.executed = False

    async def execute(self, **kwargs) -> ToolResult:
        self.executed = True
        return ToolResult(output="ran")


def _engine():
    return PermissionEngine(mode=PermissionMode.DEFAULT)


def _tu():
    return ToolUse(id="tc-1", name="SideEffect", input={})


async def _approve(req, res):
    return True


async def _reject(req, res):
    return False


async def _boom(req, res):
    raise RuntimeError("callback exploded")


@pytest.fixture(autouse=True)
def _reset_logger():
    yield
    set_audit_logger(None)


# ── Core invariant (non-streaming) ────────────────────────────────────


@pytest.mark.asyncio
async def test_ask_no_callback_does_not_execute():
    tool = SideEffectTool()
    res = await _execute_one_tool(_tu(), {"SideEffect": tool}, _engine(), None, None)
    assert tool.executed is False
    assert res.error and res.metadata.get("permission_required") is True


@pytest.mark.asyncio
async def test_ask_reject_does_not_execute():
    tool = SideEffectTool()
    res = await _execute_one_tool(_tu(), {"SideEffect": tool}, _engine(), None, _reject)
    assert tool.executed is False and res.error


@pytest.mark.asyncio
async def test_ask_approve_executes_once():
    tool = SideEffectTool()
    res = await _execute_one_tool(_tu(), {"SideEffect": tool}, _engine(), None, _approve)
    assert tool.executed is True and not res.error


@pytest.mark.asyncio
async def test_ask_callback_exception_denies():
    tool = SideEffectTool()
    res = await _execute_one_tool(_tu(), {"SideEffect": tool}, _engine(), None, _boom)
    assert tool.executed is False and res.error


# ── Streaming ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_streaming_ask_without_approval_does_not_execute():
    tool = SideEffectTool()
    ex = StreamingToolExecutor(
        tools_map={"SideEffect": tool},
        permission_engine=_engine(),
        hooks=None,
        session_store=None,
        approval_callback=None,
    )
    ex.submit(_tu())
    ((_, res),) = await ex.get_results()
    assert tool.executed is False and res.error


@pytest.mark.asyncio
async def test_streaming_executes_after_approval():
    tool = SideEffectTool()
    ex = StreamingToolExecutor(
        tools_map={"SideEffect": tool},
        permission_engine=_engine(),
        hooks=None,
        session_store=None,
        approval_callback=_approve,
    )
    ex.submit(_tu())
    ((_, res),) = await ex.get_results()
    assert tool.executed is True and not res.error


# ── MCP ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_ask_returns_permission_required(tmp_dir, trusted_gate):
    from d2c.mcp.server import MCPServer

    tool = SideEffectTool()
    srv = MCPServer(config=Config(cwd=tmp_dir), permission_engine=_engine())
    srv._tools_map = {"SideEffect": tool}
    resp = await srv._handle_call_tool(1, {"name": "SideEffect", "arguments": {}})
    assert resp["result"]["isError"] is True
    assert "Permission required" in resp["result"]["content"][0]["text"]
    assert tool.executed is False


# ── Interactive prompt ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_interactive_prompt_empty_denies(monkeypatch):
    from d2c.main import interactive_approval

    monkeypatch.setattr(builtins, "input", lambda *a: "")
    req = PermissionRequest(
        tool_name="Bash", tool_input={"command": "ls"}, tool_category=PermissionCategory.SHELL
    )
    assert (
        await interactive_approval(req, PermissionResult(PermissionDecision.ASK, reason="?"))
        is False
    )


@pytest.mark.asyncio
async def test_interactive_prompt_accepts_yes_rejects_no(monkeypatch):
    from d2c.main import interactive_approval

    req = PermissionRequest(
        tool_name="Bash", tool_input={"command": "ls"}, tool_category=PermissionCategory.SHELL
    )
    res = PermissionResult(PermissionDecision.ASK, reason="?")
    for yes in ("y", "yes", "YES"):
        monkeypatch.setattr(builtins, "input", lambda *a, _v=yes: _v)
        assert await interactive_approval(req, res) is True
    for no in ("n", "no", ""):
        monkeypatch.setattr(builtins, "input", lambda *a, _v=no: _v)
        assert await interactive_approval(req, res) is False


# ── Granular audit events (Phase 49) ──────────────────────────────────


def test_classify_permission_event():
    from d2c.permissions import PERMISSION_REQUIRED_REASON

    ask = PermissionResult(PermissionDecision.ASK)
    assert (
        classify_permission_event(
            ask, PermissionResult(PermissionDecision.ALLOW, "approved by user")
        )
        == "permission_approved"
    )
    assert (
        classify_permission_event(ask, PermissionResult(PermissionDecision.DENY, "denied by user"))
        == "permission_denied"
    )
    assert (
        classify_permission_event(
            ask, PermissionResult(PermissionDecision.DENY, PERMISSION_REQUIRED_REASON)
        )
        == "permission_required"
    )
    assert (
        classify_permission_event(
            ask, PermissionResult(PermissionDecision.DENY, "Permission approval error (X); denied.")
        )
        == "permission_approval_error"
    )
    # plain rule/mode ALLOW → no event
    assert (
        classify_permission_event(
            PermissionResult(PermissionDecision.ALLOW), PermissionResult(PermissionDecision.ALLOW)
        )
        is None
    )


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_audit_records_ask_and_required_no_secret(tmp_dir, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-leak-abc123")
    path = tmp_dir / "audit.jsonl"
    set_audit_logger(AuditLogger(path=path, enabled=True))

    tool = SideEffectTool()
    await _execute_one_tool(_tu(), {"SideEffect": tool}, _engine(), None, None)  # ASK, no cb

    evs = {e["event"]: e for e in _events(path)}
    assert "permission_ask" in evs
    assert "permission_required" in evs
    # correlation by tool_call_id
    assert evs["permission_ask"]["tool_call_id"] == "tc-1"
    assert evs["permission_required"]["tool_call_id"] == "tc-1"
    assert "sk-should-not-leak-abc123" not in path.read_text()


@pytest.mark.asyncio
async def test_audit_records_approved_and_denied(tmp_dir):
    path = tmp_dir / "audit.jsonl"
    set_audit_logger(AuditLogger(path=path, enabled=True, level="INFO"))

    await _execute_one_tool(_tu(), {"SideEffect": SideEffectTool()}, _engine(), None, _approve)
    await _execute_one_tool(_tu(), {"SideEffect": SideEffectTool()}, _engine(), None, _reject)

    names = {e["event"] for e in _events(path)}
    assert "permission_approved" in names
    assert "permission_denied" in names
