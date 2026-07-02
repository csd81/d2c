# Phase 85: DeepSeek Batch API eval mode

**Priority:** MEDIUM-HIGH (cheaper live evals, less traffic-control sensitivity)

## Context

Phases 81-84 closed the high-ROI DeepSeek runtime gaps:

- model surface/defaults
- thinking control
- official pricing/limits
- provider error UX

The remaining DeepSeek-doc opportunity is the Batch API. DeepSeek documents a
Batch API for asynchronous `/v1/chat/completions` jobs with lower cost and less
traffic-control sensitivity. The current `d2c eval` harness runs live tasks
through the normal agent loop, with subprocess-isolated fixture copies and
parallelism from Phase 67.

Batch mode is useful for model-behavior measurement and prompt/tool-policy
experiments, but it cannot directly execute local tools inside DeepSeek's batch
job. This phase should therefore add a clearly scoped optional eval mode rather
than replacing the current live eval runner.

## Goal

Add an optional DeepSeek Batch-backed eval workflow for batchable model-call
experiments while preserving the existing live subprocess eval path as the
default and source of truth for tool-execution behavior.

## Scope

In scope:

- `d2c eval --batch` or `d2c eval batch ...` entry point
- JSONL request generation for batchable eval tasks
- DeepSeek file upload / batch create / poll / output download client
- result parsing into `eval-results/` summaries
- clear skip/unsupported handling for tasks requiring local tool execution
- mocked tests only; no CI model calls
- docs and changelog note

Out of scope:

- replacing the normal live eval harness
- trying to execute local Bash/Edit/ApplyPatch tools inside provider batch jobs
- changing eval corpus semantics globally
- adding generic OpenAI provider support
- retry/backoff beyond simple polling/error reporting
- full release gate

## Design Constraint

DeepSeek Batch API can process model requests, not local agent side effects.

Therefore, split eval tasks into two categories:

```text
agent-live      existing d2c loop; can use tools and mutate fixture copy
model-batch     single model-call prompt/response measurement; no local tools
```

The existing corpus should continue to run as `agent-live` by default. Batch mode
should either:

1. run only tasks explicitly marked batchable, or
2. generate a clear skipped result for non-batchable tasks.

Do not pretend batch results are equivalent to full tool-using agent runs.

## Proposed CLI

Preferred:

```bash
python -m d2c eval eval/corpus.yaml --batch --out-dir eval-results/batch
```

Optional subcommands if cleaner:

```bash
python -m d2c eval batch eval/corpus.yaml --out-dir eval-results/batch
python -m d2c eval run eval/corpus.yaml --out-dir eval-results/live
```

Keep the current command unchanged:

```bash
python -m d2c eval eval/corpus.yaml --out-dir eval-results/live
```

## Corpus Metadata

Add optional metadata per task:

```yaml
batchable: true
batch_prompt: |
  ...
```

or a top-level batch section if that fits the current corpus parser better:

```yaml
batch:
  mode: model-call
```

Recommendation: start with `batchable: true` and reuse the normal task prompt
unless `batch_prompt` is provided.

Non-batchable tasks should produce a result with:

```json
{
  "status": "skipped",
  "reason": "task requires local tool execution"
}
```

## Batch Request Shape

Generate JSONL compatible with DeepSeek's documented Batch API for
`/v1/chat/completions`.

Each line should include:

```json
{
  "custom_id": "task-id",
  "method": "POST",
  "url": "/v1/chat/completions",
  "body": {
    "model": "deepseek-v4-flash",
    "messages": [
      {"role": "system", "content": "..."},
      {"role": "user", "content": "..."}
    ],
    "max_tokens": 32000
  }
}
```

If thinking is enabled for batch mode, include only the officially supported
OpenAI-compatible fields. Do not assume Anthropic `extra_body` maps directly to
Batch JSONL.

## Batch Client

Add a small DeepSeek Batch client, preferably isolated from the normal
Anthropic-compatible loop:

```text
src/d2c/eval_batch.py
```

Responsibilities:

- write JSONL input
- upload file
- create batch
- poll status
- download result/error file
- parse result lines
- emit summary JSON

Use the OpenAI-compatible DeepSeek base URL for Batch:

```text
https://api.deepseek.com
```

Keep auth on `DEEPSEEK_API_KEY`.

## Result Files

Write outputs under the selected `--out-dir`, for example:

```text
eval-results/batch/
  batch-input.jsonl
  batch-submit.json
  batch-status.json
  batch-output.jsonl
  summary.json
  tasks/
    <task-id>.json
```

Summary should include:

```text
mode: batch
model
submitted_count
skipped_count
succeeded_count
failed_count
batch_id
duration
estimated_cost
```

If exact usage fields are missing from batch output, mark cost as estimated or
unknown rather than fabricating precision.

## Files to Inspect / Modify

Likely:

```text
src/d2c/eval.py
src/d2c/main.py
src/d2c/config.py
src/d2c/usage.py
eval/corpus.yaml
eval/README.md
README.md
CHANGELOG.md
tests/test_eval.py
tests/test_eval_corpus.py
```

Optional:

```text
src/d2c/eval_batch.py
tests/test_phase85_eval_batch.py
eval/batch-corpus.yaml
```

## Tests

Add tests for:

1. corpus parser accepts optional `batchable` / `batch_prompt` fields.
2. JSONL generation is deterministic and uses stable `custom_id` values.
3. non-batchable tasks are skipped with a clear reason.
4. batch client constructs upload/create/poll/download requests correctly with a
   mocked HTTP transport.
5. completed batch output maps back to task results.
6. failed batch output maps to failed task results with provider error detail
   sanitized.
7. summary counts submitted/skipped/succeeded/failed correctly.
8. default `d2c eval` path remains the existing live runner.
9. missing `DEEPSEEK_API_KEY` fails clearly before attempting upload.
10. no tests perform real network/model calls.

## Verification

Fast only:

```bash
./scripts/check_fast.sh
python -m pytest tests/test_eval.py tests/test_eval_corpus.py tests/test_phase85_eval_batch.py
```

Manual dry run:

```bash
python -m d2c eval eval/corpus.yaml --batch --dry-run --out-dir eval-results/batch-dry
```

Optional live smoke only when explicitly requested and `DEEPSEEK_API_KEY` is set:

```bash
python -m d2c eval eval/batch-corpus.yaml --batch --out-dir eval-results/batch-live
```

Do not run the full release gate unless explicitly requested.

## Risks

| Risk | Mitigation |
| --- | --- |
| Batch cannot represent tool-using agent tasks | Mark tasks batchable explicitly; skip the rest |
| Results get compared to live eval incorrectly | Record `mode=batch` and document non-equivalence |
| Async polling makes tests flaky | Mock transport and clocks; keep live smoke manual |
| Provider API differs from docs | Isolate batch client and keep payload generation unit-tested |
| Cost fields are incomplete | Report unknown/estimated rather than precise values |

## Acceptance Criteria

- Existing `d2c eval` behavior is unchanged by default.
- Optional batch mode can generate deterministic JSONL requests.
- Non-batchable tasks are skipped explicitly.
- Batch upload/create/poll/download flow is covered by mocked tests.
- Batch results write a clear summary under `eval-results/`.
- Docs explain batch mode is for model-call evals, not full agent/tool evals.
- Fast checks pass.

## Expected Outcome

`d2c` gains a cheaper optional path for provider-side model-call evals without
weakening the existing live agent eval harness. The project can use Batch for
large prompt/model experiments while continuing to use the normal eval runner
when local tools, fixture mutation, or end-to-end agent behavior matter.
