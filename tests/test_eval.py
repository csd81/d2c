"""Phase 66: headless eval harness (d2c.eval).

Mocks the model client the same way test_phase59_sdk.py does — D2CClient
always runs with stream=True, so .messages.stream() is mocked rather than
.messages.create().
"""

from __future__ import annotations

from functools import partial
from unittest.mock import patch

import pytest
import yaml

import d2c.sdk as sdk
from d2c.eval import (
    EvalCorpus,
    EvalTask,
    EvalTaskResult,
    compute_summary,
    run_eval,
    run_task,
)
from d2c.persistence import SessionManager
from tests.test_phase59_sdk import _mock_stream_client, _text_response, _tool_use_response


@pytest.fixture(autouse=True)
def _tmp_sessions(tmp_dir, monkeypatch):
    """Point D2CClient's SessionManager at a temp dir, not the real ~/.d2c."""
    monkeypatch.setattr(sdk, "SessionManager", partial(SessionManager, base_dir=tmp_dir))
    return tmp_dir


# ── EvalCorpus ──────────────────────────────────────────────────────


def test_corpus_loads_valid_yaml(tmp_dir):
    corpus_path = tmp_dir / "corpus.yaml"
    corpus_path.write_text(
        yaml.dump(
            {
                "tasks": [
                    {"id": "greet", "prompt": "Write a hello-world script"},
                    {
                        "id": "add-errors",
                        "prompt": "Add error handling",
                        "repo": "/tmp/some-repo",
                        "expect": {"max_turns": 8, "tools_used": ["Read", "Edit"]},
                    },
                ]
            }
        )
    )
    corpus = EvalCorpus.load(corpus_path)
    assert len(corpus.tasks) == 2
    assert corpus.tasks[0].id == "greet"
    assert corpus.tasks[1].expect.max_turns == 8
    assert corpus.tasks[1].expect.tools_used == ["Read", "Edit"]


def test_corpus_rejects_missing_required_fields(tmp_dir):
    corpus_path = tmp_dir / "corpus.yaml"
    corpus_path.write_text(yaml.dump({"tasks": [{"id": "no-prompt"}]}))
    with pytest.raises(Exception):
        EvalCorpus.load(corpus_path)


def test_corpus_rejects_missing_tasks_key(tmp_dir):
    corpus_path = tmp_dir / "corpus.yaml"
    corpus_path.write_text(yaml.dump({"not_tasks": []}))
    with pytest.raises(ValueError):
        EvalCorpus.load(corpus_path)


# ── run_task ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_task_text_only_result_shape(tmp_dir):
    task = EvalTask(id="greet", prompt="say hi", repo=str(tmp_dir))

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client([_text_response("hello there")])
        result = await run_task(task, trust=True)

    assert isinstance(result, EvalTaskResult)
    assert result.id == "greet"
    assert result.turns == 1
    assert result.tools == {}
    assert result.tool_sequence == []
    assert result.success is True
    assert result.divergences == []
    assert result.compactions == 0
    assert result.error is None
    assert result.input_tokens >= 0
    assert result.output_tokens >= 0


@pytest.mark.asyncio
async def test_run_task_records_tool_calls(tmp_dir):
    f = tmp_dir / "notes.txt"
    f.write_text("secret plan")
    task = EvalTask(id="read-file", prompt="read notes.txt", repo=str(tmp_dir))

    responses = [
        _tool_use_response("tu1", "Read", {"file_path": str(f)}),
        _text_response("Found: secret plan"),
    ]

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client(responses)
        result = await run_task(task, trust=True)

    assert result.tools == {"Read": 1}
    assert result.tool_sequence == ["Read"]
    assert result.turns == 2
    assert result.success is True


@pytest.mark.asyncio
async def test_run_task_fails_when_last_tool_result_errors(tmp_dir):
    task = EvalTask(id="bad-read", prompt="read missing.txt", repo=str(tmp_dir))

    responses = [
        _tool_use_response("tu1", "Read", {"file_path": str(tmp_dir / "missing.txt")}),
        _text_response("couldn't find it"),
    ]

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client(responses)
        result = await run_task(task, trust=True)

    assert result.tools == {"Read": 1}
    assert result.success is False


def test_expectation_parses_tolerate_verification_failure(tmp_dir):
    corpus_path = tmp_dir / "corpus.yaml"
    corpus_path.write_text(
        yaml.dump(
            {
                "tasks": [
                    {
                        "id": "t",
                        "prompt": "p",
                        "expect": {"tolerate_verification_failure": True},
                    }
                ]
            }
        )
    )
    corpus = EvalCorpus.load(corpus_path)
    assert corpus.tasks[0].expect.tolerate_verification_failure is True


