"""Phase 67: eval corpus and fixture hygiene checks.

These validate corpus shape only (unique IDs, fixture paths exist,
advisory `expect` keys are known) — no live model calls, so this runs in
normal CI. Actually running the corpus against DeepSeek is a manual
`python -m d2c eval eval/corpus.yaml --trust` (see eval/README.md).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from d2c.eval import EvalCorpus

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = PROJECT_ROOT / "eval" / "corpus.yaml"
README_PATH = PROJECT_ROOT / "eval" / "README.md"

_ALLOWED_EXPECT_KEYS = {
    "max_turns",
    "tools_used",
    "avoids",
    "preferred_tool",
    "tolerate_verification_failure",
}


def test_corpus_parses_through_eval_corpus_loader():
    corpus = EvalCorpus.load(CORPUS_PATH)
    assert len(corpus.tasks) >= 10


def test_task_ids_are_unique():
    corpus = EvalCorpus.load(CORPUS_PATH)
    ids = [task.id for task in corpus.tasks]
    assert len(ids) == len(set(ids))


def test_every_task_repo_path_exists():
    corpus = EvalCorpus.load(CORPUS_PATH)
    for task in corpus.tasks:
        assert task.repo, f"task {task.id} has no repo"
        repo_path = (PROJECT_ROOT / task.repo).resolve()
        assert repo_path.is_dir(), f"task {task.id}: repo path missing: {repo_path}"


def test_fixture_repos_contain_a_source_file():
    corpus = EvalCorpus.load(CORPUS_PATH)
    fixture_repos = {
        (PROJECT_ROOT / task.repo).resolve() for task in corpus.tasks if task.repo != "."
    }
    assert fixture_repos, "expected at least one fixture repo in the corpus"
    for repo_path in fixture_repos:
        files = [p for p in repo_path.rglob("*") if p.is_file()]
        assert files, f"fixture repo has no files: {repo_path}"


def test_advisory_expectation_keys_are_allowed():
    raw = yaml.safe_load(CORPUS_PATH.read_text())
    for task in raw["tasks"]:
        expect = task.get("expect")
        if not expect:
            continue
        unknown = set(expect) - _ALLOWED_EXPECT_KEYS
        assert not unknown, f"task {task['id']} has unknown expect keys: {unknown}"


def test_readme_documents_the_run_command():
    text = README_PATH.read_text()
    assert "python -m d2c eval" in text
