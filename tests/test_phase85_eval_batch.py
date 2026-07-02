"""Phase 85: DeepSeek Batch API eval mode. All HTTP is mocked — no network."""

from __future__ import annotations

import json

import httpx
import pytest
import yaml

from d2c.eval import EvalCorpus, EvalTask
from d2c.eval_batch import (
    DeepSeekBatchClient,
    build_batch_requests,
    parse_batch_output,
    requests_to_jsonl,
    run_batch_eval,
)

_SUCCESS_LINE = json.dumps(
    {
        "custom_id": "t1",
        "response": {
            "status_code": 200,
            "body": {
                "choices": [{"message": {"content": "hello there"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        },
        "error": None,
    }
)
_ERROR_LINE = json.dumps(
    {"custom_id": "t2", "response": {"status_code": 400}, "error": {"message": "bad request"}}
)


def _corpus() -> EvalCorpus:
    return EvalCorpus(
        tasks=[
            EvalTask(id="t1", prompt="say hi", batchable=True),
            EvalTask(id="t2", prompt="do x", batchable=True, batch_prompt="custom batch prompt"),
            EvalTask(id="t3", prompt="edit a file", repo="."),  # not batchable
        ]
    )


# ── corpus metadata ─────────────────────────────────────────────────


def test_corpus_accepts_batch_fields(tmp_dir):
    p = tmp_dir / "c.yaml"
    p.write_text(
        yaml.dump({"tasks": [{"id": "a", "prompt": "p", "batchable": True, "batch_prompt": "bp"}]})
    )
    task = EvalCorpus.load(p).tasks[0]
    assert task.batchable is True
    assert task.batch_prompt == "bp"

    p2 = tmp_dir / "c2.yaml"
    p2.write_text(yaml.dump({"tasks": [{"id": "b", "prompt": "p"}]}))
    task2 = EvalCorpus.load(p2).tasks[0]
    assert task2.batchable is False  # default
    assert task2.batch_prompt is None


# ── request generation ──────────────────────────────────────────────


def test_build_requests_skips_non_batchable_and_is_deterministic():
    reqs, skipped = build_batch_requests(_corpus(), model="deepseek-v4-flash", max_tokens=32_000)
    assert [r["custom_id"] for r in reqs] == ["t1", "t2"]  # corpus order, stable ids
    assert [t.id for t in skipped] == ["t3"]
    assert reqs[0]["url"] == "/v1/chat/completions"
    assert reqs[0]["method"] == "POST"
    assert reqs[0]["body"]["model"] == "deepseek-v4-flash"
    assert reqs[0]["body"]["max_tokens"] == 32_000
    # batch_prompt overrides prompt
    assert reqs[1]["body"]["messages"][-1]["content"] == "custom batch prompt"
    # deterministic serialization
    assert requests_to_jsonl(reqs) == requests_to_jsonl(reqs)


def test_system_prompt_is_prepended():
    reqs, _ = build_batch_requests(_corpus(), model="m", max_tokens=100, system_prompt="be terse")
    assert reqs[0]["body"]["messages"][0] == {"role": "system", "content": "be terse"}


# ── output parsing ──────────────────────────────────────────────────


def test_parse_output_success_and_error():
    by_id = parse_batch_output(_SUCCESS_LINE + "\n" + _ERROR_LINE)
    assert by_id["t1"]["ok"] is True
    assert by_id["t1"]["text"] == "hello there"
    assert by_id["t1"]["input_tokens"] == 5
    assert by_id["t1"]["output_tokens"] == 3
    assert by_id["t2"]["ok"] is False
    assert "bad request" in by_id["t2"]["error"]


def test_parse_output_sanitizes_error_detail():
    line = json.dumps(
        {
            "custom_id": "t",
            "response": None,
            "error": {"message": "leaked DEEPSEEK_API_KEY=sk-abc123DEF456ghi789"},
        }
    )
    by_id = parse_batch_output(line)
    assert "sk-abc123DEF456ghi789" not in by_id["t"]["error"]
    assert "[REDACTED]" in by_id["t"]["error"]


def test_parse_output_skips_malformed_lines():
    by_id = parse_batch_output("not json\n" + _SUCCESS_LINE + "\n{}\n")
    assert set(by_id) == {"t1"}


# ── HTTP client (mocked transport) ──────────────────────────────────


def _mock_client(handler) -> DeepSeekBatchClient:
    return DeepSeekBatchClient(
        "test-key", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_client_upload_create_get_download_shapes():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer test-key"
        if request.url.path == "/v1/files":
            return httpx.Response(200, json={"id": "file-in-1"})
        if request.url.path == "/v1/batches":
            body = json.loads(request.content)
            assert body["input_file_id"] == "file-in-1"
            assert body["endpoint"] == "/v1/chat/completions"
            return httpx.Response(200, json={"id": "batch-1", "status": "validating"})
        if request.url.path == "/v1/batches/batch-1":
            return httpx.Response(200, json={"id": "batch-1", "status": "completed"})
        if request.url.path == "/v1/files/file-out-1/content":
            return httpx.Response(200, content=b"line")
        return httpx.Response(404)

    bc = _mock_client(handler)
    assert bc.upload_input(b"x") == "file-in-1"
    assert bc.create_batch("file-in-1")["id"] == "batch-1"
    assert bc.get_batch("batch-1")["status"] == "completed"
    assert bc.download_file("file-out-1") == b"line"
    assert ("POST", "/v1/files") in seen and ("POST", "/v1/batches") in seen


# ── full orchestration ──────────────────────────────────────────────


def _full_flow_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/files":
        return httpx.Response(200, json={"id": "file-in-1"})
    if path == "/v1/batches":
        return httpx.Response(200, json={"id": "batch-1", "status": "validating"})
    if path == "/v1/batches/batch-1":
        return httpx.Response(
            200, json={"id": "batch-1", "status": "completed", "output_file_id": "file-out-1"}
        )
    if path == "/v1/files/file-out-1/content":
        return httpx.Response(200, content=(_SUCCESS_LINE + "\n" + _ERROR_LINE).encode())
    return httpx.Response(404)


def test_full_batch_flow_maps_results_and_counts(tmp_dir):
    out = tmp_dir / "batch"
    summary = run_batch_eval(
        _corpus(),
        out,
        model="flash",
        api_key="test-key",
        client=_mock_client(_full_flow_handler),
        sleep=lambda _s: None,
        poll_interval=0,
    )

    assert summary.mode == "batch"
    assert summary.model == "deepseek-v4-flash"
    assert summary.submitted_count == 2
    assert summary.skipped_count == 1
    assert summary.succeeded_count == 1
    assert summary.failed_count == 1
    assert summary.batch_id == "batch-1"
    assert summary.estimated_cost is None  # never fabricated

    # files written
    assert (out / "batch-input.jsonl").exists()
    assert (out / "batch-output.jsonl").exists()
    assert (out / "summary.json").exists()

    t1 = json.loads((out / "tasks" / "t1.json").read_text())
    assert t1["status"] == "succeeded" and t1["output_text"] == "hello there"
    t2 = json.loads((out / "tasks" / "t2.json").read_text())
    assert t2["status"] == "failed" and "bad request" in t2["error"]
    t3 = json.loads((out / "tasks" / "t3.json").read_text())
    assert t3["status"] == "skipped" and "tool execution" in t3["reason"]


def test_dry_run_writes_jsonl_without_network(tmp_dir):
    out = tmp_dir / "dry"
    summary = run_batch_eval(_corpus(), out, model="flash", dry_run=True)
    assert summary.submitted_count == 2
    assert summary.skipped_count == 1
    assert summary.succeeded_count == 0
    lines = [x for x in (out / "batch-input.jsonl").read_text().splitlines() if x]
    assert len(lines) == 2  # two batchable tasks
    assert json.loads((out / "tasks" / "t3.json").read_text())["status"] == "skipped"


def test_missing_api_key_fails_before_upload(tmp_dir):
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        run_batch_eval(_corpus(), tmp_dir / "m", api_key=None, dry_run=False)


# ── CLI parser / default path ───────────────────────────────────────


def test_eval_parser_has_batch_flags():
    import d2c.main as m

    args = m._build_eval_parser().parse_args(["corpus.yaml"])
    assert args.batch is False
    assert args.dry_run is False
    args2 = m._build_eval_parser().parse_args(["corpus.yaml", "--batch", "--dry-run"])
    assert args2.batch is True and args2.dry_run is True
