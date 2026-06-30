# Phase 5: Compaction Pipeline

## Files

- `src/d2c/compact.py` — applyContextShapers(), applyBudgetReduction(), autoCompact(), buildPostCompactMessages()
- `tests/test_compact.py`

## Pipeline (2 of 5 layers from paper)

1. Budget reduction — cap individual tool results at configurable size (default 30K chars)
2. Auto-compact — when context still exceeds pressure threshold (85%), call model to produce compressed summary, mark compact_boundary in transcript

## Key Types

- `CompactConfig`: tool_result_max_chars, pressure_threshold, context_window_tokens, chars_per_token

## Edge Cases

- Messages under threshold after budget reduction → skip auto-compact
- Compaction model fails → keep original, continue with overflow risk
- Very short conversation (< 4 messages) → skip
- Summary exceeds budget → second-pass truncation
