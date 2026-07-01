# Phase 66: Headless eval harness for empirical tool-usage analysis

**Goal:** Answer questions like "does the model actually use ApplyPatch over
Edit for multi-file changes?" with data, not intuition. A CLI eval harness
runs d2c in headless mode against a corpus of task descriptions and produces
structured reports on tool-usage distributions, turn counts, and cost.

## What it is

A `d2c eval` subcommand that:
1. Reads a YAML corpus of task descriptions + optional ground-truth expectations
2. Runs each task through `d2c.sdk.D2CClient` in headless mode (no stdin)
3. Records every tool call, token usage, turn count, and outcome
4. Emits a structured JSON report per task and a summary across the corpus

## Corpus format

A YAML file with a list of tasks:

```yaml
tasks:
  - id: "add-error-handling"
    prompt: "Add try/except error handling to the database module in src/db.py"
    repo: "/tmp/eval-repos/python-project"
    expect:
      max_turns: 8
      tools_used: ["Read", "Edit"]
      avoids: ["Bash"]

  - id: "refactor-auth-middleware"
    prompt: "Extract the auth middleware into a separate package under src/middleware/"
    expect:
      max_turns: 15
      preferred_tool: "ApplyPatch"
```

The `expect` field is advisory — it doesn't fail the run, it flags divergence
in the summary report.

## What it measures per task

- **Turn count** — total model calls
- **Tool call distribution** — raw count per tool name
- **Token usage** — input, output, cache-read, estimated cost
- **Compaction events** — how many times compaction fired and which shaper
- **Outcome** — success (no error in final tool result) or failure
- **Tool sequence** — ordered list of tool names, for detecting patterns like
  Read→Grep→Read→Edit vs Read→ReplaceMany

## Output

Per-task JSON in `--out-dir`:

```json
{
  "id": "add-error-handling",
  "turns": 5,
  "tools": {"Read": 3, "Grep": 1, "Edit": 2, "Bash": 1},
  "input_tokens": 12500,
  "output_tokens": 3400,
  "cost_estimate": 0.008,
  "compactions": 0,
  "tool_sequence": ["Read", "Read", "Grep", "Edit", "Edit", "Bash"],
  "success": true,
  "divergences": ["expected <=8 turns, got 5"]
}
```

Plus a summary JSON with aggregate stats (mean/median turns, tool share
percentages, cost totals).

## What's intentionally not in v1

- No parallel execution (tasks run sequentially; the SDK session model makes
  concurrent runs messy)
- No model comparison mode (compare two models on the same corpus — that's v2)
- No pass/fail assertions against ground truth (flagged as divergences, not
  failures — the corpus is descriptive, not prescriptive)
- No REPL mode interaction (headless only — eval never prompts)

## Files to change

### 1. `src/d2c/__main__.py` — add `eval` subcommand

    python -m d2c eval corpus.yaml --out-dir ./eval-results

Parses the corpus, iterates tasks, delegates to the eval runner.

### 2. `src/d2c/eval.py` (new) — the eval runner

- `EvalCorpus` — YAML loader with validation (tasks must have id + prompt)
- `EvalTaskResult` — pydantic model for per-task output
- `EvalSummary` — aggregate stats across all tasks
- `run_eval(corpus, out_dir, config)` — the main loop: for each task,
  create/fork a session, run the prompt via `D2CClient`, collect tool events,
  write result JSON
- `run_task(config, prompt)` — single-task runner that instruments the loop
  events (tracks tool calls via `ToolExecutionEvent`)

### 3. `src/d2c/sdk.py` — optional: expose a richer event stream

If `D2CClient.run()` doesn't already yield sufficient metadata for eval
(tool names, token counts), add a thin wrapper or a `collect_events()` helper.
Check first — the loop already yields `TextDelta`, `ToolExecutionEvent`,
`StopEvent`, which may be enough.

### 4. `tests/test_eval.py`

- `EvalCorpus` parses valid YAML and rejects missing required fields
- `run_task` returns a result with the expected shape
- `EvalSummary` computes correct aggregates
- Optional: a smoke test with a trivial task against a tiny repo

## Usage example

```bash
# Build a corpus of 20 tasks
cat > eval-corpus.yaml << 'EOF'
tasks:
  - id: "greet"
    prompt: "Write a Python hello-world script"
    repo: "/tmp/eval-tmp"
EOF

# Run it
python -m d2c eval eval-corpus.yaml --out-dir ./eval-results

# Quick summary
cat ./eval-results/summary.json | python -m json.tool
```

## How this answers the original question

After building a 20-50 task corpus (mixing single-file edits, multi-file
refactors, bug fixes, and exploratory tasks), run it once and look at the
tool distribution:

    ApplyPatch:  3  (6%)
    Edit:       38  (76%)
    Write:       8  (16%)
    ReplaceMany: 1  (2%)

If ApplyPatch is at single-digit percentages, the model isn't reaching for it.
You can then iterate: tweak the system prompt, improve the tool description,
or add example invocations to the schema — and re-run the same corpus to see
if the numbers shift. That's the whole point.

## Why start here instead of MultiEdit

Building MultiEdit is a guess. Building the eval harness is a measurement
apparatus. With the harness, you can:

1. Measure the baseline (does the model use ApplyPatch? When? Why not?)
2. Test interventions (better prompt guidance, schema examples, reordering)
3. Decide with data whether MultiEdit is needed — or whether better prompting
   for ApplyPatch closes the gap

If the answer turns out to be "DeepSeek just won't use ApplyPatch but would
use MultiEdit," you've already got the harness to verify the fix.
