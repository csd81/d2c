# Phase 22: Global Prompt History

**Paper Reference:** Section 9.1 — "Global prompt history: User prompts only, stored in
history.jsonl at the Claude configuration home directory."

**Priority:** LOW

## Rationale

Enables Up-arrow / ctrl+r navigation in interactive mode, matching the paper's
"history.ts → makeHistoryReader() yields entries in reverse order." This is a quality-
of-life feature for interactive CLI users.

## Files to Create/Modify

1. CREATE `src/d2c/history.py` — global prompt history management
2. MODIFY `src/d2c/main.py` — integrate history into interactive REPL

## Key Design

```python
class PromptHistory:
    """Global prompt history, stored as JSONL in ~/.d2c/history.jsonl."""

    def __init__(self, base_dir: Path):
        self.history_path = base_dir / "history.jsonl"

    def append(self, prompt: str, metadata: dict | None = None) -> None:
        entry = {
            "prompt": prompt,
            "timestamp": _utc_now(),
            "cwd": str(Path.cwd()),
            "metadata": metadata or {},
        }
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def read_reverse(self, limit: int = 1000) -> list[dict]:
        """Read most recent entries in reverse order (for ctrl+r)."""
        if not self.history_path.exists():
            return []
        # readLinesReverse equivalent
        ...

    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Fuzzy search history for ctrl+r navigation."""
        ...
```

## Integration

```python
# In run_interactive():
history = PromptHistory(Path.home() / ".d2c")
while True:
    try:
        prompt_text = input("> ").strip()
        if prompt_text:
            history.append(prompt_text)
    except (EOFError, KeyboardInterrupt):
        break
```

## Tests (~5)

- History append and readback
- read_reverse returns most recent first
- Search finds matching prompts
- Empty history → no errors
- Large history limited in search results
