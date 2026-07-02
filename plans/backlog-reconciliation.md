# Backlog Reconciliation (updated after Phase 71)

## Summary

76 plan documents now exist under `plans/`. `plans/master_plan.md` remains historical intent; the
current truth is the source, tests, `COMPARISON.md`, `CHANGELOG.md`, and this updated reconciliation.

Phases 51-71 implemented the entire Phase 50 "recommended next phases" list and a long run of
follow-on work: more tools, session and persistent approvals, prompt-injection hardening, full-src
mypy, usage accounting, TUI permission polish, WebSearch provider expansion, SDK/server surfaces,
scoped settings, subagent profiles, bubblewrap sandboxing, context-economy ReadRange, the eval
harness plus a checked-in corpus and measured baseline, the first eval-guided tool-tuning pass, a
local quality gate (replacing GitHub CI), and REPL management UX for approvals and subagent profiles.

**Verified pre-update HEAD state:** worktree was clean before this documentation refresh; last
commits cover Phases 67-71 plus the CI removal and a tests-package import fix. There is no automatic
GitHub CI anymore — the gate is local and two-tier (`scripts/check_fast.sh` for the inner loop,
`scripts/check_release.sh` before push/release). Phase 71's fast checks are green: `ruff check .`,
`ruff format --check .`, `mypy` (71 source files clean), and the targeted test suites. Tool pool
assembles 29 built-in tools + dynamic MCP (Phases 67-71 added commands/harness/scripts, no new tools).

## Method

- Re-read the newer phase plans (67-71), commit history, tool pool, CLI/REPL surface, and package
  config.
- Verified candidates against `src/d2c/`, `tests/`, and runtime checks (CI config is gone; the
  local quality-gate scripts are the reference for the gate now).
- Classified old open items as implemented, deferred, obsolete, or still candidate.
- No runtime code changed in this update; this is a documentation/backlog refresh.

## Implemented / resolved

| Area | Evidence |
|---|---|
| Phase 50 recommended backlog completed | Phases 51-66; tests below |
| Tool breadth batch 2: `ApplyPatch`, `EnvInfo` | `src/d2c/tools/apply_patch.py`, `env_info.py`; `tests/test_phase51_tools.py` |
| Session-scoped approvals | `src/d2c/approvals.py`, `main.py`; `tests/test_phase52_approvals.py` |
| Prompt-injection hardening | `src/d2c/untrusted.py`, `context.py`, `web_fetch.py`, `web_search.py`; `tests/test_security_regressions.py` |
| Full-src mypy coverage | `pyproject.toml` has `files = ["src/d2c"]`; `mypy` passes |
| Cost/token accounting UI | `src/d2c/usage.py`, `/usage` in `main.py`; `tests/test_usage.py` |
| Tool breadth batch 3: `ConfigInfo`, `PackageInfo`, `CodeSymbols` | `src/d2c/tools/config_info.py`, `package_info.py`, `code_symbols.py`; `tests/test_phase56_tools.py` |
| TUI/statusline/permission prompt polish | `src/d2c/main.py`; `tests/test_phase57_ui_polish.py`, `test_repl_ux.py` |
| WebSearch provider expansion | `src/d2c/tools/web_search.py`; `tests/test_phase58_websearch_providers.py` |
| Python SDK and local HTTP server | `src/d2c/sdk.py`, `server.py`; `tests/test_phase59_sdk.py`, `test_phase59_server.py` |
| Scoped settings and managed policy | `src/d2c/settings.py`, `config.py`, `doctor.py`; `tests/test_phase60_settings.py` |
| Subagent capability profiles | `src/d2c/subagent_profiles.py`, `subagent.py`, `tools/agent_tool.py`; `tests/test_phase61_profiles.py` |
| Linux bubblewrap sandbox backend | `src/d2c/sandbox.py`, `doctor.py`; `tests/test_phase62_sandbox.py` |
| Context economy / `ReadRange` | `src/d2c/tools/read_range_tool.py`, `context.py`; `tests/test_phase63_readrange.py` |
| Persistent cross-session approvals | `src/d2c/approvals.py`, `main.py`; `tests/test_phase64_approvals.py` |
| Interactive TUI permission dialog | `src/d2c/main.py`; `tests/test_phase65_ui_dialog.py` |
| Headless eval harness | `src/d2c/eval.py`, `main.py`; `tests/test_eval.py` |
| Eval corpus + measured baseline (Phase 67) | `eval/corpus.yaml`, `eval/fixtures/`, `eval/baseline.md`; `tests/test_eval_corpus.py` |
| Eval-guided tool tuning (Phase 68) | `expect.tolerate_verification_failure` in `src/d2c/eval.py`, `ApplyPatch` description; `eval/phase68-results.md`; `tests/test_phase51_tools.py` |
| Local two-tier quality gate (Phase 69) | `scripts/check_fast.sh`, `scripts/check_release.sh`; README/CONTRIBUTING |
| Approval management UX `/approvals` (Phase 70) | `src/d2c/approvals.py`, `main.py`; `tests/test_phase70_approvals_ux.py` |
| Subagent profile UX `/profiles` (Phase 71) | `src/d2c/main.py`; `tests/test_phase71_profiles_ux.py` |
| Multi-tool malformed follow-up request fix | commit `8196a17`; `src/d2c/loop.py`, `main.py`; `tests/test_loop.py` |

