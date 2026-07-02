"""Phase 87: corpus v2 hygiene — broader refactor/edit coverage.

CI-safe only: no model calls. Validates the expanded corpus, the new fixtures,
and that the batchable task is self-contained.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from d2c.eval import EvalCorpus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS = EvalCorpus.load(PROJECT_ROOT / "eval" / "corpus.yaml")
FIXTURES = PROJECT_ROOT / "eval" / "fixtures"

_V2_IDS = {
    "v2-cross-file-rename-subtract",
    "v2-import-move-multiply",
    "v2-repeated-literal-update",
    "v2-targeted-line-fix-noise",
    "v2-add-edge-case-test",
    "v2-json-nested-edit",
    "v2-yaml-add-field",
    "v2-docs-code-paired-change",
    "v2-search-before-edit",
    "v2-noop-inspection",
    "v2-explain-add-function",
}


def test_corpus_grew_and_ids_unique():
    ids = [t.id for t in CORPUS.tasks]
    assert len(ids) >= 24
    assert len(ids) == len(set(ids))  # unique
    assert _V2_IDS <= set(ids)  # all v2 tasks present


def test_v2_prompts_nonempty():
    for task in CORPUS.tasks:
        if task.id in _V2_IDS:
            assert task.prompt.strip(), f"{task.id} has an empty prompt"


def test_v2_repo_paths_exist():
    for task in CORPUS.tasks:
        if task.id in _V2_IDS:
            assert (PROJECT_ROOT / task.repo).is_dir(), f"{task.id}: missing repo {task.repo}"


# ── new fixtures ────────────────────────────────────────────────────


def test_refactor_mini_fixture_repeats_literal_in_three_files():
    base = FIXTURES / "refactor-mini"
    assert "TIMEOUT = 30" in (base / "config.py").read_text()
    assert "30 seconds" in (base / "README.md").read_text()
    assert "TIMEOUT == 30" in (base / "tests" / "test_config.py").read_text()


def test_json_config_has_nested_logging():
    data = json.loads((FIXTURES / "json-config" / "config" / "app.json").read_text())
    assert data["logging"]["level"] == "info"  # nested key present + valid JSON


def test_settings_yaml_is_valid():
    data = yaml.safe_load((FIXTURES / "json-config" / "config" / "settings.yaml").read_text())
    assert isinstance(data, dict) and "service" in data


def test_reference_doc_typo_is_on_line_15():
    lines = (FIXTURES / "docs-site" / "docs" / "reference.md").read_text().splitlines()
    assert "termiate" in lines[14]  # 1-indexed line 15


def test_simple_cli_has_readme():
    assert (FIXTURES / "simple-cli" / "README.md").is_file()


# ── batchability ────────────────────────────────────────────────────


def test_batchable_tasks_have_self_contained_batch_prompt():
    batchable = [t for t in CORPUS.tasks if t.batchable]
    assert batchable, "expected at least one batchable v2 task"
    for task in batchable:
        # batch jobs can't read files, so the batch prompt must stand alone
        assert task.batch_prompt and task.batch_prompt.strip()


# ── advisory expectation keys stay within the known set ─────────────


def test_v2_expect_keys_are_known():
    raw = yaml.safe_load((PROJECT_ROOT / "eval" / "corpus.yaml").read_text())
    allowed = {
        "max_turns",
        "tools_used",
        "avoids",
        "preferred_tool",
        "tolerate_verification_failure",
    }
    for task in raw["tasks"]:
        expect = task.get("expect") or {}
        assert set(expect) <= allowed, f"{task['id']}: unknown expect keys {set(expect) - allowed}"
