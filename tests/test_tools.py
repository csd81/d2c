"""Tests for Phase 1: Tool Base & Built-in Tools."""

import asyncio
import json
from pathlib import Path

import pytest

from d2c.tools import PermissionCategory, Tool, ToolResult
from d2c.tools.bash_tool import BashTool
from d2c.tools.edit_tool import FileEditTool
from d2c.tools.pool import Config, Rule, RuleType, assembleToolPool, filterToolsByDenyRules, getAllBaseTools
from d2c.tools.read_tool import FileReadTool
from d2c.tools.write_tool import FileWriteTool, mark_file_read


# ── Tool ABC ──────────────────────────────────────────────────────────

def test_tool_result_defaults():
    r = ToolResult(output="hello")
    assert r.output == "hello"
    assert r.attachments == []
    assert r.error is False
    assert r.metadata == {}


def test_tool_result_str():
    r = ToolResult(output="hello")
    assert str(r) == "hello"


def test_tool_api_format():
    class FakeTool(Tool):
        name = "Fake"
        description = "A fake tool"
        input_schema = {"type": "object", "properties": {}}
        category = PermissionCategory.READ

        async def execute(self, **kwargs):
            return ToolResult(output="done")

    t = FakeTool()
    fmt = t.to_api_format()
    assert fmt["name"] == "Fake"
    assert fmt["description"] == "A fake tool"
    assert fmt["input_schema"] == {"type": "object", "properties": {}}


# ── FileReadTool ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_text_file(tmp_dir):
    f = tmp_dir / "test.txt"
    f.write_text("line 1\nline 2\nline 3\n")

    tool = FileReadTool()
    result = await tool.execute(file_path=str(f))

    assert result.error is False
    assert "line 1" in result.output
    assert "line 2" in result.output
    assert "line 3" in result.output


@pytest.mark.asyncio
async def test_read_nonexistent_file(tmp_dir):
    tool = FileReadTool()
    nonexistent = str(tmp_dir / "nonexistent.txt")
    result = await tool.execute(file_path=nonexistent)
    assert result.error is True
    assert "not found" in result.output.lower()


@pytest.mark.asyncio
async def test_read_relative_path_fails():
    tool = FileReadTool()
    result = await tool.execute(file_path="relative/path.txt")
    assert result.error is True
    assert "absolute" in result.output.lower()


@pytest.mark.asyncio
async def test_read_directory_fails(tmp_dir):
    tool = FileReadTool()
    result = await tool.execute(file_path=str(tmp_dir))
    assert result.error is True
    assert "directory" in result.output.lower()


@pytest.mark.asyncio
async def test_read_with_offset(tmp_dir):
    f = tmp_dir / "lines.txt"
    f.write_text("\n".join(str(i) for i in range(100)))

    tool = FileReadTool()
    result = await tool.execute(file_path=str(f), offset=95, limit=5)

    assert result.error is False
    assert "96\t95" in result.output  # offset is 0-based internally


@pytest.mark.asyncio
async def test_read_with_limit(tmp_dir):
    f = tmp_dir / "lines.txt"
    f.write_text("\n".join(str(i) for i in range(100)))

    tool = FileReadTool()
    result = await tool.execute(file_path=str(f), limit=10)

    assert result.error is False
    assert "[Showing lines 1-10 of 100 total]" in result.output


@pytest.mark.asyncio
async def test_read_image_file(tmp_dir):
    f = tmp_dir / "img.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    tool = FileReadTool()
    result = await tool.execute(file_path=str(f))

    assert result.error is False
    assert len(result.attachments) == 1
    assert result.attachments[0]["type"] == "image"


@pytest.mark.asyncio
async def test_read_notebook(tmp_dir):
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "source": ["print('hello')"],
                "outputs": [{"text": ["hello\n"]}],
            },
            {
                "cell_type": "markdown",
                "source": ["# Title"],
            },
        ],
    }
    f = tmp_dir / "nb.ipynb"
    f.write_text(json.dumps(nb))

    tool = FileReadTool()
    result = await tool.execute(file_path=str(f))

    assert result.error is False
    assert "print('hello')" in result.output
    assert "# Title" in result.output


# ── FileWriteTool ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_new_file(tmp_dir):
    f = tmp_dir / "new.txt"
    tool = FileWriteTool()
    result = await tool.execute(file_path=str(f), content="hello world")

    assert result.error is False
    assert f.read_text() == "hello world"


@pytest.mark.asyncio
async def test_write_overwrite_after_read(tmp_dir):
    f = tmp_dir / "existing.txt"
    f.write_text("old content")

    # Read first
    mark_file_read(str(f))

    tool = FileWriteTool()
    result = await tool.execute(file_path=str(f), content="new content")

    assert result.error is False
    assert f.read_text() == "new content"


@pytest.mark.asyncio
async def test_write_fails_without_prior_read(tmp_dir):
    f = tmp_dir / "existing.txt"
    f.write_text("old content")

    tool = FileWriteTool()
    result = await tool.execute(file_path=str(f), content="new content")

    assert result.error is True
    assert "Read" in result.output


@pytest.mark.asyncio
async def test_write_relative_path_fails():
    tool = FileWriteTool()
    result = await tool.execute(file_path="relative/path.txt", content="x")
    assert result.error is True


@pytest.mark.asyncio
async def test_write_parent_missing(tmp_dir):
    f = tmp_dir / "nonexistent" / "file.txt"
    tool = FileWriteTool()
    result = await tool.execute(file_path=str(f), content="x")
    assert result.error is True
    assert "parent directory" in result.output.lower()


