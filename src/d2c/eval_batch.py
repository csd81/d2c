"""Phase 85: DeepSeek Batch API eval mode.

An OPTIONAL, cheaper eval path for *model-call* experiments — a single
prompt→response measurement per task, submitted through DeepSeek's
OpenAI-compatible Batch API. It is NOT a substitute for the live agent eval
(``d2c.eval``): Batch jobs run on the provider and cannot execute local tools
(Bash/Edit/ApplyPatch) or mutate fixtures, so only tasks explicitly marked
``batchable: true`` are submitted and the rest are recorded as skipped.

The HTTP client is isolated from the Anthropic-compatible agent loop and accepts
an injected transport/client so the whole flow is unit-testable without network.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from pydantic import BaseModel, Field

from d2c.eval import EvalCorpus, EvalTask
from d2c.observability import redact

# DeepSeek's Batch API is served on the OpenAI-compatible base URL (not the
# /anthropic one the agent loop uses).
DEEPSEEK_BATCH_BASE_URL = "https://api.deepseek.com"
_TERMINAL_STATUSES = frozenset({"completed", "failed", "expired", "cancelled"})
_MAX_ERROR_DETAIL = 200


# ── Result models ───────────────────────────────────────────────────


class BatchTaskResult(BaseModel):
    id: str
    status: str  # "succeeded" | "failed" | "skipped"
    reason: str | None = None
    output_text: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


class BatchSummary(BaseModel):
    mode: str = "batch"
    model: str
    submitted_count: int
    skipped_count: int
    succeeded_count: int
    failed_count: int
    batch_id: str | None = None
    duration_seconds: float | None = None
    # None = unknown/estimated: batch output may omit usage; we never fabricate.
    estimated_cost: float | None = None
    tasks: list[BatchTaskResult] = Field(default_factory=list)


# ── Request generation ──────────────────────────────────────────────


def build_batch_requests(
    corpus: EvalCorpus,
    *,
    model: str,
    max_tokens: int,
    system_prompt: str = "",
) -> tuple[list[dict[str, Any]], list[EvalTask]]:
    """Return (jsonl request dicts, skipped tasks). Deterministic: corpus order,
    stable ``custom_id`` = task id, no timestamps/randomness."""
    requests: list[dict[str, Any]] = []
    skipped: list[EvalTask] = []
    for task in corpus.tasks:
        if not task.batchable:
            skipped.append(task)
            continue
        prompt = (task.batch_prompt or task.prompt).strip()
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        requests.append(
            {
                "custom_id": task.id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {"model": model, "messages": messages, "max_tokens": max_tokens},
            }
        )
    return requests, skipped


def requests_to_jsonl(requests: list[dict[str, Any]]) -> str:
    """Deterministic JSONL (sorted keys) for the batch input file."""
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in requests)


# ── Output parsing ──────────────────────────────────────────────────


def _completion_text(body: dict[str, Any]) -> str:
    try:
        return str(body["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        return ""


def _sanitize_error(payload: Any) -> str:
    """Short, redacted error string — never the whole request/response body."""
    if isinstance(payload, dict):
        msg = (
            payload.get("message")
            or payload.get("error")
            or json.dumps(payload)[:_MAX_ERROR_DETAIL]
        )
    else:
        msg = str(payload)
    return str(redact(str(msg))).strip().replace("\n", " ")[:_MAX_ERROR_DETAIL]


def parse_batch_output(text: str) -> dict[str, dict[str, Any]]:
    """Map each output/error JSONL line to ``{custom_id: {...}}``.

    Success rows carry the completion text + usage; error rows carry a
    sanitized message. Handles both the ``response``/``error`` OpenAI batch
    shapes and malformed lines (skipped)."""
    by_id: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = rec.get("custom_id")
        if not isinstance(cid, str):
            continue
        response = rec.get("response")
        error = rec.get("error")
        status_code = response.get("status_code", 200) if isinstance(response, dict) else None
        if isinstance(response, dict) and not error and (status_code is None or status_code < 400):
            body = response.get("body") or {}
            usage = body.get("usage") or {}
            by_id[cid] = {
                "ok": True,
                "text": _completion_text(body),
                "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "output_tokens": int(usage.get("completion_tokens", 0) or 0),
            }
        else:
            detail = _sanitize_error(error or (response if response else "unknown batch error"))
            by_id[cid] = {"ok": False, "error": detail}
    return by_id


# ── Batch HTTP client ───────────────────────────────────────────────


class DeepSeekBatchClient:
    """Thin OpenAI-compatible Batch client (upload/create/poll/download).

    Pass a preconfigured ``httpx.Client`` (or one built on ``httpx.MockTransport``)
    to unit-test without network."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEEPSEEK_BATCH_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=60.0)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def upload_input(self, jsonl: bytes) -> str:
        r = self._client.post(
            f"{self._base}/v1/files",
            headers=self._headers(),
            files={"file": ("batch-input.jsonl", jsonl, "application/jsonl")},
            data={"purpose": "batch"},
        )
        r.raise_for_status()
        return str(r.json()["id"])

    def create_batch(self, input_file_id: str) -> dict[str, Any]:
        r = self._client.post(
            f"{self._base}/v1/batches",
            headers=self._headers(),
            json={
                "input_file_id": input_file_id,
                "endpoint": "/v1/chat/completions",
                "completion_window": "24h",
            },
        )
        r.raise_for_status()
        return dict(r.json())

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        r = self._client.get(f"{self._base}/v1/batches/{batch_id}", headers=self._headers())
        r.raise_for_status()
        return dict(r.json())

    def download_file(self, file_id: str) -> bytes:
        r = self._client.get(f"{self._base}/v1/files/{file_id}/content", headers=self._headers())
        r.raise_for_status()
        return r.content