## Still open - candidates

| Item | Current status | ROI (UV/SI/PF/T/E/R -> score) | Recommendation | Evidence |
|---|---|---|---|---|
| Eval harness v2: model comparison and optional assertions | Phase 66 v1 is sequential/descriptive; corpus + baseline now exist (67/68) | 4/1/2/5/3/2 -> **7** | **Keep - top next measurement work** | `plans/phase66-eval-harness.md`; `src/d2c/eval.py`; `eval/` |
| Grow the eval corpus (more multi-file-edit tasks) | 13 tasks; ApplyPatch signal rests on one rename task | 4/1/2/5/2/2 -> **8** | **Keep - cheap, strengthens tuning** | `eval/phase68-results.md` recommendation |
| Further eval-guided tuning (worked ApplyPatch example, ReplaceMany/ReadRange) | Phase 68 moved ApplyPatch 0/6 -> 3/6; not yet saturated | 4/1/3/4/2/2 -> **8** | **Keep after corpus growth** | `eval/phase68-results.md` next-lever note |
| Remaining tool breadth toward paper's ~54 | 29 built-ins; gap mostly browser/platform/product tools | 2/1/3/3/4/3 -> **2** | **Defer unless eval shows need** | `plans/tool-inventory.md`; runtime pool count 29 |
| KAIROS background heartbeat mode | Module/config exist but not instantiated | 2/1/2/2/5/4 -> **-2** | **Defer** | `src/d2c/kairos.py`; no loop/main wiring |
| Windows-native OS sandbox backend | Process fallback + Linux bubblewrap exist | 2/3/1/3/5/4 -> **0** | **Research only if Windows support becomes a goal** | `src/d2c/sandbox.py`; Phase 62 scope |
| Lifecycle hooks with no runtime source | 27 defined, 19 fired, 8 intentionally inactive | 1/1/2/2/4/3 -> **-1** | **Defer** | `tests/test_phase40_hooks.py`; `COMPARISON.md` |
| Browser/screenshot/computer-use tools | Not built-in | 2/1/2/2/5/4 -> **-2** | **Prefer MCP integration** | `plans/tool-inventory.md`; `COMPARISON.md` |

## Deferred intentionally

| Item | Reason |
|---|---|
| `bubble` permission mode | Subagent escalation is handled through capability profiles; adding `bubble` would increase risk without much product value. |
| KAIROS heartbeat | Paper flags it as feature-gated / uncertain; implementation exists as scaffolding but not active. |
| Native Windows sandbox | High effort and hard to validate in this repo; process fallback and Linux bubblewrap cover current scope. |
| OpenTelemetry/exporter | Local JSONL audit logging covers current observability needs. |
| Docker image / binary distribution | `pip install` + wheel build remain the intended distribution path. |
| PyPI publishing automation | `release.yml` builds artifacts; upload remains manual by design. |
| Automatic GitHub CI | Removed in Phase 69 (noisy/red while local was green); replaced by the local two-tier quality gate. Not planned to return. |
| Bundled plugin ecosystem | Loader exists; shipping first-party plugins is content/product work, not architecture work. |
| Browser/computer-use tools | Better delivered through MCP where a browser runtime can be managed independently. |

## Obsolete / dropped

| Item | Reason |
|---|---|
| Phase 50 recommendations for Phases 51-53 | Implemented in Phases 51-53 and extended by Phases 54-71. |
| "Tavily only" WebSearch gap | Closed by Brave and SearXNG providers in Phase 58. |
| "Session-only approvals" as the final approval model | Superseded by Phase 64 persistent exact-match approvals, Phase 65 UX, and Phase 70 management commands. |
| Approval management UX as an open item | Delivered in Phase 70 (`/approvals` status/clear-session/reset). |
| Eval corpus / baseline / tool-tuning as open items | Delivered in Phases 67-68 (`eval/corpus.yaml`, `eval/baseline.md`, `eval/phase68-results.md`). |
| `.github/workflows/ci.yml` as reference/evidence | CI removed in Phase 69; the local quality-gate scripts are the reference now. |
| `plans/master_plan.md` as current truth | Historical blueprint only; current source and tests supersede it. |
| Coarse `permission_decision` audit event | Replaced by granular permission ask/approval/denial events. |

## Recommended next phases

1. **Eval harness v2 — model comparison + optional assertions.** Extend the harness to run two
   models/configs over the same corpus and diff tool-use/turns/cost, and allow opt-in pass/fail
   assertions per task (kept separate from the advisory `expect`/`divergences` flow).
2. **Grow the eval corpus.** Add a few more multi-file-edit and refactor tasks so the
   ApplyPatch-vs-Edit/ReplaceMany signal isn't resting on a single rename task, then re-baseline.
3. **Second eval-guided tuning pass.** Use the larger corpus to add a short worked `ApplyPatch`
   example and revisit `ReplaceMany`/`ReadRange` guidance; keep changes only if they move the metrics.

Lower-priority follow-ons: Windows sandbox research, KAIROS activation, and any tool breadth that
eval data shows is genuinely missing.
