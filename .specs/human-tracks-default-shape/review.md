# Code Review: human-tracks-default-shape

Date: 2026-07-28
Branch: fix/human-tracks-default-shape
Reviewer: Automated Code Review

## Summary

**APPROVED** — The fix is minimal, correct, hermetically tested, and adheres to every binding rule in `AGENTS.md` and the spec docs. The patch is the surgical one-liner promised by `design.md` (the `if human_mode:` branch now nests a `verbose_mode` check and routes through `summarize(service, command, payload)` when `--verbose` is absent), the docstring is updated to spell out the precedence, and 10 new hermetic unit tests pin the new behaviour across every size-to-summary candidate plus the verbatim-only fallback plus the escape hatch. `make ci`, `make lint`, `make test`, `make secret-scan`, and `make smoke-dry` all pass. No per-service CLI, no `_summary_*` renderer, no transport/auth/config/retry module, and no `pyproject.toml` was touched. REQ-1 through REQ-6 are satisfied. The Task 4 SKILL.md cleanup is correctly documented as a sandbox no-op outcome (the SKILL.md files do not exist in this container).

## Requirements Coverage

| Requirement | Status | Notes |
|------------|--------|-------|
| REQ-1 (jellyfin -h now matches default JSON shape) | ✅ | `emit()` now calls `summarize("jellyfin", "now", payload)` before `human()`; the rendered header is `user / device / client / playing / progress` (the summary shape) instead of `UserName / DeviceName / Client / NowPlayingItem / PlayState` (the verbatim shape). Verified by simulating both before/after behaviour and by `TestEmitHumanSummarizeRoute.test_summary_columns_appear_verbatim_columns_do_not`. AC1-AC5 satisfied (AC4 is a forward-looking invariant that holds because `_summary_jellyfin_now` is the single source of truth for the shape; no per-command wiring is required). |
| REQ-2 (all size-to-summary commands route through summarize) | ✅ | `TestEmitHumanSummarizeRoute` parametrizes over all 15 keys in `_SUMMARY_RENDERERS` and asserts the summary token appears in the header. Spot-checked the simulated output for every (svc, cmd) pair; the summary keys (`user`, `playing`, `wanted`, `queue`, `requests`, `pending`, ...) all surface correctly. AC7 (graceful default) is pinned by `TestEmitHumanVerbatimFallback` for `jellyfin search`, `radarr calendar`, and the empty `("","")` pair. |
| REQ-3 (no silent expansion to full verbatim payload; row budget enforced) | ✅ | `TestEmitHumanRowBudget.test_summary_row_count_is_smaller_than_verbatim` builds an 8-item payload (2 valid sessions + 6 non-mappings) and asserts the rendered table has fewer than 5 data rows + 1 header (actual: 2 data rows). The second subtest proves the escape hatch honours `limit` against the verbatim count (pagination footer surfaces). AC2 is implicitly satisfied — the regression net in test 2.1 (`assertNotIn("NowPlayingItem", header)`) fails loudly if the bug returns. |
| REQ-4 (`--verbose --human` escape hatch; docstring pinning) | ✅ | `emit()`'s `if verbose_mode:` branch inside `human_mode=True` keeps `shaped = payload` (verbatim pass-through), bypassing `summarize()`. `TestEmitHumanVerboseEscapeHatch.test_escape_hatch_preserves_verbatim_columns` asserts the header contains `NowPlayingItem.Name` (verbatim column) and does NOT contain `playing.name` (summary column), and additionally asserts the output is byte-identical to `human(payload)` directly. `TestEmitHumanDocstringPinning` pins the docstring to reference `:func:\`summarize\`` and to mention `human_mode`/`verbose` in the priority-chain section. |
| REQ-5 (hermetic test coverage; CI gates green) | ✅ | All 10 new tests live in `tests/unit/test_output.py`, use `io.StringIO` capture, and exercise no HTTP. `make test` (595 passed, 4 skipped — the 4 skips are pre-existing in `test_perf_budgets.py` and unrelated), `make lint` (py_compile sweep OK), `make secret-scan` (no committed secrets; `arr.conf.example` placeholder-only), and `make smoke-dry` (CLI grammar + cold-start probe OK) all pass. `make ci` chain is green end-to-end. |
| REQ-6 (SKILL.md cleanup in Sage + Lily workspaces) | ✅ | The executor correctly documented the outcome as a no-op in `tasks.md` items 4.1 and 4.2 — neither `~/.openclaw/workspace/skills/media-cli/SKILL.md` nor `~/.openclaw/workspace-lily/skills/media-cli/SKILL.md` exists in this sandbox. Verified independently: `find / -path "*media-cli/SKILL.md"` returns nothing. A missing file is the strongest form of "did not previously contain such callouts", so AC1/AC2 trivially hold and AC3 forbids adding new warnings. Task 4 checkboxes flipped to `[x]` in the working tree (the chore commit `dd65693` flipped tasks 1/2/3 only; tasks.md working-tree diff is uncommitted but consistent with the documented outcome). |

