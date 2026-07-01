"""Phase 43: interactive ASK permission handling — ASK never auto-executes."""

import builtins

import pytest

from d2c.config import Config
from d2c.loop import _execute_one_tool
from d2c.streaming_executor import StreamingToolExecutor
from d2c.permissions import (
    PermissionEngine,
    PermissionMode,
    PermissionRequest,
    PermissionResult,
    PermissionDecision,
    resolve_permission_decision,
)
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


def _default_engine():
    return PermissionEngine(mode=PermissionMode.DEFAULT)


async def _approve(req, res):
    return True


async def _reject(req, res):
    return False


async def _boom(req, res):
    raise RuntimeError("callback exploded")


def _tu():
    return ToolUse(id="1", name="SideEffect", input={})


# ── Shared resolver ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolver_ask_no_callback_denies():
    req = PermissionRequest(tool_name="X", tool_input={}, tool_category=PermissionCategory.SHELL)
    ask = PermissionResult(PermissionDecision.ASK)
    out = await resolve_permission_decision(req, ask, None)
    assert out.decision == PermissionDecision.DENY

    assert (await resolve_permission_decision(req, ask, _approve)).decision == PermissionDecision.ALLOW
    assert (await resolve_permission_decision(req, ask, _reject)).decision == PermissionDecision.DENY
    assert (await resolve_permission_decision(req, ask, _boom)).decision == PermissionDecision.DENY
    # No engine → None passes through (caller executes).
    assert await resolve_permission_decision(req, None, None) is None


# ── Non-streaming executor (headless == no callback) ──────────────────

@pytest.mark.asyncio
async def test_ask_no_callback_does_not_execute():
    tool = SideEffectTool()
    res = await _execute_one_tool(_tu(), {"SideEffect": tool}, _default_engine(), None, None)
    assert tool.executed is False
    assert res.error is True
    assert res.metadata.get("permission_required") is True


@pytest.mark.asyncio
async def test_ask_callback_reject_does_not_execute():
    tool = SideEffectTool()
    res = await _execute_one_tool(_tu(), {"SideEffect": tool}, _default_engine(), None, _reject)
    assert tool.executed is False
    assert res.error is True


@pytest.mark.asyncio
async def test_ask_callback_approve_executes_once():
    tool = SideEffectTool()
    res = await _execute_one_tool(_tu(), {"SideEffect": tool}, _default_engine(), None, _approve)
    assert tool.executed is True
    assert res.error is False


@pytest.mark.asyncio
async def test_ask_callback_exception_denies():
    tool = SideEffectTool()
    res = await _execute_one_tool(_tu(), {"SideEffect": tool}, _default_engine(), None, _boom)
    assert tool.executed is False
    assert res.error is True


# ── Streaming executor ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_streaming_ask_without_approval_does_not_execute():
    tool = SideEffectTool()
    ex = StreamingToolExecutor(
        tools_map={"SideEffect": tool}, permission_engine=_default_engine(),
        hooks=None, session_store=None, approval_callback=None,
    )
    ex.submit(_tu())
    (_, res), = await ex.get_results()
    assert tool.executed is False
    assert res.error is True


@pytest.mark.asyncio
async def test_streaming_executes_after_approval():
    tool = SideEffectTool()
    ex = StreamingToolExecutor(
        tools_map={"SideEffect": tool}, permission_engine=_default_engine(),
        hooks=None, session_store=None, approval_callback=_approve,
    )
    ex.submit(_tu())
    (_, res), = await ex.get_results()
    assert tool.executed is True
    assert res.error is False


# ── MCP server ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_ask_returns_permission_required(tmp_dir, trusted_gate):
    from d2c.mcp.server import MCPServer

    tool = SideEffectTool()
    srv = MCPServer(config=Config(cwd=tmp_dir), permission_engine=_default_engine())
    srv._tools_map = {"SideEffect": tool}

    resp = await srv._handle_call_tool(1, {"name": "SideEffect", "arguments": {}})
    assert resp["result"]["isError"] is True
    assert "Permission required" in resp["result"]["content"][0]["text"]
    assert tool.executed is False


@pytest.mark.asyncio
async def test_mcp_without_engine_executes(tmp_dir, trusted_gate):
    from d2c.mcp.server import MCPServer

    tool = SideEffectTool()
    srv = MCPServer(config=Config(cwd=tmp_dir))  # no engine → current behavior
    srv._tools_map = {"SideEffect": tool}
    resp = await srv._handle_call_tool(1, {"name": "SideEffect", "arguments": {}})
    assert tool.executed is True
    assert resp["result"].get("isError") in (False, None)


# ── Interactive prompt ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_interactive_prompt_defaults_to_deny_on_empty(monkeypatch, capsys):
    from d2c.main import interactive_approval
    monkeypatch.setattr(builtins, "input", lambda *a: "")
    req = PermissionRequest(tool_name="Bash", tool_input={"command": "ls"}, tool_category=PermissionCategory.SHELL)
    assert await interactive_approval(req, PermissionResult(PermissionDecision.ASK, reason="?")) is False


@pytest.mark.asyncio
async def test_interactive_prompt_accepts_yes_rejects_no(monkeypatch):
    from d2c.main import interactive_approval
    req = PermissionRequest(tool_name="Bash", tool_input={"command": "ls"}, tool_category=PermissionCategory.SHELL)
    res = PermissionResult(PermissionDecision.ASK, reason="?")

    for ans in ("y", "yes", "Y", "YES"):
        monkeypatch.setattr(builtins, "input", lambda *a, _v=ans: _v)
        assert await interactive_approval(req, res) is True
    for ans in ("n", "no", "nope", "x"):
        monkeypatch.setattr(builtins, "input", lambda *a, _v=ans: _v)
        assert await interactive_approval(req, res) is False