# ── Orchestration ───────────────────────────────────────────────────


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str) + "\n", encoding="utf-8")


def run_batch_eval(
    corpus: EvalCorpus,
    out_dir: str | Path,
    *,
    model: str | None = None,
    max_tokens: int = 32_000,
    system_prompt: str = "",
    dry_run: bool = False,
    api_key: str | None = None,
    client: DeepSeekBatchClient | None = None,
    poll_interval: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> BatchSummary:
    """Generate + (optionally) submit a batch, poll to completion, and write
    per-task results + a summary under ``out_dir``. ``dry_run`` stops after
    writing the JSONL (no upload). Raises ValueError if a real run is requested
    without an API key."""
    from d2c.config import resolve_model

    resolved = resolve_model(model or "deepseek-v4-flash")
    out = Path(out_dir)
    (out / "tasks").mkdir(parents=True, exist_ok=True)

    requests, skipped = build_batch_requests(
        corpus, model=resolved, max_tokens=max_tokens, system_prompt=system_prompt
    )
    results: list[BatchTaskResult] = []
    for task in skipped:
        r = BatchTaskResult(
            id=task.id, status="skipped", reason="task requires local tool execution"
        )
        results.append(r)
        _write_json(out / "tasks" / f"{task.id}.json", r.model_dump(exclude_none=True))

    (out / "batch-input.jsonl").write_text(requests_to_jsonl(requests), encoding="utf-8")

    def _finish(
        succeeded: int, failed: int, batch_id: str | None, duration: float | None = None
    ) -> BatchSummary:
        summary = BatchSummary(
            model=resolved,
            submitted_count=len(requests),
            skipped_count=len(skipped),
            succeeded_count=succeeded,
            failed_count=failed,
            batch_id=batch_id,
            duration_seconds=duration,
            estimated_cost=None,  # batch usage may be absent; never fabricate
            tasks=results,
        )
        _write_json(out / "summary.json", summary.model_dump())
        return summary

    if dry_run or not requests:
        return _finish(0, 0, None)

    if not api_key:
        raise ValueError(
            "DEEPSEEK_API_KEY is required for a batch eval run "
            "(set it before running, or use --dry-run to only generate the JSONL)."
        )

    bc = client or DeepSeekBatchClient(api_key)
    started = time.monotonic()
    file_id = bc.upload_input(requests_to_jsonl(requests).encode("utf-8"))
    batch = bc.create_batch(file_id)
    batch_id = batch.get("id")
    _write_json(out / "batch-submit.json", batch)

    status = batch
    while status.get("status") not in _TERMINAL_STATUSES:
        sleep(poll_interval)
        status = bc.get_batch(str(batch_id))
    _write_json(out / "batch-status.json", status)

    by_id: dict[str, dict[str, Any]] = {}
    if status.get("output_file_id"):
        raw = bc.download_file(str(status["output_file_id"]))
        (out / "batch-output.jsonl").write_bytes(raw)
        by_id.update(parse_batch_output(raw.decode("utf-8", "replace")))
    if status.get("error_file_id"):
        raw = bc.download_file(str(status["error_file_id"]))
        (out / "batch-errors.jsonl").write_bytes(raw)
        by_id.update(parse_batch_output(raw.decode("utf-8", "replace")))

    succeeded = failed = 0
    for req in requests:
        tid = req["custom_id"]
        res = by_id.get(tid)
        if res is None:
            tr = BatchTaskResult(id=tid, status="failed", error="no batch result for task")
            failed += 1
        elif res.get("ok"):
            tr = BatchTaskResult(
                id=tid,
                status="succeeded",
                output_text=res.get("text", ""),
                input_tokens=res.get("input_tokens", 0),
                output_tokens=res.get("output_tokens", 0),
            )
            succeeded += 1
        else:
            tr = BatchTaskResult(id=tid, status="failed", error=res.get("error", "batch error"))
            failed += 1
        results.append(tr)
        _write_json(out / "tasks" / f"{tid}.json", tr.model_dump(exclude_none=True))

    return _finish(
        succeeded,
        failed,
        str(batch_id) if batch_id else None,
        duration=round(time.monotonic() - started, 3),
    )
