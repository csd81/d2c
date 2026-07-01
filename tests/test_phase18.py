"""Tests for Phase 18: Missing Core Tools (Glob, Grep, NotebookEdit, Task tools)."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from d2c.tools.glob_tool import GlobTool
from d2c.tools.grep_tool import GrepTool
from d2c.tools.notebook_edit import NotebookEditTool
from d2c.tools.task_tools import (
    TaskCreateTool,
    TaskListTool,
    TaskStore,
    TaskUpdateTool,
)

# ── GlobTool tests ────────────────────────────────────────────────────────


class TestGlobTool:
    def test_finds_files_matching_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("x")
            (root / "b.js").write_text("y")
            (root / "c.py").write_text("z")

            tool = GlobTool(cwd=root)
            result = asyncio.run(tool.execute("*.py", path="."))
            assert not result.error
            assert "a.py" in result.output
            assert "c.py" in result.output
            assert "b.js" not in result.output

    def test_no_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool = GlobTool(cwd=root)
            result = asyncio.run(tool.execute("*.rs", path="."))
            assert not result.error
            assert "No files found" in result.output

    def test_recursive_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("x")
            (root / "tests").mkdir()
            (root / "tests" / "test_main.py").write_text("y")

            tool = GlobTool(cwd=root)
            result = asyncio.run(tool.execute("**/*.py", path="."))
            assert not result.error
            assert "main.py" in result.output
            assert "test_main.py" in result.output

    def test_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data.json").write_text("{}")

            tool = GlobTool(cwd=Path("/"))
            result = asyncio.run(tool.execute("*.json", path=str(root)))
            assert not result.error
            assert result.metadata["count"] == 1

    def test_sorted_by_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            import time

            root = Path(tmp)
            first = root / "first.py"
            second = root / "second.py"
            first.write_text("older")
            time.sleep(0.05)  # ensure different mtime on Windows (10ms resolution)
            second.write_text("newer")

            tool = GlobTool(cwd=root)
            result = asyncio.run(tool.execute("*.py", path="."))
            assert not result.error
            lines = result.output.strip().split("\n")
            # second.py (newer) should come first
            assert lines[0].endswith("second.py")


# ── GrepTool tests ─────────────────────────────────────────────────────────


class TestGrepTool:
    def test_finds_content_in_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("def foo():\n    pass\n")
            (root / "b.py").write_text("def bar():\n    pass\n")

            tool = GrepTool(cwd=root)
            result = asyncio.run(
                tool.execute(
                    "def foo",
                    path=".",
                    output_mode="files_with_matches",
                )
            )
            assert not result.error
            assert "a.py" in result.output
            assert "b.py" not in result.output

    def test_content_mode_shows_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test.py").write_text("import os\nimport sys\nprint('hello')\n")

            tool = GrepTool(cwd=root)
            result = asyncio.run(
                tool.execute(
                    "import",
                    path="test.py",
                    output_mode="content",
                    n=True,
                )
            )
            assert not result.error
            assert "import os" in result.output
            assert "import sys" in result.output

    def test_count_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data.py").write_text("x = 1\ny = 2\nz = 3\n")

            tool = GrepTool(cwd=root)
            result = asyncio.run(
                tool.execute(
                    "=",
                    path=".",
                    output_mode="count",
                )
            )
            assert not result.error
            assert result.metadata["count"] >= 1

    def test_no_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "empty.txt").write_text("nothing here")

            tool = GrepTool(cwd=root)
            result = asyncio.run(
                tool.execute(
                    "NONEXISTENT_PATTERN_XYZ",
                    path=".",
                )
            )
            # May find no matches
            assert not result.error

    def test_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test.txt").write_text("Hello WORLD\n")

            tool = GrepTool(cwd=root)
            result = asyncio.run(
                tool.execute(
                    "world",
                    path=".",
                    output_mode="content",
                    i=True,
                )
            )
            assert not result.error
            assert "WORLD" in result.output

    def test_invalid_regex(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test.txt").write_text("hello")

            tool = GrepTool(cwd=root)
            result = asyncio.run(
                tool.execute(
                    "[invalid",
                    path=".",
                )
            )
            # Python fallback should catch invalid regex
            # If rg is available it will also error
            if result.error:
                assert "Invalid" in result.output or "error" in result.output.lower()

    def test_nonexistent_path(self):
        tool = GrepTool()
        result = asyncio.run(
            tool.execute(
                "pattern",
                path="/nonexistent/path/xyz",
            )
        )
        assert result.error is True

    def test_glob_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text("TODO: fix this")
            (root / "b.js").write_text("TODO: fix that")

            tool = GrepTool(cwd=root)
            result = asyncio.run(
                tool.execute(
                    "TODO",
                    path=".",
                    glob="*.py",
                    output_mode="files_with_matches",
                )
            )
            assert not result.error
            assert "a.py" in result.output
            assert "b.js" not in result.output


# ── NotebookEditTool tests ─────────────────────────────────────────────────


class TestNotebookEditTool:
    def _make_notebook(self, path: Path, cells: list[dict]) -> None:
        nb = {
            "cells": cells,
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 2,
        }
        path.write_text(json.dumps(nb))

    def test_read_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nb_path = root / "test.ipynb"
            self._make_notebook(
                nb_path,
                [
                    {
                        "cell_type": "code",
                        "source": ["print('hello')"],
                        "outputs": [],
                        "execution_count": None,
                    },
                    {"cell_type": "markdown", "source": ["# Title"], "metadata": {}},
                ],
            )

            tool = NotebookEditTool(cwd=root)
            result = asyncio.run(tool.execute(str(nb_path), action="read"))
            assert not result.error
            assert "[0] code" in result.output
            assert "[1] markdown" in result.output

    def test_edit_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nb_path = root / "test.ipynb"
            self._make_notebook(
                nb_path,
                [
                    {
                        "cell_type": "code",
                        "source": ["print('old')"],
                        "outputs": [],
                        "execution_count": None,
                    },
                ],
            )

            tool = NotebookEditTool(cwd=root)
            result = asyncio.run(
                tool.execute(
                    str(nb_path),
                    action="edit",
                    cell_id=0,
                    new_source="print('new')",
                )
            )
            assert not result.error
            assert "Cell [0] updated" in result.output

            # Verify written
            nb_data = json.loads(nb_path.read_text())
            assert "".join(nb_data["cells"][0]["source"]) == "print('new')"

    def test_add_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nb_path = root / "test.ipynb"
            self._make_notebook(nb_path, [])

            tool = NotebookEditTool(cwd=root)
            result = asyncio.run(
                tool.execute(
                    str(nb_path),
                    action="add",
                    new_source="x = 1",
                    cell_type="code",
                )
            )
            assert not result.error

            nb_data = json.loads(nb_path.read_text())
            assert len(nb_data["cells"]) == 1
            assert "".join(nb_data["cells"][0]["source"]) == "x = 1"

    def test_delete_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nb_path = root / "test.ipynb"
            self._make_notebook(
                nb_path,
                [
                    {
                        "cell_type": "code",
                        "source": ["keep me"],
                        "outputs": [],
                        "execution_count": None,
                    },
                    {
                        "cell_type": "code",
                        "source": ["delete me"],
                        "outputs": [],
                        "execution_count": None,
                    },
                ],
            )

            tool = NotebookEditTool(cwd=root)
            result = asyncio.run(
                tool.execute(
                    str(nb_path),
                    action="delete",
                    cell_id=1,
                )
            )
            assert not result.error
            assert "Cell [1]" in result.output

            nb_data = json.loads(nb_path.read_text())
            assert len(nb_data["cells"]) == 1
            assert "".join(nb_data["cells"][0]["source"]) == "keep me"

    def test_edit_invalid_cell_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nb_path = root / "test.ipynb"
            self._make_notebook(nb_path, [])

            tool = NotebookEditTool(cwd=root)
            result = asyncio.run(
                tool.execute(
                    str(nb_path),
                    action="edit",
                    cell_id=99,
                    new_source="x",
                )
            )
            assert result.error is True
            assert "Invalid cell_id" in result.output

    def test_read_nonexistent_file(self):
        tool = NotebookEditTool()
        result = asyncio.run(
            tool.execute(
                "/nonexistent/notebook.ipynb",
                action="read",
            )
        )
        assert result.error is True

    def test_invalid_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nb_path = root / "test.ipynb"
            self._make_notebook(nb_path, [])
            tool = NotebookEditTool(cwd=root)
            result = asyncio.run(tool.execute(str(nb_path), action="unknown"))
            assert result.error is True


# ── Task tools tests ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_task_store():
    """Reset singleton TaskStore before each test to prevent state leakage."""
    TaskStore._instance = None


class TestTaskStore:
    def test_create_task(self):
        store = TaskStore()
        task = store.create("Fix login bug", "The login endpoint returns 500")
        assert task["id"] == "1"
        assert task["subject"] == "Fix login bug"
        assert task["status"] == "pending"

    def test_list_tasks(self):
        store = TaskStore()
        store.create("Task A", "Do A")
        store.create("Task B", "Do B")
        tasks = store.list_all()
        assert len(tasks) == 2

    def test_get_task(self):
        store = TaskStore()
        store.create("Test", "Desc")
        task = store.get("1")
        assert task["subject"] == "Test"

    def test_get_nonexistent_task(self):
        store = TaskStore()
        assert store.get("999") is None

    def test_update_task_status(self):
        store = TaskStore()
        store.create("Test", "Desc")
        updated = store.update("1", status="in_progress")
        assert updated["status"] == "in_progress"


class TestTaskCreateTool:
    def test_creates_task(self):
        tool = TaskCreateTool()
        result = asyncio.run(
            tool.execute(
                subject="Add tests",
                description="Write unit tests for module X",
            )
        )
        assert not result.error
        assert "Add tests" in result.output
        assert "pending" in result.output

    def test_task_has_id(self):
        tool = TaskCreateTool()
        result = asyncio.run(
            tool.execute(
                subject="Refactor",
                description="Clean up the codebase",
            )
        )
        assert result.metadata["task"]["id"] is not None


class TestTaskUpdateTool:
    def test_update_status(self):
        create = TaskCreateTool()
        update = TaskUpdateTool()

        asyncio.run(create.execute(subject="Test", description="Desc"))
        result = asyncio.run(update.execute(taskId="1", status="in_progress"))
        assert not result.error
        assert "updated" in result.output.lower()

    def test_invalid_transition(self):
        create = TaskCreateTool()
        update = TaskUpdateTool()

        asyncio.run(create.execute(subject="Test", description="Desc"))
        asyncio.run(update.execute(taskId="1", status="in_progress"))
        asyncio.run(update.execute(taskId="1", status="completed"))

        # completed → pending is not allowed
        result = asyncio.run(update.execute(taskId="1", status="pending"))
        assert result.error is True
        assert "Invalid status transition" in result.output

    def test_delete_task(self):
        create = TaskCreateTool()
        update = TaskUpdateTool()
        list_tool = TaskListTool()

        asyncio.run(create.execute(subject="Remove me", description="Will be deleted"))
        result = asyncio.run(update.execute(taskId="1", status="deleted"))
        assert not result.error
        assert "deleted" in result.output.lower()

        # Verify gone
        list_result = asyncio.run(list_tool.execute())
        assert "Remove me" not in list_result.output

    def test_nonexistent_task(self):
        update = TaskUpdateTool()
        result = asyncio.run(update.execute(taskId="nonexistent"))
        assert result.error is True
        assert "not found" in result.output.lower()

    def test_update_subject(self):
        create = TaskCreateTool()
        update = TaskUpdateTool()

        asyncio.run(create.execute(subject="Old name", description="Desc"))
        result = asyncio.run(
            update.execute(
                taskId="1",
                subject="New name",
            )
        )
        assert not result.error
        assert "New name" in result.output


class TestTaskListTool:
    def test_empty_list(self):
        tool = TaskListTool()
        result = asyncio.run(tool.execute())
        assert not result.error
        assert "No tasks created" in result.output

    def test_lists_tasks_with_status(self):
        create = TaskCreateTool()
        list_tool = TaskListTool()

        asyncio.run(create.execute(subject="Task 1", description="d1"))
        asyncio.run(create.execute(subject="Task 2", description="d2"))

        result = asyncio.run(list_tool.execute())
        assert "Task 1" in result.output
        assert "Task 2" in result.output
        assert result.metadata["count"] == 2

    def test_in_progress_first(self):
        create = TaskCreateTool()
        update = TaskUpdateTool()
        list_tool = TaskListTool()

        asyncio.run(create.execute(subject="Normal", description="d"))
        asyncio.run(create.execute(subject="Active", description="d"))
        asyncio.run(update.execute(taskId="2", status="in_progress"))

        result = asyncio.run(list_tool.execute())
        lines = result.output.strip().split("\n")
        # in_progress task should be first
        assert "Active" in lines[0]