# ── FileEditTool ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_edit_single_occurrence(tmp_dir):
    f = tmp_dir / "code.py"
    f.write_text("hello world\nmore text\n")

    mark_file_read(str(f))

    tool = FileEditTool()
    result = await tool.execute(
        file_path=str(f),
        old_string="hello world",
        new_string="goodbye world",
    )

    assert result.error is False
    assert f.read_text() == "goodbye world\nmore text\n"


@pytest.mark.asyncio
async def test_edit_non_unique_fails(tmp_dir):
    f = tmp_dir / "code.py"
    f.write_text("dup\nmiddle\ndup\n")

    mark_file_read(str(f))

    tool = FileEditTool()
    result = await tool.execute(
        file_path=str(f),
        old_string="dup",
        new_string="replaced",
    )

    assert result.error is True
    assert "not unique" in result.output.lower()


@pytest.mark.asyncio
async def test_edit_replace_all(tmp_dir):
    f = tmp_dir / "code.py"
    f.write_text("dup\nmiddle\ndup\n")

    mark_file_read(str(f))

    tool = FileEditTool()
    result = await tool.execute(
        file_path=str(f),
        old_string="dup",
        new_string="replaced",
        replace_all=True,
    )

    assert result.error is False
    assert f.read_text() == "replaced\nmiddle\nreplaced\n"
    assert result.metadata["occurrences_replaced"] == 2


@pytest.mark.asyncio
async def test_edit_not_found(tmp_dir):
    f = tmp_dir / "code.py"
    f.write_text("some content\n")

    mark_file_read(str(f))

    tool = FileEditTool()
    result = await tool.execute(
        file_path=str(f),
        old_string="not in file",
        new_string="replacement",
    )

    assert result.error is True
    assert "not found" in result.output.lower()


@pytest.mark.asyncio
async def test_edit_without_prior_read(tmp_dir):
    f = tmp_dir / "code.py"
    f.write_text("content\n")

    tool = FileEditTool()
    result = await tool.execute(
        file_path=str(f),
        old_string="content",
        new_string="new",
    )

    assert result.error is True
    assert "Read" in result.output


@pytest.mark.asyncio
async def test_edit_same_string_fails(tmp_dir):
    f = tmp_dir / "code.py"
    f.write_text("content\n")

    mark_file_read(str(f))

    tool = FileEditTool()
    result = await tool.execute(
        file_path=str(f),
        old_string="content",
        new_string="content",
    )

    assert result.error is True
    assert "different" in result.output.lower()


# ── BashTool ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bash_simple_command(tmp_dir):
    tool = BashTool(cwd=tmp_dir)
    result = await tool.execute(command="echo hello")

    assert result.error is False
    assert "hello" in result.output


@pytest.mark.asyncio
async def test_bash_failing_command(tmp_dir):
    tool = BashTool(cwd=tmp_dir)
    result = await tool.execute(command="exit 1")

    assert result.error is True
    assert result.metadata["exit_code"] == 1


@pytest.mark.asyncio
async def test_bash_timeout(tmp_dir):
    tool = BashTool(cwd=tmp_dir)
    result = await tool.execute(command="sleep 10", timeout=500)

    assert result.error is True
    assert result.metadata.get("timed_out") is True


@pytest.mark.asyncio
async def test_bash_creates_file(tmp_dir):
    tool = BashTool(cwd=tmp_dir)
    # Use python to write the file to avoid PowerShell UTF-16 encoding
    result = await tool.execute(command="python -c \"open('output.txt','w',encoding='utf-8').write('data')\"")
    assert result.error is False
    assert (tmp_dir / "output.txt").exists()
    assert (tmp_dir / "output.txt").read_text(encoding="utf-8").strip() == "data"


# ── Tool Pool ─────────────────────────────────────────────────────────

def test_get_all_base_tools():
    config = Config()
    tools = getAllBaseTools(config)
    names = {t.name for t in tools}
    assert "Read" in names
    assert "Write" in names
    assert "Edit" in names
    assert "Bash" in names


def test_filter_by_deny_rule():
    tools = getAllBaseTools(Config())
    rule = Rule(rule_type=RuleType.DENY, pattern="Bash", reason="no shell")
    filtered = filterToolsByDenyRules(tools, [rule])
    names = {t.name for t in filtered}
    assert "Bash" not in names
    assert "Read" in names


def test_filter_by_deny_pattern_wildcard():
    tools = getAllBaseTools(Config())
    # Deny all file operations
    for pattern in ["Read", "Write", "Edit"]:
        rule = Rule(rule_type=RuleType.DENY, pattern=pattern)
        tools = filterToolsByDenyRules(tools, [rule])

    names = {t.name for t in tools}
    assert "Read" not in names
    assert "Write" not in names
    assert "Edit" not in names


@pytest.mark.asyncio
async def test_assemble_tool_pool():
    config = Config()
    tools = await assembleToolPool(config)
    names = {t.name for t in tools}
    assert names == {"Read", "Write", "Edit", "Bash", "Agent", "Skill", "WebFetch", "WebSearch", "Glob", "Grep", "NotebookEdit", "TaskCreate", "TaskUpdate", "TaskList", "ToolSearch"}


@pytest.mark.asyncio
async def test_assemble_tool_pool_with_deny_rules():
    config = Config(deny_rules=[
        Rule(rule_type=RuleType.DENY, pattern="Bash"),
    ])
    tools = await assembleToolPool(config)
    names = {t.name for t in tools}
    assert "Bash" not in names


def test_rule_matches_exact():
    r = Rule(rule_type=RuleType.DENY, pattern="Read")
    assert r.matches_tool("Read") is True
    assert r.matches_tool("ReadMore") is False


def test_rule_matches_wildcard():
    r = Rule(rule_type=RuleType.DENY, pattern="mcp__*")
    assert r.matches_tool("mcp__server") is True  # pattern "mcp__*" has leading prefix "mcp__"
    assert r.matches_tool("mcp") is False