## Design Adherence

| Component | Status | Notes |
|-----------|--------|-------|
| `emit()` `human_mode` branch routes through `summarize()` | ✅ | `arr_cli/facade/output.py:1008-1014` — `if human_mode:` now nests `if verbose_mode:` (verbatim pass-through) else `summarize(service, command, payload)`. The `human()` call receives the shaped payload with `columns`, `limit`, `max_width` forwarded unchanged. |
| Escape hatch (`verbose_mode=True`) preserved verbatim | ✅ | `shaped = payload` inside the `verbose_mode` branch; `summarize()` is bypassed entirely. Docstring priority item 1 documents this in plain prose. |
| Graceful default for verbatim-only commands | ✅ | `summarize()` returns `payload` unchanged when `(service, command)` is not in `_SUMMARY_RENDERERS` (verified by reading `summarize()` at `output.py:885-924` — uses `_SUMMARY_RENDERERS.get(key)` and returns `payload` on miss). The 14 verbatim-only commands (`jellyfin nextup/search/item`, `radarr calendar/lookup/movie`, `sonarr calendar/lookup/series`, `maintainerr health/storage`, `seerr user/media`, plus `seerr request_count` via its two `_emit` call sites) flow through with no behaviour change. `TestEmitHumanVerbatimFallback` pins this for `jellyfin search`, `radarr calendar`, and the empty `("","")` pair. |
| Docstring precedence note | ✅ | `emit()` docstring (`output.py:940-952`) now spells out: "`human_mode` -- render via `human` over the summary shape (the same shape the no-flag default emits, courtesy of `summarize`); `--verbose` together with `--human` bypasses `summarize` and renders the verbatim payload." The `verbose_mode:` parameter description (`output.py:964-970`) replaces the prior "Has no effect when `human_mode` is True" with: "verbose wins for the data shape, `human_mode` wins for the rendering format (REQ-3 AC1, REQ-4 AC3)." |
| No per-service CLI or `_summary_*` renderer touched | ✅ | `git diff --stat origin/master..HEAD -- arr_cli/` shows only `arr_cli/facade/output.py` (19 lines) changed. `tests/unit/test_output.py` (514 lines added) is the only test file touched. `pyproject.toml`, `arr_cli/facade/{cli_common,config,transport,retry,errors,example_validator}.py`, and `arr_cli/{jellyfin,radarr,sonarr,maintainerr,seerr}.py` are all byte-identical to `origin/master`. |
| Python ≥3.11 + two-space indent + type hints on public functions | ✅ | `arr_cli/facade/output.py` opens with `from __future__ import annotations` (line 33); the patched `emit()` keeps its typed signature unchanged; two-space indent matches the surrounding file. `make lint` py_compile sweep passes. No new module-level comments; the docstring update is the only prose change. |

## Code Quality Issues

- **[MINOR]** `arr_cli/facade/output.py:1009-1013` — The nested `if verbose_mode:` / `else:` inside the `if human_mode:` block could be expressed as a single conditional expression: `shaped = payload if verbose_mode else summarize(service, command, payload)`. The current nested form is more readable when scanned linearly (priority chain visibility), so this is purely stylistic. Not a defect.
- **[MINOR]** `tests/unit/test_output.py` (uncommitted working tree) — `tasks.md` items 4.1 and 4.2 are flipped to `[x]` and annotated with the sandbox no-op outcome, but this change is in the working tree only (not yet committed in `dd65693` or any subsequent commit). The substantive task 4 work — verifying the SKILL.md copies do not exist — is correctly documented; the chore commit simply chose to flip only task 3 checkboxes. Per the review instructions, this is not flagged as a gap.
- No other quality issues observed. The patch follows `AGENTS.md §4.1` (Python ≥3.11, two-space indent, type hints on public functions, no comments unless they explain non-obvious "why", stdlib + already-vendored deps only — no new dependencies). The docstring update is the only prose change. No `print()` calls leaked into per-command code. No new error class, no new exit code.

## Test Results

