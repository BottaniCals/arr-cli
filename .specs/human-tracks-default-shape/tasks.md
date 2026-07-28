# Implementation Plan

## Task Overview

Make `arr_cli.facade.output.emit` route the `--human` (`-h`) branch through `summarize(service, command, payload)` before calling `human()`, so the table describes the same data shape as the no-flag default JSON. `--verbose --human` remains the deliberate escape hatch that bypasses `summarize()` and renders the verbatim payload as a table. Verbatim-only commands (14 entries) keep their current full-payload-column table unchanged thanks to `summarize()`'s graceful default. The fix is local to `arr_cli/facade/output.py` (one branch of `emit()` plus its docstring) and its companion test file `tests/unit/test_output.py`. No per-service CLI, no `_summary_*` renderer, no transport/auth/config/retry module is touched. No new runtime dependencies. SKILL.md "Output formats" prose in the Sage and Lily workspace copies is updated as a docs follow-up once the code lands.

Implementation order:
1. Patch `emit()` (the single line of behaviour change + the docstring that documents the precedence).
2. Land the test file additions in one cohesive PR-sized chunk, grouped by behavioural concern (orthogonal flag paths, regression nets, hermetic verification).
3. Sweep SKILL.md prose in both workspace copies.
4. Verify the local CI chain (`make ci`, `make lint`) and confirm no per-service CLI / no `_summary_*` renderer changed.

## Tasks

- [x] 1. Patch `emit()` to route the `--human` branch through `summarize()`
  - [x] 1.1 Modify `arr_cli/facade/output.py` `emit()` body so the `human_mode=True, verbose_mode=False` branch calls `summarize(service, command, payload)` first, then `human(summarized, columns=..., limit=..., max_width=...)`
    - Rewrite the single `if human_mode:` branch in `emit()` (currently at `arr_cli/facade/output.py:928`): when `verbose_mode` is False, pass `summarize(service, command, payload)` into `human()`; when `verbose_mode` is True, keep the existing verbatim pass-through (escape hatch).
    - Forward the existing `limit` / `max_width` / `columns` kwargs unchanged into `human()` so summary-row budgets are enforced (REQ-3 AC1).
    - Keep the function signature unchanged; the fix is inside the body.
    - _Requirements: REQ-1, REQ-2, REQ-3, REQ-4_
  - [x] 1.2 Update `emit()`'s docstring to spell out the `--human` vs `--verbose` precedence
    - Edit the priority-chain prose in the `emit()` docstring (currently at `arr_cli/facade/output.py:928`) to state: "`human_mode` -- render via :func:`human` over the summary shape (same shape the no-flag default emits, courtesy of :func:`summarize`); `--verbose` together with `--human` bypasses :func:`summarize` and renders the verbatim payload".
    - Replace the line "Has no effect when `human_mode` is True" with the new escape-hatch semantics ("verbose wins for the data shape; human wins for the rendering format") per REQ-4 AC3.
    - Do not add unrelated prose or commentary; two-space indent; no module-level comment noise (AGENTS.md §4.1).
    - _Requirements: REQ-4 AC3_
  - [x] 1.3 Sanity-check the patched `emit()` body against `summarize()`'s graceful default
    - Confirm `emit()` still falls through to `human(payload, ...)` unchanged for the 14 verbatim-only commands (because `summarize()` returns `payload` unchanged when `(service, command)` is not in `_SUMMARY_RENDERERS`).
    - Confirm callers that omit `service` / `command` (empty strings) keep their verbatim `--human` behaviour.
    - No code change here — this is a read-back verification of the patch from task 1.1.
    - _Requirements: REQ-2 AC7, REQ-4 AC1, NFR-Reliability_

