"""Headless eval harness (Phase 66).

Runs a YAML corpus of task prompts through ``d2c.sdk.D2CClient`` in headless
mode, one task at a time, and records tool-usage distribution, turn counts,
token/cost, compaction activity, and outcome per task. This answers
empirical questions ("does the model actually reach for ApplyPatch over
Edit?") with data instead of intuition — see plans/phase66-eval-harness.md.

Tasks run sequentially (not in parallel — the SDK session model makes
concurrent runs messy) and the corpus's ``expect`` field is advisory: it
never fails a task, it only flags a divergence in the result.
"""

from __future__ import annotations

import json
import os
import statistics
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from d2c.loop import StopEvent, TextResponse, ToolExecutionEvent

# Audit event names that represent one compaction shaper actually firing.
# "compaction_shaper_applied" covers snip/microcompact/context_collapse
# (compact.py's _audit_shaper_applied); "compaction_end" covers the
# last-resort model-generated auto-compact (compact.py::autoCompact).
_COMPACTION_EVENTS = {"compaction_shaper_applied", "compaction_end"}


# ── Corpus ──────────────────────────────────────────────────────────


class EvalExpectation(BaseModel):
    """Advisory ground-truth for a task. Never fails a run — only flags
    divergences in the result."""

    max_turns: int | None = None
    tools_used: list[str] = Field(default_factory=list)
    avoids: list[str] = Field(default_factory=list)
    preferred_tool: str | None = None
    # Phase 68: some tasks verify by running a broader check (e.g. the whole
    # test suite) that can fail for reasons unrelated to the task — a fixture
    # that ships a known-failing test, say. When set, a run whose *only*
    # reason to be marked failed is that trailing verification tool error is
    # instead counted as successful, and the swallowed error is surfaced as a
    # note (never hidden). Real errors — exceptions, no model call, a
    # mid-sequence tool error followed by more work — are unaffected.
    tolerate_verification_failure: bool = False


class EvalTask(BaseModel):
    id: str
    prompt: str
    repo: str | None = None
    expect: EvalExpectation | None = None


class EvalCorpus(BaseModel):
    tasks: list[EvalTask]

    @classmethod
    def load(cls, path: str | Path) -> "EvalCorpus":
        raw = yaml.safe_load(Path(path).read_text())
        if not isinstance(raw, dict) or "tasks" not in raw:
            raise ValueError(f"Corpus {path} must be a mapping with a top-level 'tasks' list")
        return cls.model_validate(raw)


# ── Results ─────────────────────────────────────────────────────────


class EvalTaskResult(BaseModel):
    id: str
    turns: int
    tools: dict[str, int]
    input_tokens: int
    output_tokens: int
    cost_estimate: float
    compactions: int
    tool_sequence: list[str]
    success: bool
    divergences: list[str]
    error: str | None = None
    # Phase 68: non-fatal observations (e.g. a tolerated trailing verification
    # failure). Unlike divergences, these are not expectation mismatches, so
    # they are kept out of the divergence count.
    notes: list[str] = Field(default_factory=list)


class EvalSummary(BaseModel):
    task_count: int
    success_count: int
    mean_turns: float
    median_turns: float
    tool_totals: dict[str, int]
    tool_share: dict[str, float]
    total_input_tokens: int
    total_output_tokens: int
    total_cost_estimate: float
    total_compactions: int
    total_divergences: int


# ── Trust resolution ────────────────────────────────────────────────


def _resolve_trust_for_task(cwd: Path, *, trust: bool | None) -> None:
    """Headless trust resolution for one eval task's repo.

    Mirrors main.py's ``_resolve_trust`` headless branch (no interactive
    prompt): --trust/--no-trust win outright, otherwise a previously
    trusted workspace stays trusted, otherwise deny (matches the safe
    default for non-interactive contexts).
    """
    from d2c.trust import TrustStore, WorkSpaceTrustGate, set_trust_gate

    store = TrustStore()
    gate = WorkSpaceTrustGate(cwd, store)
    if trust is True:
        gate.decide(True)
    elif trust is False:
        gate.decide(False)
    else:
        gate.decide(store.is_trusted(cwd))
    set_trust_gate(gate)