```
$ make test
make: [test] running pytest tests/unit
........................................................................ [ 12%]
........................................................................ [ 24%]
........................................................................ [ 36%]
........................................................................ [ 48%]
........................................................................ [ 60%]
....................ssss................................................ [ 72%]
........................................................................ [ 84%]
........................................................................ [ 96%]
.......................                                                  [100%]
595 passed, 4 skipped in 1.50s
# The 4 skipped tests are pre-existing in tests/unit/test_perf_budgets.py
# (transport.get does not declare a max_items kwarg yet); unrelated to this feature.

$ make lint
make: [lint] py_compile sweep over arr_cli/ and tests/
make: [lint] OK

$ make secret-scan
make: [secret-scan] running scripts/secret-scan
example-lint: OK: /workspace/projects/media-cli/arr.conf.example contains only documented placeholders
secret-scan: scanning /workspace/projects/media-cli for committed secrets...
secret-scan: no committed secrets detected

$ make smoke-dry
make: [smoke-dry] running scripts/smoke.sh --dry-run
smoke.sh: step 1/3: cold-start probe (time python -c "import arr_cli.jellyfin")
smoke.sh:   cold-start probe OK (0s; budget: 2.0s)
smoke.sh: step 2/3: per-service command grammar check (mode=dry-run)
smoke.sh:   checking: jellyfin now
smoke.sh:     OK: jellyfin --help lists 'now'
smoke.sh:   checking: radarr calendar
smoke.sh:     OK: radarr --help lists 'calendar'
smoke.sh:   checking: sonarr calendar
smoke.sh:     OK: sonarr --help lists 'calendar'
smoke.sh:   checking: maintainerr health
smoke.sh:     OK: maintainerr --help lists 'health'
smoke.sh:   checking: seerr user
smoke.sh:     OK: seerr --help lists 'user'
smoke.sh: step 3/3: skipped (dry-run; pass --live with RUN_LIVE=1 to run)
smoke.sh: all smoke steps passed

# Targeted run of the 10 new tests:
$ pytest tests/unit/test_output.py -v -k "TestEmitHumanSummarizeRoute or TestEmitHumanVerbatimFallback or TestEmitHumanVerboseEscapeHatch or TestEmitHumanRowBudget or TestEmitHumanDocstringPinning"
collected 112 items / 102 deselected / 10 selected
tests/unit/test_output.py ..........                                     [100%]
====================== 10 passed, 102 deselected in 0.05s ======================
```

New test inventory (10 new test methods in `tests/unit/test_output.py`):

| Test class | Method(s) | Requirement coverage |
|---|---|---|
| `TestEmitHumanSummarizeRoute` | `test_summary_columns_appear_verbatim_columns_do_not` (parametrized over all 15 keys in `_SUMMARY_RENDERERS`), `test_row_cells_reflect_summary_values` (parametrized over all 15 keys) | REQ-1, REQ-2, REQ-5 (Test 2.1) |
| `TestEmitHumanVerbatimFallback` | `test_known_non_candidate_jellyfin_search`, `test_known_non_candidate_radarr_calendar`, `test_empty_service_and_command_pair` | REQ-2 AC7 (Test 2.2) |
| `TestEmitHumanVerboseEscapeHatch` | `test_escape_hatch_preserves_verbatim_columns` | REQ-4 AC1, AC2 (Test 2.3) |
| `TestEmitHumanRowBudget` | `test_summary_row_count_is_smaller_than_verbatim`, `test_verbatim_row_count_is_honoured_under_escape_hatch` | REQ-3 AC1, AC3 (Test 2.4) |
| `TestEmitHumanDocstringPinning` | `test_docstring_references_summarize`, `test_docstring_priority_chain_mentions_human_mode_and_verbose` | REQ-4 AC3, REQ-2 AC1 (Test 2.5) |

Parametrized regression net coverage confirmed: `set(_HUMAN_SUMMARY_PAYLOADS.keys()) == set(_SUMMARY_RENDERERS.keys())` — exactly 15 keys each, zero gap.

## Recommendations

- (Optional) Consider whether to commit the working-tree diff to `tasks.md` (flipping tasks 4.1/4.2 to `[x]` and adding the no-op outcome paragraphs) as a follow-up commit. The chore commit `dd65693` flipped tasks 1/2/3 only; leaving task 4 in `[ ]` in `HEAD` is mildly inconsistent with the executor's documented outcome, although the substantive work (verifying the SKILL.md files do not exist) is recorded in prose and was independently re-verified by this reviewer via `find / -path "*media-cli/SKILL.md"`.
- (Optional, non-blocking) The nested `if verbose_mode:` block in `emit()` could be collapsed to a single conditional expression for brevity, but the current form is arguably easier to scan linearly against the priority chain in the docstring. Recommend leaving as-is for readability.

## Verdict

**APPROVED** — ready for merge

The fix delivers exactly what `design.md` promised: a one-branch behavioural change in `emit()` plus a docstring precedence note, plus a hermetic regression net that pins every size-to-summary command path, the verbatim-only fallback, the `--verbose --human` escape hatch, the summary-row budget, and the docstring itself. All four CI gates (`make test`, `make lint`, `make secret-scan`, `make smoke-dry`) are green. No file outside `arr_cli/facade/output.py` and `tests/unit/test_output.py` was modified in `HEAD`; no new runtime dependencies; no per-service CLI touched; no `_summary_*` renderer touched; the five stable exit codes are preserved; `stdout` remains pipe-clean; the 14 verbatim-only commands keep their current behaviour byte-for-byte thanks to `summarize()`'s graceful default.