def test_expectation_defaults_tolerate_verification_failure_false():
    from d2c.eval import EvalExpectation

    assert EvalExpectation().tolerate_verification_failure is False


@pytest.mark.asyncio
async def test_run_task_tolerates_trailing_verification_failure(tmp_dir):
    # A trailing tool error, with tolerate_verification_failure set, flips the
    # run back to success while surfacing the swallowed error as a note.
    task = EvalTask(
        id="tolerant",
        prompt="read missing.txt",
        repo=str(tmp_dir),
        expect={"tolerate_verification_failure": True},
    )
    responses = [
        _tool_use_response("tu1", "Read", {"file_path": str(tmp_dir / "missing.txt")}),
        _text_response("couldn't find it"),
    ]

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client(responses)
        result = await run_task(task, trust=True)

    assert result.success is True
    assert result.notes  # the failure is surfaced, not hidden
    assert any("Read" in n for n in result.notes)
    assert result.divergences == []  # a note, not a divergence


@pytest.mark.asyncio
async def test_run_task_without_tolerance_still_fails_and_no_note(tmp_dir):
    task = EvalTask(id="strict", prompt="read missing.txt", repo=str(tmp_dir))
    responses = [
        _tool_use_response("tu1", "Read", {"file_path": str(tmp_dir / "missing.txt")}),
        _text_response("couldn't find it"),
    ]

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client(responses)
        result = await run_task(task, trust=True)

    assert result.success is False
    assert result.notes == []


@pytest.mark.asyncio
async def test_tolerance_does_not_rescue_no_model_call(tmp_dir, monkeypatch):
    # Tolerance is narrow: it only forgives a *trailing tool* error, never a
    # run that never called the model (turns == 0).
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    task = EvalTask(
        id="nokey",
        prompt="hi",
        repo=str(tmp_dir),
        expect={"tolerate_verification_failure": True},
    )

    result = await run_task(task, trust=True)

    assert result.success is False
    assert result.notes == []


@pytest.mark.asyncio
async def test_run_task_flags_max_turns_divergence(tmp_dir):
    task = EvalTask(
        id="slow",
        prompt="do a thing",
        repo=str(tmp_dir),
        expect={"max_turns": 1},
    )
    f = tmp_dir / "a.txt"
    f.write_text("x")

    responses = [
        _tool_use_response("tu1", "Read", {"file_path": str(f)}),
        _text_response("done"),
    ]

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client(responses)
        result = await run_task(task, trust=True)

    assert result.turns == 2
    assert any("turns" in d for d in result.divergences)


@pytest.mark.asyncio
async def test_run_task_flags_avoided_tool_divergence(tmp_dir):
    f = tmp_dir / "a.txt"
    f.write_text("x")
    task = EvalTask(
        id="no-bash",
        prompt="read a file",
        repo=str(tmp_dir),
        expect={"avoids": ["Read"]},
    )

    responses = [
        _tool_use_response("tu1", "Read", {"file_path": str(f)}),
        _text_response("done"),
    ]

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client(responses)
        result = await run_task(task, trust=True)

    assert any("avoid 'Read'" in d for d in result.divergences)


@pytest.mark.asyncio
async def test_run_task_flags_missing_preferred_tool_divergence(tmp_dir):
    task = EvalTask(
        id="wants-applypatch",
        prompt="say hi",
        repo=str(tmp_dir),
        expect={"preferred_tool": "ApplyPatch"},
    )

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client([_text_response("hi")])
        result = await run_task(task, trust=True)

    assert any("ApplyPatch" in d for d in result.divergences)


