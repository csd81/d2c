# Phase 8: Subagent Delegation

## Files

- `src/d2c/subagent.py` — SubagentType, SubagentDefinition, SubagentResult, spawn_subagent(), build_subagent_tool_pool()
- `src/d2c/tools/agent_tool.py` — AgentTool (meta-tool)
- `tests/test_subagent.py`

## Built-in Subagent Types

| Type | Tool Restriction |
|---|---|
| Explore | Read/search only (Write/Edit/Bash denied) |
| Plan | Standard tools; creates plans for user approval |
| General-purpose | Full capability |

## Isolation Architecture

Each subagent:
- Gets its own conversation context (no parent history inheritance)
- Has restricted tool subset (allowlist/denylist)
- Writes to separate sidechain JSONL transcript
- Returns only final summary text to parent (not full history)

## Custom Subagents

Defined via `.d2c/agents/*.md` with YAML frontmatter: description, tools, disallowedTools, model, permissionMode, maxTurns, background.

## Edge Cases

- Subagent model call fails → error summary to parent
- Subagent hits max turns → partial summary
- Background subagent → fire-and-forget
- Custom agent file malformed → parse error