- [x] 2. Add hermetic unit tests for the new `--human` → `summarize()` → `human()` path
  - [x] 2.1 Add a parametrized regression net in `tests/unit/test_output.py` that pins the `--human` branch through `summarize()` for every size-to-summary command
    - File: `tests/unit/test_output.py` (extend the existing test classes; reuse `_capture_stdout` and the existing `_SUMMARY_RENDERERS` import already in the module).
    - For every key in `_SUMMARY_RENDERERS.keys()`, call `emit(synthetic_payload, human_mode=True, verbose_mode=False, service=svc, command=cmd, stream=io.StringIO(), columns=...)` against a payload that contains both summary-shape keys (e.g. `playing`, `progress`, `wanted`, `queue`, `requests`, `pending`) and verbatim-only keys (e.g. `NowPlayingItem`, `PlayState`).
    - Assert the captured stdout's first line (the header row) contains at least one summary-shape token and does NOT contain `NowPlayingItem.` or `PlayState`.
    - Assert at least one row cell equals a value present in the synthetic payload's summary (no `<null>` for fields the summary populated).
    - Use `io.StringIO` capture, mirror the `TestEmitHuman` pattern; `responses` is already a dev dep but these tests do not exercise HTTP at all (REQ-5 AC2).
    - _Requirements: REQ-1, REQ-2, REQ-5_
  - [x] 2.2 Add a verbatim-only fallback test in `tests/unit/test_output.py` to pin the graceful default for non-candidate commands
    - File: `tests/unit/test_output.py` (extend `TestSummarizeGracefulDefault` or add a sibling class).
    - Call `emit(small_payload, human_mode=True, verbose_mode=False, service="jellyfin", command="search", stream=io.StringIO())` for any verbatim-only command (e.g. `jellyfin search`, `radarr calendar`, `maintainerr health`).
    - Assert the captured stdout is byte-identical to `human(small_payload, ...)` invoked directly (the existing verbatim-table behaviour is preserved).
    - Confirm at least one test covers an empty `("","")` service/command pair as a regression guard for callers that do not thread both kwargs.
    - _Requirements: REQ-2 AC7, REQ-5_
  - [x] 2.3 Add a `--verbose --human` escape-hatch test in `tests/unit/test_output.py` to pin the verbatim-shape table
    - File: `tests/unit/test_output.py` (add a new test method to `TestEmitPriorityChain` or a dedicated class).
    - Call `emit(verbatim_payload, human_mode=True, verbose_mode=True, service="jellyfin", command="now", stream=io.StringIO())` against a payload that includes both summary-shape keys and `NowPlayingItem.Name`, `NowPlayingItem.SeriesName`, `PlayState`.
    - Assert the captured stdout's first line contains `NowPlayingItem.Name` and does NOT contain `playing.name` (proves `_summary_jellyfin_now` was not applied).
    - Assert `human(verbatim_payload, ...) == captured_output` (snapshot invariant for the escape hatch).
    - _Requirements: REQ-4 AC1, REQ-4 AC2_
  - [x] 2.4 Add a summary-row-budget assertion in `tests/unit/test_output.py` that pins REQ-3 AC1 / AC3
    - File: `tests/unit/test_output.py` (add a new test method).
    - Construct a `verbatim_payload` whose `_summary_jellyfin_now`-shaped summary has fewer than 5 items but whose raw `/Sessions` shape has more than 5 items.
    - Call `emit(payload, human_mode=True, verbose_mode=False, service="jellyfin", command="now", limit=5, stream=io.StringIO())`; assert the captured table contains fewer than 5 data rows plus 1 header row and does NOT expand to the verbatim row count.
    - Call the same payload with `human_mode=True, verbose_mode=True, limit=5`; assert the verbatim row count is honored (escape hatch is not summary-bound by `limit`).
    - _Requirements: REQ-3 AC1, REQ-3 AC3_
  - [x] 2.5 Add a docstring-pinning regression net in `tests/unit/test_output.py` that verifies `emit()`'s docstring mentions the precedence
    - File: `tests/unit/test_output.py` (add a new test method).
    - Use `inspect.getdoc(emit)`; assert the docstring contains the substring tokens `human_mode` near `verbose` so a future revert that drops the escape-hatch note fails this test (REQ-4 AC3).
    - Also assert the docstring references `:func:`summarize`` so a future revert that drops the summary-shape language fails (REQ-2 AC1).
    - _Requirements: REQ-4 AC3, REQ-2 AC1_

- [ ] 3. Verify CI gates and untouched-files contract
  - [ ] 3.1 Run `make ci` and `make lint` locally and confirm both pass with the patched `emit()` and the new tests
    - Execute `make ci` (= `make test && make secret-scan && make smoke-dry`) and confirm all three stages exit 0.
    - Execute `make lint` and confirm the `py_compile` sweep stays green for the patched `arr_cli/facade/output.py`.
    - If any test fails, fix it in the same patch (do not loosen assertions).
    - _Requirements: REQ-5 AC2, REQ-5 AC3, NFR-Reliability_
  - [ ] 3.2 Confirm no file outside `arr_cli/facade/output.py` and `tests/unit/test_output.py` was modified
    - `git status --porcelain` should list exactly those two files (plus possibly `CHANGELOG.md` if the contributor chose to log the fix in the Unreleased section; otherwise untouched).
    - No per-service CLI (`arr_cli/{jellyfin,radarr,sonarr,maintainerr,seerr}.py`) is modified; no `_summary_*` renderer is modified; no `pyproject.toml` change; no transport / config / retry module change.
    - Verify with `git diff --stat HEAD~1 -- arr_cli/` that only `output.py` appears.
    - _Requirements: REQ-3, NFR-Reliability, NFR-Security_

- [ ] 4. Drop SKILL.md "Known issue" / "workaround" callouts in both workspace copies
  - [ ] 4.1 Edit `~/.openclaw/workspace/skills/media-cli/SKILL.md` "Output formats" section to remove `--human` known-issue prose (Sage copy)
    - File: `~/.openclaw/workspace/skills/media-cli/SKILL.md`.
    - Remove any prose that calls out `--human` as a "Known issue", "workaround", or "does not currently match its intended design".
    - If the SKILL.md does not currently contain such callouts, do not add any new warnings about the `--human` renderer (REQ-6 AC3).
    - Preserve all other content in the "Output formats" section verbatim; only edit the callout lines.
    - _Requirements: REQ-6 AC1, REQ-6 AC3_
  - [ ] 4.2 Edit `~/.openclaw/workspace-lily/skills/media-cli/SKILL.md` "Output formats" section to apply the same edit (Lily copy)
    - File: `~/.openclaw/workspace-lily/skills/media-cli/SKILL.md`.
    - Same edit as task 4.1: drop the "Known issue" / "workaround" / "does not currently match its intended design" callouts from the "Output formats" section.
    - If only one of the two workspace copies contains the callouts, only edit that one; skip the no-op edit on the other (REQ-6 AC3).
    - Confirm both Sage and Lily copies end up consistent with the new `--human` behaviour.
    - _Requirements: REQ-6 AC2, REQ-6 AC3_