# Phase 14: Remaining Permission Modes (Auto + BypassPermissions)

**Paper Reference:** Section 5 — "up to seven permission modes" (we have 4 of 7)

**Priority:** MEDIUM

## Rationale

The paper specifies 7 modes, with Auto being the ML-classifier-based mode that enables
higher autonomy with safety. BypassPermissions is the internal "trust the operator" mode.
We currently have: plan, default, acceptEdits, dontAsk.

Missing modes:
- **Auto**: ML classifier (yoloClassifier.ts) — two-stage fast-filter + chain-of-thought
- **BypassPermissions**: Skips most prompts but safety-critical checks remain
- **Bubble** (internal-only): Escalates to parent terminal

## Files to Create/Modify

1. MODIFY `src/d2c/permissions.py` — add Auto, BypassPermissions modes
2. CREATE `src/d2c/permissions/classifier.py` — auto-mode safety classifier

## Auto Mode

```python
class AutoClassifier:
    """
    Paper: "two-stage fast-filter and chain-of-thought evaluation of tool safety."

    Stage 1 (fast-filter): Heuristic check — is this tool+input combination
    in the known-safe category? (e.g., Read on a text file)

    Stage 2 (CoT): Call a fast model (Haiku) with the tool name and input,
    asking it to classify safety. Returns: safe/unsafe/review.
    """

    async def evaluate(self, request: PermissionRequest) -> PermissionResult:
        fast = self._fast_filter(request)
        if fast is not None:
            return fast
        return await self._cot_classify(request)

    def _fast_filter(self, request: PermissionRequest) -> PermissionResult | None:
        if request.tool_category == PermissionCategory.READ:
            if request.tool_name in ("Read", "Glob", "Grep"):
                return PermissionResult(PermissionDecision.ALLOW, reason="safe read")
        if request.tool_name == "Bash":
            cmd = request.tool_input.get("command", "")
            if cmd.strip().startswith("rm -rf /"):
                return PermissionResult(PermissionDecision.DENY, reason="destructive")
        return None  # Needs CoT evaluation

    async def _cot_classify(self, request: PermissionRequest) -> PermissionResult: ...
```

## BypassPermissions Mode

```python
def _mode_default(self, request):
    if self.mode == PermissionMode.BYPASS:
        if self._is_safety_critical(request):
            return PermissionResult(PermissionDecision.ASK,
                                    reason="safety-critical despite bypass")
        return PermissionResult(PermissionDecision.ALLOW)
```

## Integration

Update `PermissionEngine.evaluate()` to handle new modes.
Update CLI with `--permission-mode auto` and `--permission-mode bypass`.

## Edge Cases

- Auto classifier model unavailable → fall back to default/ask mode
- CoT classifier times out → deny (safety conservative)
- Bypass mode still checks deny rules (deny always wins)
- Fast-filter false positive → CoT stage catches it

## Tests (~12)

- Auto mode fast-filter approves safe reads
- Auto mode fast-filter denies destructive commands
- Auto mode CoT classifier called for ambiguous cases
- CoT classifier result respected
- Bypass mode auto-approves non-critical operations
- Bypass mode still asks for safety-critical operations
- Bypass mode still enforces deny rules
- Auto classifier timeout → deny
- Auto classifier model error → fallback
