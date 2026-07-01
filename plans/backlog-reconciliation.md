# Backlog Reconciliation (Phase 50)

## Summary

54 phase plans exist under `plans/`. Phases 1–33 built the architecture; Phases 34–49 closed the
"last-mile wiring", safety, ops, and packaging gaps surfaced by `COMPARISON.md`. This document
reconciles the deferred / out-of-scope / optional items from those plans against the **actual source
and tests** (verified, not assumed) and produces a ranked backlog.

**State of the project:** full CI gate suite green — ruff, ruff-format, mypy (staged), bandit,
pytest (1138 passed / 1 skipped), build, twine check. 23 built-in tools + MCP. Versioned/releasable.

## Method

- Extracted "Out of scope / Deferred / Optional / Known limitation" items from every plan file.
- Verified each candidate against `src/d2c/` and `tests/` with grep/imports (evidence cited).
- Classified: implemented · deferred · obsolete · candidate. Scored candidates
  `priority = user_value + safety_impact + paper_fidelity + testability − effort − risk` (1–5 each),
  then applied judgement.
- No runtime code changed in this phase (markdown only).

## Implemented / resolved (Phases 34–49)

| Area | Evidence |
|---|---|
| Read-before-Write gate (+ canonicalization/symlink) | `tools/read_tool.py`, `write_tool.py`; `tests/test_phase34.py`, `test_security_regressions.py` |
| File-history checkpoints / `--rewind-files` | `file_history.py`, `main.py`; `tests/test_phase34.py`, `test_phase37.py` |
| Output-token recovery | `loop.py`; `tests/test_loop_output_recovery.py` |
| Compaction flag split | `loop.py` (`has_attempted_proactive_compact`) |
| Sandbox wired to BashTool (`D2C_SANDBOX`) | `tools/pool.py`, `config.py`; `tests/test_phase37.py` |
| Hook events (19/27 fired) + FILE_CHANGED/INSTRUCTIONS_LOADED | `tools/__init__.py`, `main.py`; `tests/test_phase40_hooks.py` |
| Path-scoped rules enforced | `permissions/__init__.py`; `tests/test_phase37.py` |
| Auto-memory (`Remember`), background status (`AgentStatus`) | `tools/memory_tool.py`, `background_status.py`; `tests/test_phase34.py` |
| Real slash commands + multi-turn REPL | `main.py`; `tests/test_repl_commands.py` |
| Shell permission hardening (structural acceptEdits) | `permissions/classifier.py`; `tests/test_phase38.py` |
| Fail-closed permission gate + interactive ASK + granular audit | `loop.py`, `streaming_executor.py`, `mcp/server.py`; `tests/test_phase43_ask_permissions.py`, `test_phase49_ask_permissions.py` |
| Real WebSearch (Tavily) | `tools/web_search.py`; `tests/test_web_search.py` (live-verified) |
| Tool breadth 17→23 (git/fs/structured-edit) | `tools/git_tools.py`, `fs_tools.py`, `structured_edit.py`; `tests/test_phase41_tools.py` |
| Observability / audit logging (redacted) | `observability.py`; `tests/test_observability.py` |
| Security regression suite + `docs/security.md` | `tests/test_security_regressions.py` |
| CI quality gates | `.github/workflows/ci.yml`, `pyproject.toml` |
| Doctor diagnostics (`--doctor`) | `doctor.py`; `tests/test_doctor.py` |
| Versioning + packaging + release checklist | `__init__.py`, `pyproject.toml`, `CHANGELOG.md`, `docs/release.md`, `.github/workflows/release.yml`; `tests/test_version.py` |

## Still open — candidates (verified)

| Item | Status | ROI (UV/SI/PF/T/E/R → score) | Recommendation | Evidence |
|---|---|---|---|---|
| More built-in tools toward paper's ~54 (e.g. MultiEdit/ApplyPatch, EnvInfo) | 23 built-ins | 4/2/4/4/3/2 → **9** | **Keep — next** | `plans/tool-inventory.md`; pool has 23 |
| Persistent "always allow" approvals (session-scoped) | Phase 49 is one-shot only | 4/3/3/4/3/3 → **8** | **Keep** | `main.py interactive_approval`; Phase 49 out-of-scope |
| Secondary WebSearch providers (Brave/SerpAPI/SearXNG) | Tavily only | 4/1/2/4/2/1 → **8** | **Keep** | `web_search.py: _PROVIDERS={"tavily"}` |
| Prompt-injection hardening (delimit tool/web content as untrusted in context) | carried-as-data, no active delimiting | 3/4/3/3/3/3 → **7** | **Research → Keep** | `tests/test_security_regressions.py` (data-not-action) |
| Multi-platform CI matrix (macOS/Windows) | ubuntu + 3.11/3.13 only | 3/2/1/5/2/2 → **7** | **Keep (small)** | `ci.yml: runs-on ubuntu-latest` |
| Expand mypy to full `src/d2c` | 7 module-entries staged | 2/2/1/5/3/2 → **5** | **Keep (incremental)** | `pyproject [tool.mypy].files` (7) |

## Deferred intentionally

| Item | Reason |
|---|---|
| KAIROS background heartbeat mode | Dead code (`kairos.py` never instantiated); paper flags it unconfirmed — low value, high effort/risk (score 0) |
| `bubble` permission mode | Subagent-escalation niche; 6 modes cover the spectrum (score 4, low value) |
| Native Windows sandbox backend | Stub → process fallback; high effort, low reach (score −1) |
| OpenTelemetry / exporter | Local JSONL audit covers the need; external telemetry out of scope |
| Docker image / binary distribution | Out of scope; `pip install` + wheel suffice |
| PyPI publishing automation | Deliberately manual (`release.yml` uploads artifacts only) |
| Config setup wizard | `--doctor` diagnoses (not auto-fixes) by design |
| Richer plugin/skill ecosystem (bundled content) | Mechanism exists (`plugins/`, `skills/`); shipping content is out of scope for an educational port |

## Obsolete / dropped

| Item | Reason |
|---|---|
| Browser / computer-use tools | Better delivered via an MCP server than a built-in (needs a browser runtime); score −2 |
| `plans/master_plan.md` as current truth | Historical intent; superseded by actual code + `COMPARISON.md` (already noted in `CLAUDE.md`) |
| Coarse `permission_decision` audit event | Replaced by granular Phase 49 events (`permission_ask/approved/denied/required/approval_error`) |

## Recommended next phases

1. **Phase 51 — Tool breadth, batch 2.** Add a small high-value set (e.g. `ApplyPatch`/`MultiEdit`
   unified-diff editing, `EnvInfo`) with permissions, Read-before-Write/checkpoint compliance, and
   tests. Highest paper fidelity + user value, deterministic to test. (score 9)
2. **Phase 52 — Persistent (session-scoped) approvals.** Extend the Phase 49 approval flow with an
   in-session "always allow this tool / this exact command" cache (never persisted to disk),
   surfaced in the `[y/N/a]` prompt, with audit + tests. Safety-relevant, closes the explicit Phase
   49 follow-up. (score 8)
3. **Phase 53 — Prompt-injection hardening + CI matrix.** Structurally delimit tool-result and
   web/memory content as untrusted in the model context (system-prompt guidance + wrapping),
   regression tests; plus add a macOS CI leg. Safety impact with modest effort. (scores 7 + 7)

Lower-priority follow-ons: incremental mypy expansion (Phase 54), secondary WebSearch providers
(fold into a tools phase).