# ── Compaction counting ─────────────────────────────────────────────


def _count_compactions(audit_path: str | Path) -> int:
    path = Path(audit_path)
    if not path.exists():
        return 0
    count = 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event") in _COMPACTION_EVENTS:
            count += 1
    return count


# ── Divergence checking ─────────────────────────────────────────────


def _check_divergence(task: EvalTask, turns: int, tools: Counter[str]) -> list[str]:
    expect = task.expect
    if expect is None:
        return []
    divergences: list[str] = []
    if expect.max_turns is not None and turns > expect.max_turns:
        divergences.append(f"expected <={expect.max_turns} turns, got {turns}")
    for name in expect.tools_used:
        if tools.get(name, 0) == 0:
            divergences.append(f"expected tool '{name}' to be used, but it wasn't")
    for name in expect.avoids:
        if tools.get(name, 0) > 0:
            divergences.append(f"expected to avoid '{name}', but it was used {tools[name]}x")
    if expect.preferred_tool is not None and tools.get(expect.preferred_tool, 0) == 0:
        divergences.append(
            f"expected preferred tool '{expect.preferred_tool}' to be used, but it wasn't"
        )
    return divergences


# ── Task runner ─────────────────────────────────────────────────────


async def run_task(
    task: EvalTask,
    *,
    model: str | None = None,
    permission_mode: str = "bypass",
    max_turns: int = 25,
    trust: bool | None = None,
) -> EvalTaskResult:
    """Run a single eval task headlessly and record what happened.

    Instruments the loop events yielded by ``D2CClient.run()`` for tool
    calls, reads the global usage tracker afterward for token/cost totals
    (D2CClient.run() sets it but never clears it), and scopes a temporary
    audit log to the run to count compaction events fired.
    """
    from d2c.sdk import D2CClient
    from d2c.usage import get_usage_tracker

    cwd = Path(task.repo).resolve() if task.repo else Path.cwd()
    _resolve_trust_for_task(cwd, trust=trust)

    client = D2CClient(cwd=cwd, model=model, permission_mode=permission_mode, max_turns=max_turns)

    tool_sequence: list[str] = []
    tools: Counter[str] = Counter()
    last_tool_error = False
    last_tool_error_name: str | None = None
    last_tool_error_output: str = ""
    saw_tool = False
    error: str | None = None

    audit_fd, audit_path = tempfile.mkstemp(prefix="d2c-eval-audit-", suffix=".jsonl")
    os.close(audit_fd)
    prev_audit_enabled = os.environ.get("D2C_AUDIT_LOG")
    prev_audit_path = os.environ.get("D2C_AUDIT_LOG_PATH")
    os.environ["D2C_AUDIT_LOG"] = "1"
    os.environ["D2C_AUDIT_LOG_PATH"] = audit_path
    try:
        async for event in client.run(task.prompt):
            if isinstance(event, ToolExecutionEvent):
                saw_tool = True
                last_tool_error = bool(event.result.error)
                if last_tool_error:
                    last_tool_error_name = event.tool_use.name
                    last_tool_error_output = event.result.output or ""
                else:
                    last_tool_error_name = None
                    last_tool_error_output = ""
                tool_sequence.append(event.tool_use.name)
                tools[event.tool_use.name] += 1
            elif isinstance(event, TextResponse):
                pass
            elif isinstance(event, StopEvent):
                pass
    except Exception as exc:  # noqa: BLE001 — a task failure must not abort the corpus
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if prev_audit_enabled is None:
            os.environ.pop("D2C_AUDIT_LOG", None)
        else:
            os.environ["D2C_AUDIT_LOG"] = prev_audit_enabled
        if prev_audit_path is None:
            os.environ.pop("D2C_AUDIT_LOG_PATH", None)
        else:
            os.environ["D2C_AUDIT_LOG_PATH"] = prev_audit_path

    compactions = _count_compactions(audit_path)
    try:
        Path(audit_path).unlink(missing_ok=True)
    except OSError:
        pass

    tracker = get_usage_tracker()
    session = tracker.session if tracker is not None else None
    turns = session.calls if session is not None else 0
    input_tokens = session.input_tokens if session is not None else 0
    output_tokens = session.output_tokens if session is not None else 0
    cost_estimate = float(session.estimated_cost_usd) if session is not None else 0.0

    # turns == 0 means no model call was ever recorded (e.g. a missing
    # API key short-circuits before the loop's first call) — that's a
    # failed run even though there's no tool error to point to.
    success = error is None and turns > 0 and not (saw_tool and last_tool_error)
    divergences = _check_divergence(task, turns, tools)
    notes: list[str] = []

    # Phase 68: with error is None and turns > 0, the *only* thing that can
    # make success False is a trailing tool error — so this branch is
    # exactly the "verification failed" case and nothing else.
    trailing_error_only = (
        not success and error is None and turns > 0 and saw_tool and last_tool_error
    )
    if (
        trailing_error_only
        and task.expect is not None
        and task.expect.tolerate_verification_failure
    ):
        success = True
        snippet = " ".join(last_tool_error_output.split())[:200]
        note = f"tolerated trailing {last_tool_error_name or 'tool'} verification failure"
        if snippet:
            note += f": {snippet}"
        notes.append(note)

    return EvalTaskResult(
        id=task.id,
        turns=turns,
        tools=dict(tools),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_estimate=cost_estimate,
        compactions=compactions,
        tool_sequence=tool_sequence,
        success=success,
        divergences=divergences,
        error=error,
        notes=notes,
    )


