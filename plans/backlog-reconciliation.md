# Backlog Reconciliation (updated after Phase 66)

## Summary

71 plan documents now exist under `plans/`. `plans/master_plan.md` remains historical intent; the
current truth is the source, tests, `COMPARISON.md`, `CHANGELOG.md`, and this updated reconciliation.

Phases 51-66 implemented the entire Phase 50 "recommended next phases" list and several follow-on
items: more tools, session and persistent approvals, prompt-injection hardening, full-src mypy,
usage accounting, TUI permission polish, WebSearch provider expansion, SDK/server surfaces, scoped
settings, subagent profiles, bubblewrap sandboxing, context-economy ReadRange, and the eval harness.

**Verified pre-update HEAD state:** worktree was clean before this documentation refresh; last 20
commits cover Phases 48-66 plus one multi-tool request bug fix. Local gates are green: `pytest`
1463 passed / 1 skipped, `ruff check .`, `ruff format --check .`, and `mypy`. Tool pool assembles
29 built-in tools + dynamic MCP.

## Method

- Re-read the old Phase 50 backlog, newer phase plans, commit history, tool pool, CLI surface, and
  package config.
- Verified candidates against `src/d2c/`, `tests/`, `.github/workflows/ci.yml`, and runtime checks.
- Classified old open items as implemented, deferred, obsolete, or still candidate.
- No runtime code changed in this update; this is a documentation/backlog refresh.

## Implemented / resolved

| Area | Evidence |
|---|---|
| Phase 50 recommended backlog completed | commits `eb7d9f2` through `b5bded7`; tests below |
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
| Multi-tool malformed follow-up request fix | commit `8196a17`; `src/d2c/loop.py`, `main.py`; `tests/test_loop.py` |
| macOS CI leg and full gate workflow | `.github/workflows/ci.yml` |

## Still open - candidates

| Item | Current status | ROI (UV/SI/PF/T/E/R -> score) | Recommendation | Evidence |
|---|---|---|---|---|
| Eval harness v2: model comparison and optional assertions | Phase 66 v1 is sequential and descriptive | 4/1/2/5/3/2 -> **7** | **Keep - next measurement work** | `plans/phase66-eval-harness.md`; `src/d2c/eval.py` |
| Build an actual eval corpus and run baseline reports | Harness exists, corpus/results are not part of repo | 5/1/2/5/2/2 -> **9** | **Keep - highest value next step** | `python -m d2c eval ...`; no checked-in corpus |
| Use eval data to tune `ApplyPatch`/edit tool descriptions | Measurement apparatus exists; prompt/schema tuning not yet data-driven | 4/1/3/4/2/2 -> **8** | **Keep after baseline corpus** | Phase 66 rationale |
| Remaining tool breadth toward paper's ~54 | 29 built-ins; gap mostly browser/platform/product tools | 2/1/3/3/4/3 -> **2** | **Defer unless eval shows need** | `plans/tool-inventory.md`; runtime pool count 29 |
| KAIROS background heartbeat mode | Module/config exist but not instantiated | 2/1/2/2/5/4 -> **-2** | **Defer** | `src/d2c/kairos.py`; no loop/main wiring |
| Windows-native OS sandbox backend | Process fallback + Linux bubblewrap exist | 2/3/1/3/5/4 -> **0** | **Research only if Windows support becomes a goal** | `src/d2c/sandbox.py`; Phase 62 scope |
| Lifecycle hooks with no runtime source | 27 defined, 19 fired, 8 intentionally inactive | 1/1/2/2/4/3 -> **-1** | **Defer** | `tests/test_phase40_hooks.py`; `COMPARISON.md` |
| Browser/screenshot/computer-use tools | Not built-in | 2/1/2/2/5/4 -> **-2** | **Prefer MCP integration** | `plans/tool-inventory.md`; `COMPARISON.md` |
| Approval management UX | Persistent approvals exist, but no `/approvals` list/reset command | 3/2/1/4/2/2 -> **6** | **Keep small** | `src/d2c/approvals.py`; Phase 64 out-of-scope |

## Deferred intentionally

| Item | Reason |
|---|---|
| `bubble` permission mode | Subagent escalation is handled through capability profiles; adding `bubble` would increase risk without much product value. |
| KAIROS heartbeat | Paper flags it as feature-gated / uncertain; implementation exists as scaffolding but not active. |
| Native Windows sandbox | High effort and hard to validate in this repo; process fallback and Linux bubblewrap cover current scope. |
| OpenTelemetry/exporter | Local JSONL audit logging covers current observability needs. |
| Docker image / binary distribution | `pip install` + wheel build remain the intended distribution path. |
| PyPI publishing automation | Release workflow builds artifacts; upload remains manual by design. |
| Bundled plugin ecosystem | Loader exists; shipping first-party plugins is content/product work, not architecture work. |
| Browser/computer-use tools | Better delivered through MCP where a browser runtime can be managed independently. |

## Obsolete / dropped

| Item | Reason |
|---|---|
| Phase 50 recommendations for Phases 51-53 | Implemented in Phases 51-53 and extended by Phases 54-66. |
| "Tavily only" WebSearch gap | Closed by Brave and SearXNG providers in Phase 58. |
| "Session-only approvals" as the final approval model | Superseded by Phase 64 persistent exact-match approvals and Phase 65 UX. |
| `plans/master_plan.md` as current truth | Historical blueprint only; current source and tests supersede it. |
| Coarse `permission_decision` audit event | Replaced by granular permission ask/approval/denial events. |

## Recommended next phases

1. **Phase 67 - Eval corpus and baseline report.** Add a small checked-in corpus of deterministic
   fixture tasks, run the Phase 66 harness, and document baseline tool-use/cost/turn metrics.
2. **Phase 68 - Eval-guided tool prompt/schema tuning.** Use the baseline to improve `ApplyPatch`,
   `ReplaceMany`, `ReadRange`, and inspection-tool descriptions, then compare results.
3. **Phase 69 - Approval management command.** Add a small `/approvals` or CLI command to list count,
   show storage path, and reset persistent approvals without asking users to edit files manually.

Lower-priority follow-ons: eval model comparison mode, optional pass/fail assertions for corpora,
Windows sandbox research, and any tool breadth that eval data shows is genuinely missing.