@pytest.mark.asyncio
async def test_run_task_never_raises_on_no_api_key(tmp_dir, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    task = EvalTask(id="no-key", prompt="hi", repo=str(tmp_dir))

    result = await run_task(task, trust=True)

    assert result.success is False
    assert result.turns == 0


# ── compute_summary ─────────────────────────────────────────────────


def test_compute_summary_aggregates():
    results = [
        EvalTaskResult(
            id="a",
            turns=2,
            tools={"Read": 1, "Edit": 1},
            input_tokens=100,
            output_tokens=50,
            cost_estimate=0.001,
            compactions=0,
            tool_sequence=["Read", "Edit"],
            success=True,
            divergences=[],
        ),
        EvalTaskResult(
            id="b",
            turns=4,
            tools={"Read": 2},
            input_tokens=200,
            output_tokens=80,
            cost_estimate=0.002,
            compactions=1,
            tool_sequence=["Read", "Read"],
            success=False,
            divergences=["expected <=3 turns, got 4"],
        ),
    ]
    summary = compute_summary(results)
    assert summary.task_count == 2
    assert summary.success_count == 1
    assert summary.mean_turns == 3.0
    assert summary.median_turns == 3.0
    assert summary.tool_totals == {"Read": 3, "Edit": 1}
    assert summary.tool_share["Read"] == 75.0
    assert summary.tool_share["Edit"] == 25.0
    assert summary.total_input_tokens == 300
    assert summary.total_output_tokens == 130
    assert round(summary.total_cost_estimate, 3) == 0.003
    assert summary.total_compactions == 1
    assert summary.total_divergences == 1


def test_compute_summary_empty():
    summary = compute_summary([])
    assert summary.task_count == 0
    assert summary.mean_turns == 0.0
    assert summary.tool_totals == {}
    assert summary.tool_share == {}


# ── run_eval ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_eval_writes_per_task_and_summary_json(tmp_dir):
    corpus = EvalCorpus(
        tasks=[
            EvalTask(id="t1", prompt="say hi", repo=str(tmp_dir)),
            EvalTask(id="t2", prompt="say bye", repo=str(tmp_dir)),
        ]
    )
    out_dir = tmp_dir / "results"

    with patch("d2c.loop.anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = _mock_stream_client([_text_response("ok")])
        summary = await run_eval(corpus, out_dir, trust=True)

    assert summary.task_count == 2
    assert (out_dir / "t1.json").exists()
    assert (out_dir / "t2.json").exists()
    assert (out_dir / "summary.json").exists()

    import json

    t1 = json.loads((out_dir / "t1.json").read_text())
    assert t1["id"] == "t1"
    assert t1["success"] is True
    assert "error" not in t1  # exclude_none — no error on a clean run


# ── CLI dispatch ────────────────────────────────────────────────────


def test_build_eval_parser_parses_flags(tmp_dir):
    import d2c.main as m

    args = m._build_eval_parser().parse_args(
        [
            "corpus.yaml",
            "--out-dir",
            str(tmp_dir / "out"),
            "--model",
            "deepseek-chat",
            "--permission-mode",
            "default",
            "--max-turns",
            "5",
            "--trust",
        ]
    )
    assert str(args.corpus) == "corpus.yaml"
    assert args.out_dir == tmp_dir / "out"
    assert args.model == "deepseek-chat"
    assert args.permission_mode == "default"
    assert args.max_turns == 5
    assert args.trust is True
    assert args.no_trust is False


def test_run_eval_cli_errors_on_missing_corpus(tmp_dir, capsys):
    import d2c.main as m

    code = m._run_eval_cli([str(tmp_dir / "does-not-exist.yaml")])
    assert code == 1
    assert "Error loading corpus" in capsys.readouterr().err


def test_run_eval_cli_errors_on_empty_corpus(tmp_dir, capsys):
    import d2c.main as m

    corpus_path = tmp_dir / "empty.yaml"
    corpus_path.write_text(yaml.dump({"tasks": []}))
    code = m._run_eval_cli([str(corpus_path)])
    assert code == 1
    assert "no tasks" in capsys.readouterr().err


def test_main_dispatches_eval_subcommand_without_touching_repl(monkeypatch, tmp_dir):
    import d2c.main as m

    called = {"eval": None, "headless": False, "interactive": False}
    monkeypatch.setattr(m, "_run_eval_cli", lambda argv: called.__setitem__("eval", argv) or 0)
    monkeypatch.setattr(m, "run_headless", lambda *a, **k: called.__setitem__("headless", True))
    monkeypatch.setattr(
        m, "run_interactive", lambda *a, **k: called.__setitem__("interactive", True)
    )
    monkeypatch.setattr(
        "sys.argv", ["d2c", "eval", "corpus.yaml", "--out-dir", str(tmp_dir / "out")]
    )

    with pytest.raises(SystemExit) as exc_info:
        m.main()

    assert exc_info.value.code == 0
    assert called["eval"] == ["corpus.yaml", "--out-dir", str(tmp_dir / "out")]
    assert called == {"eval": called["eval"], "headless": False, "interactive": False}