# ── Corpus runner ────────────────────────────────────────────────────


def compute_summary(results: list[EvalTaskResult]) -> EvalSummary:
    if not results:
        return EvalSummary(
            task_count=0,
            success_count=0,
            mean_turns=0.0,
            median_turns=0.0,
            tool_totals={},
            tool_share={},
            total_input_tokens=0,
            total_output_tokens=0,
            total_cost_estimate=0.0,
            total_compactions=0,
            total_divergences=0,
        )

    turns_list = [r.turns for r in results]
    tool_totals: Counter[str] = Counter()
    for r in results:
        tool_totals.update(r.tools)
    total_tool_calls = sum(tool_totals.values())
    tool_share = {
        name: round(count / total_tool_calls * 100, 2) if total_tool_calls else 0.0
        for name, count in tool_totals.items()
    }

    return EvalSummary(
        task_count=len(results),
        success_count=sum(1 for r in results if r.success),
        mean_turns=round(statistics.mean(turns_list), 2),
        median_turns=round(statistics.median(turns_list), 2),
        tool_totals=dict(tool_totals),
        tool_share=tool_share,
        total_input_tokens=sum(r.input_tokens for r in results),
        total_output_tokens=sum(r.output_tokens for r in results),
        total_cost_estimate=round(sum(r.cost_estimate for r in results), 6),
        total_compactions=sum(r.compactions for r in results),
        total_divergences=sum(len(r.divergences) for r in results),
    )


async def run_eval(
    corpus: EvalCorpus,
    out_dir: str | Path,
    *,
    model: str | None = None,
    permission_mode: str = "bypass",
    max_turns: int = 25,
    trust: bool | None = None,
) -> EvalSummary:
    """Run every task in the corpus sequentially, writing a per-task JSON
    report and a summary.json into out_dir. Returns the summary."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    results: list[EvalTaskResult] = []
    for task in corpus.tasks:
        result = await run_task(
            task,
            model=model,
            permission_mode=permission_mode,
            max_turns=max_turns,
            trust=trust,
        )
        results.append(result)
        payload: dict[str, Any] = result.model_dump(exclude_none=True)
        (out_path / f"{task.id}.json").write_text(json.dumps(payload, indent=2) + "\n")

    summary = compute_summary(results)
    (out_path / "summary.json").write_text(json.dumps(summary.model_dump(), indent=2) + "\n")
    return summary
