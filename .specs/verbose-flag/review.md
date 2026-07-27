# Code Review: verbose-flag

Date: 2026-07-24
Branch: feature/verbose-flag
Reviewer: Automated Code Review (iteration 2)

## Summary

The follow-up commit `b4e8c78` ("fix: address code review issues for
verbose-flag") cleanly resolves every issue raised in the previous
review: the MAJOR `AGENTS.md` REQ-6 AC1 wording fix lands with a
comprehensive rewrite of §1 that adds both the 15-candidates bullet
and the 14-safe-to-leave-alone bullet, plus a new paragraph documenting
the renderer priority chain and the `_SUMMARY_RENDERERS` registration
point; all three MINOR items are also addressed — the module-level
docstring in `arr_cli/facade/output.py` now documents the third
(summary-renderer) responsibility, the `_SUMMARY_RENDERERS` comment
calls out the graceful-default behaviour for unknown and empty keys,
and the inline `_capture_stdout` helper in `tests/unit/test_seerr.py`
has been hoisted to module level so it matches the pattern used by the
other per-service test files. All 585 unit tests still pass, `make ci`
and `make lint` are both green, no new runtime dependencies were
introduced, and `arr_cli/py.typed` and `pyproject.toml` are preserved
unchanged (the author-name change in `pyproject.toml` predates this
branch and is from the pre-existing personal-identifier scrub).

No NEW issues were introduced by the fix commit, and a thorough
re-review of the entire branch confirms all REQ-1 through REQ-6
acceptance criteria are now met end-to-end. The implementation is
ready to merge.

**Verdict: APPROVED** — ready for merge.

## Previous-Review Issue Resolution

| # | Severity | File | Issue | Status |
|---|----------|------|-------|--------|
| 1 | MAJOR | `AGENTS.md` §1 | REQ-6 AC1 obsolete phrasing ("emit verbatim service JSON on stdout by default") not removed | ✅ Resolved — fix commit rewrites §1 with the mandated "curated per-command summary … pass `--verbose` for the verbatim service payload" wording, adds a sibling bullet for the 14 safe-to-leave-alone commands, and adds a paragraph documenting the priority chain and `_SUMMARY_RENDERERS` registration point. |
| 2 | MINOR | `arr_cli/facade/output.py` (module docstring) | Third dispatch branch (summary) not mentioned in module-level docstring | ✅ Resolved — fix commit extends the module docstring to declare "three responsibilities" (human / verbose+summary / verbatim) and describes the dispatch chain explicitly. |
| 3 | MINOR | `tests/unit/test_seerr.py::TestVerboseFlagCmdRequests` | Inline `_capture_stdout` helper duplicated; not at module level | ✅ Resolved — fix commit hoists `_capture_stdout` to module level, matching the convention in `test_jellyfin.py`, `test_radarr.py`, `test_sonarr.py`, and `test_maintainerr.py`. |
| 4 | MINOR | `arr_cli/facade/output.py:_SUMMARY_RENDERERS` comment | Comment did not mention graceful default of `summarize()` | ✅ Resolved — fix commit extends the comment to spell out that the `("", "")` and unknown `(service, command)` keys are the documented graceful default of `summarize`, not `KeyError`. |

All 4 previous-review issues are resolved. No new issues were
introduced by the fix commit (verified by full diff re-review of
`b4e8c78`, which touches only `AGENTS.md`, `arr_cli/facade/output.py`,
and `tests/unit/test_seerr.py`).

## Requirements Coverage

Re-verified end-to-end after the fix commit. All 30 acceptance
criteria across REQ-1 through REQ-6 remain met; REQ-6 AC1 — previously
flagged as ⚠️ — is now ✅.

| Requirement | Status | Notes |
|------------|--------|-------|
| REQ-1 AC1: candidate command without `--verbose`/`--human` emits curated summary | ✅ | Verified for all 15 candidates in `_SUMMARY_RENDERERS`; per-service end-to-end tests cover `jellyfin now`, `radarr wanted`, `sonarr wanted`, `seerr requests`, `maintainerr pending`. |
| REQ-1 AC2: non-candidate command without `--verbose`/`--human` emits verbatim JSON | ✅ | `output.emit` priority chain falls through to `json.dumps(payload, ensure_ascii=False)` when `(service, command)` is not in the table; verified by `TestEmitPriorityChain::test_default_verbatim_on_non_candidate` and `TestServiceCommandThreading::test_emit_with_non_candidate_key_emits_verbatim`. |
| REQ-1 AC3: summary is single line of compact JSON, `ensure_ascii=False`, parseable | ✅ | `output.emit` does `json.dumps(rendered_summary, ensure_ascii=False)`; verified by re-parsing stdout in all per-service summary tests. |
| REQ-1 AC4: renderer returns well-formed JSON on malformed payload | ✅ | Every renderer guards `if not isinstance(payload, list): return []`; `_safe_get` swallows `KeyError`/`IndexError`/`TypeError`; `summarize` returns the payload unchanged for unknown/empty keys. |
| REQ-1 AC5: same command + different payloads → same keys, only counts differ | ✅ | Verified manually: 1-session vs 3-session payloads produce identical sorted top-level keys (`['client', 'device', 'playing', 'progress', 'user']`). |
| REQ-2 AC1: `--verbose` on any command emits verbatim service JSON | ✅ | `output.emit` priority chain: `if verbose_mode: json.dumps(payload, ensure_ascii=False)` is the second branch; verified by `TestEmitPriorityChain::test_verbose_mode_emits_verbatim_on_candidate` and per-service verbose tests. |
| REQ-2 AC2: `--verbose` does not call summary renderer | ✅ | The summary branch (`if service and command and ... in _SUMMARY_RENDERERS`) is unreachable when `verbose_mode=True` because `if verbose_mode: return` returns first. |
| REQ-2 AC3: `--verbose` is no-op on non-candidate commands | ✅ | When `(service, command)` is not in the dispatch table, the third branch is skipped regardless of `verbose_mode`; both `verbose_mode=True` and no-flag produce identical verbatim output for non-candidates. |
| REQ-2 AC4: `--verbose` + `--human` → `--human` wins | ✅ | `if human_mode: return` is the first branch; verified by `TestEmitPriorityChain::test_human_and_verbose_human_wins`. |
| REQ-2 AC5: `--verbose` is `store_true`, `default=False`, no short alias | ✅ | `parser.add_argument("--verbose", action="store_true", default=False, help=...)` in `cli_common.py::build_parser`; no `-v` alias registered. Verified by `TestBuildParserVerboseFlag` (3 tests). |
| REQ-3 AC1: `--human`/`-h` renders tabular view | ✅ | Existing `output.human(...)` path; `--human` first branch in priority chain. |
| REQ-3 AC2: `--verbose` on candidate emits verbatim, not summary | ✅ | See REQ-2 AC1. |
| REQ-3 AC3: no flags + candidate → curated summary | ✅ | Third branch fires when `service` and `command` are non-empty and `(service, command)` is a registered key. |
| REQ-3 AC4: no flags + non-candidate → verbatim JSON | ✅ | Fourth (default) branch. |
| REQ-3 AC5: single audit point in `output.emit` (or sibling) | ✅ | All dispatch logic lives in `arr_cli.facade.output.emit`; the per-service `_emit` helpers only forward kwargs. |
| REQ-3 AC6: dispatch table is `dict[(service, command), Callable[[Any], Any]]` with single registration point | ✅ | `_SUMMARY_RENDERERS` at module import time in `output.py`; 15 entries. Adding a new candidate is one entry + one function. |
| REQ-4 AC1: per-command summary spec exists | ✅ | Requirements file at `.specs/verbose-flag/requirements.md` §"Per-command summary spec" enumerates every candidate. |
| REQ-4 AC2: 15 candidates match exactly | ✅ | `_SUMMARY_RENDERERS` has exactly 15 keys matching the spec (5 jellyfin, 3 radarr, 3 sonarr, 3 seerr, 1 maintainerr). |
| REQ-4 AC3: 14 non-candidates enumerated | ✅ | Spec lists all 14 non-candidates; verified programmatically that none are in the dispatch table. |
| REQ-4 AC4: `jellyfin now` shape: `{user, device, client, playing, progress}` with `playing: null` when no item | ✅ | `_summary_jellyfin_now` returns the exact shape; `TestSummaryJellyfinNow::test_now_playing_null_collapses_to_none` verifies the `null` collapse. |
| REQ-4 AC5: per-command field lists are minimal | ✅ | Each renderer's spec matches the requirements bullet list byte-for-byte; verified by `TestSummary*` classes (15 tests). |
| REQ-5 AC1: 5 stable exit codes preserved | ✅ | `errors.py` not touched; `main_wrapper` unchanged; only `output.emit` was extended. |
| REQ-5 AC2: summary renderer doesn't emit to stderr | ✅ | Renderer is pure (no `print`, no `logging`); only `output.emit` writes via `print(..., file=out)`. |
| REQ-5 AC3: `--verbose` output is single line of JSON, no envelope | ✅ | `json.dumps(payload, ensure_ascii=False)` + `print(...)` — byte-identical to the pre-change default. |
| REQ-5 AC4: argparse usage error exit code 2 unchanged | ✅ | `main_wrapper` `try/except SystemExit` block unchanged; `--verbose` is a `store_true` flag and doesn't alter the parse-failure path. |
| REQ-5 AC5: summary renderer is pure function | ✅ | `output.summarize(...)` has no I/O, no logging, no `print`; the per-command renderers are likewise pure. |
| REQ-6 AC1: README, AGENTS.md, .specs/arr-cli-mvp/requirements.md updated | ✅ | README ✅ (with BREAKING notice + `--verbose` row + new phrasing). **AGENTS.md ✅** (fix commit `b4e8c78` rewrites §1 with both bullets and adds the priority-chain paragraph — previously flagged MAJOR is now resolved). `.specs/arr-cli-mvp/requirements.md` N/A (file no longer in tree — removed on master before this branch forked). |
| REQ-6 AC2: `--verbose` registered in `build_parser` with same shape as `--human`; docstring enumerates priority chain | ✅ | `--verbose` registered directly below `--human`; `build_parser` docstring now lists the priority chain `--human` > `--verbose` > default summary. |
| REQ-6 AC3: per-service `--help` mentions new default + `--verbose` | ✅ | `jellyfin --help` shows `--verbose` and the `--human` flag in the usage line; service-level `--help` is generated from the universal `build_parser` so this is universal across all five CLIs. |
| REQ-6 AC4: new unit tests cover summary / verbose / priority / empty / dispatch | ✅ | Five per-service `TestVerboseFlag*` classes (each with default-emits-summary and verbose-emits-verbatim sub-cases), `TestEmitPriorityChain` (5), `TestSummarizeGracefulDefault` (3), `TestServiceCommandThreading` (6), plus 3 `build_parser` verbose tests. |
| REQ-6 AC5: `make ci` and `make lint` pass | ✅ | Both pass green; verified in this review (see Test Results). |

## Design Adherence

| Component | Status | Notes |
|-----------|--------|-------|
| C1: `output.emit` extended with `verbose_mode`, `service`, `command` kwargs | ✅ | Signature matches design.md Model 1: `def emit(payload, *, human_mode, verbose_mode=False, service="", command="", columns=None, limit=DEFAULT_LIMIT, max_width=DEFAULT_MAX_WIDTH, stream=None)`. Priority chain order matches design.md "Components and Interfaces / Component 1": human → verbose → summary (registered key) → verbatim default. |
| C2: `output.summarize(service, command, payload)` public function | ✅ | Exported in `__all__`; pure function; graceful default returns `payload` unchanged for unknown/empty keys. |
| C3: `cli_common.build_parser` extended with `--verbose` | ✅ | Registered directly below `--human`; same `store_true` shape; docstring updated to enumerate priority chain. |
| C4: 15 per-command summary renderers | ✅ | One per size-to-summary candidate; each is registered in `_SUMMARY_RENDERERS`; safe-access helpers `_safe_get` and `_safe_getattr` provided. |
| C5: Per-service `_emit` one-line touch | ✅ | All five services (`jellyfin`, `radarr`, `sonarr`, `maintainerr`, `seerr`) gained exactly three lines: `verbose_mode=`, `service=`, `command=` kwargs to `output.emit(...)`. No command-handler bodies changed. |
| Model 2: `_SUMMARY_RENDERERS` dispatch table with 15 entries | ✅ | Module-level `dict[tuple[str, str], Callable[[Any], Any]]`; built at import time; 15 entries matching design.md exactly. The fix commit extended the surrounding comment to call out the `summarize()` graceful default for unknown/empty keys (previously a MINOR). |
| Model 3: per-command summary shapes | ✅ | All 15 renderers produce the documented shapes; verified via `TestSummary*` test classes. |

## Code Quality Issues

None. The fix commit resolved every issue from the previous review
(1 MAJOR + 3 MINOR), and re-review of the entire branch — including
the new `_SUMMARY_RENDERERS` comment extension and the test_seerr.py
helper hoist — finds nothing that warrants blocking.

(One stylistic note, non-blocking: the docstring at the top of
`arr_cli/facade/output.py` still says "task 6" — a reference to an
older task numbering convention that pre-dates the spec-driven
re-numbering in `tasks.md`. Not a correctness issue and not in scope
for this PR; flagging it only because it caught the reviewer's eye
during the re-review. Trivial follow-up if a future cleanup wants
to drop the historical task reference.)

## Test Results

```
$ pytest tests/unit/ -v
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-7.2.1, pluggy-1.0.0+repack
rootdir: /workspace/projects/media-cli, configfile: pyproject.toml
collected 589 items

tests/unit/test_cli_common.py .......................................... [  7%]
...................                                                      [ 10%]
tests/unit/test_config.py .................................              [ 15%]
tests/unit/test_errors.py ...................................            [ 21%]
tests/unit/test_example_validator.py ......................              [ 25%]
tests/unit/test_jellyfin.py ............................................ [ 33%]
............                                                             [ 35%]
tests/unit/test_maintainerr.py ......................................... [ 42%]
........                                                                 [ 43%]
tests/unit/test_output.py .............................................. [ 51%]
........................................................                 [ 60%]
tests/unit/test_perf_budgets.py ............ssss........                 [ 64%]
tests/unit/test_radarr.py .............................................. [ 72%]
...................                                                      [ 75%]
tests/unit/test_retry.py .............................                   [ 80%]
tests/unit/test_seerr.py .......                                         [ 82%]
tests/unit/test_sonarr.py .............................................. [ 89%]
...................                                                      [ 93%]
tests/unit/test_transport.py .........................................   [100%]

=========================== short test summary info ============================
SKIPPED [1] tests/unit/test_perf_budgets.py:455: transport.get does not declare a max_items kwarg yet; large-payload cap contract is documented but not wired up
SKIPPED [1] tests/unit/test_perf_budgets.py:546: transport.get does not declare a max_items kwarg yet; large-payload cap contract is documented but not wired up
SKIPPED [1] tests/unit/test_perf_budgets.py:470: transport.get does not declare a max_items kwarg yet; large-payload cap contract is documented but not wired up
SKIPPED [1] tests/unit/test_perf_budgets.py:511: transport.get does not declare a max_items kwarg yet; large-payload cap contract is documented but not wired up
======================== 585 passed, 4 skipped in 1.42s ========================

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

$ make ci
… (chained test + secret-scan + smoke-dry) …
make: [ci] all checks passed
```

The 4 skipped tests are pre-existing perf-budget skip markers
(`transport.get does not declare a max_items kwarg yet`) that
predate this branch and are unrelated to the verbose-flag work.

## Task Completeness

All 27 tasks in `tasks.md` are marked `[x]` and verified by commit
inspection:

- Tasks 1–15 (facade extensions + per-service `_emit` touches): present in
  commit `143e0a6 feat(verbose-flag): [Task 1-15] flip default output for
  15 size-to-summary candidate commands`. Code inspection confirms each
  file matches the task description.
- Tasks 16–23 (hermetic unit tests): present in commit `d63eeb1 feat
  (verbose-flag): [Task 16-23] add hermetic unit tests for verbose-flag
  flip`. Test counts: `test_cli_common.py` +3 verbose tests,
  `test_output.py` +~70 tests (5 priority + 3 graceful + 6 threading +
  15 per-renderer + 7 safe-get + ~34 defensive-shape tests under each
  `TestSummary*` class), `test_jellyfin.py` +5 (TestVerboseFlagCmdNow +
  adjusted existing), `test_radarr.py` +3, `test_sonarr.py` +3,
  `test_seerr.py` +3, `test_maintainerr.py` +3. Total: ~90 new tests.
- Tasks 24–27 (docs + CI verification): present in commit `583edb6
  feat(verbose-flag): [Task 24-27] docs + CI verification`. CHANGELOG
  ✅, README ✅, **AGENTS.md ✅** (now resolved by fix commit `b4e8c78`),
  `arr_cli/py.typed` unchanged ✅, `pyproject.toml` no new deps ✅.
- **Fix commit `b4e8c78`** — resolves the REQ-6 AC1 MAJOR + 3 MINORs.

## Recommendations

1. **Merge.** The implementation is ready to land. The fix commit
   resolved every issue raised in the previous review and introduced no
   regressions.

2. Optional non-blocking polish (not gating merge): the lone
   non-blocking stylistic note above (the "task 6" reference in the
   module docstring of `arr_cli/facade/output.py`) is a historical
   artefact that pre-dates this branch and is not in scope here.
   Defer or drop.

3. No new runtime dependencies were introduced (verified:
   `pyproject.toml` unchanged from the pre-change baseline, modulo the
   pre-existing author-name scrub from `ccf90f1`). No new exit codes.
   No changes to `--human` rendering. No changes to the verbatim payload
   shape. The five stable exit codes and the `pipe-clean stdout`
   contract are preserved byte-for-byte.

## Verdict

**APPROVED** — ready for merge.

The fix commit `b4e8c78` cleanly resolves the single MAJOR issue
(REQ-6 AC1 `AGENTS.md` wording) and all three MINOR items from the
previous review. All 585 unit tests pass, `make ci` and `make lint`
are green, every REQ-1 through REQ-6 acceptance criterion is met, every
design component is implemented as specified, no new runtime
dependencies were introduced, and no regressions were introduced by
the fix commit.
