# Phase 1: Tool Base & Built-in Tools

## Files

- `src/d2c/tools/__init__.py`
- `src/d2c/tools/base.py` — Tool ABC, ToolResult, ToolUse, PermissionCategory
- `src/d2c/tools/pool.py` — assembleToolPool(), filterToolsByDenyRules()
- `src/d2c/tools/read_tool.py` — FileReadTool
- `src/d2c/tools/write_tool.py` — FileWriteTool
- `src/d2c/tools/edit_tool.py` — FileEditTool
- `src/d2c/tools/bash_tool.py` — BashTool
- `tests/conftest.py`
- `tests/test_tools.py`

## Key Types

- `ToolResult`: output, attachments, error, metadata
- `ToolUse`: id, name, input, timestamp
- `PermissionCategory`: READ, WRITE, SHELL, META
- `Tool` (ABC): name, description, input_schema, category, is_concurrent_safe, execute(), is_enabled(), to_api_format()

## Edge Cases

| Tool | Condition | Behavior |
|---|---|---|
| Read | File not found | Error ToolResult |
| Read | Binary/PDF/image | Handle appropriately |
| Read | File > limit | Truncated + total size |
| Write | No prior Read | Error |
| Edit | old_string not unique | Error with context |
| Edit | old_string not found | Error |
| Bash | Timeout | Error with partial output |
| Bash | Non-zero exit | error=True, include stderr |
