# Phase 18: Missing Core Tools (Glob, Grep, NotebookEdit, Task tools)

**Paper Reference:** Section 3.2 — "Up to 54 built-in tools (19 unconditional, 35
conditional on feature flags and user type)" — we have ~8

**Priority:** LOW

## Rationale

Several essential developer tools listed in the paper's architecture are missing.
These are high-ROI additions since they're frequently used in coding workflows.
Adding them significantly expands the agent's action surface with minimal complexity.

## Files to Create/Modify

1. CREATE `src/d2c/tools/glob_tool.py` — File glob pattern matching
2. CREATE `src/d2c/tools/grep_tool.py` — Content search with ripgrep
3. CREATE `src/d2c/tools/notebook_edit.py` — Jupyter notebook manipulation
4. CREATE `src/d2c/tools/task_tools.py` — TaskCreate, TaskUpdate, TaskList, TaskGet
5. MODIFY `src/d2c/tools/pool.py` — register new tools

## Glob Tool

```python
class GlobTool(Tool):
    name = "Glob"
    description = "Fast file pattern matching. Supports glob patterns like **/*.js."
    category = PermissionCategory.READ
    is_concurrent_safe = True

    async def execute(self, pattern: str, path: str = ".") -> ToolResult:
        # Uses pathlib.glob, sorted by mtime
        # Returns relative file paths
```

## Grep Tool

```python
class GrepTool(Tool):
    name = "Grep"
    description = "Content search with ripgrep. Supports full regex syntax."
    category = PermissionCategory.READ
    is_concurrent_safe = True

    async def execute(self, pattern: str, path: str = ".",
                      glob: str = None, output_mode: str = "files_with_matches",
                      head_limit: int = 250, multiline: bool = False) -> ToolResult:
        # Wraps ripgrep (rg) for content search
        # Modes: content, files_with_matches, count
        # Context lines: -A, -B, -C
```

## NotebookEdit Tool

```python
class NotebookEditTool(Tool):
    name = "NotebookEdit"
    description = "Edit Jupyter notebook cells."
    category = PermissionCategory.WRITE
    is_concurrent_safe = False

    async def execute(self, notebook_path: str, cell_id: str = None,
                      new_source: str = None, cell_type: str = None) -> ToolResult:
        # Parse .ipynb JSON, modify cells, write back
```

## Task Tools

```python
class TaskCreateTool(Tool):
    name = "TaskCreate"
    description = "Create a structured task for tracking progress."
    category = PermissionCategory.META

class TaskUpdateTool(Tool):
    name = "TaskUpdate"
    description = "Update task status (pending → in_progress → completed)."
    category = PermissionCategory.META

class TaskListTool(Tool):
    name = "TaskList"
    description = "List all current tasks."
    category = PermissionCategory.META
```

## Tests (~15)

- Glob finds files matching pattern
- Glob sorts by modification time
- Grep finds content in files
- Grep supports context lines
- Grep multiline mode
- NotebookEdit modifies cell
- TaskCreate/TaskUpdate/TaskList round-trip
- Task state transitions enforced
