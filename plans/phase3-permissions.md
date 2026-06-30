# Phase 3: Permission System

## Files

- `src/d2c/permissions.py` — PermissionMode, Rule, PermissionEngine, authorize()
- `tests/test_permissions.py`

## Key Types

- `PermissionMode`: PLAN, DEFAULT, ACCEPT_EDITS, DONT_ASK
- `PermissionDecision`: ALLOW, DENY, ASK
- `RuleType`: DENY, ALLOW
- `Rule`: rule_type, pattern, reason, matches()
- `PermissionRequest`: tool_name, tool_input, tool_category, session_id
- `PermissionResult`: decision, reason, modified_input
- `PermissionEngine`: mode, rules, evaluate()

## Authorization Pipeline

1. PreToolUse hook → can deny/ask or modify input
2. Deny-first rule evaluation (deny always wins)
3. If ASK and interactive → prompt user
4. If DENY → fire PermissionDenied hook for retry guidance

## Mode Behavior

| Mode | Read | Write | Shell | Meta |
|---|---|---|---|---|
| plan | ASK | ASK | ASK | ASK |
| default | ASK | ASK | ASK | ASK |
| acceptEdits | ALLOW | ALLOW | safe-only | ASK |
| dontAsk | ALLOW | ALLOW | ALLOW | ALLOW |

Safe shell commands in acceptEdits: mkdir, rmdir, touch, rm, mv, cp, sed, ls, cat

## Edge Cases

- Deny rule matches under dontAsk → still DENY
- No rules match under default → ASK
- acceptEdits + dangerous shell → ASK
- PreToolUse hook modifies input → modified_input passed through
- User denies → denial reason to model as routing signal
- PermissionDenied hook → retry guidance injected
