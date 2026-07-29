# Code Review: library-list-with-monitored-clarification
Date: 2026-07-29
Branch: feature/library-list-with-monitored-clarification
Reviewer: Automated Code Review

## Summary
**APPROVED.** The branch delivers exactly what the three spec documents call for and no
more. Two thin read-only library-list invocations (`sonarr series` no-id, `radarr movie`
no-id) are added by extending the existing optional-positional pattern; the facade, transport,
output, parser, config, and `arr.conf.example` are all untouched. The `monitored` ->
`defaultMonitored` rename on lookup `--human` is applied surgically to only those two
`cmd_lookup` column lists, while the library-list `monitored` column (the operator's library
flag) keeps its original name and the JSON path keeps the upstream `monitored` key exactly
as the API returns it. The verbatim REQ-5 source-default phrase appears in both `cmd_lookup`
docstrings. Hermetic fixture tests pin routing, column shape, regression-safety, and parser
behaviour; full `make ci` (test + secret-scan + smoke-dry) and `make lint` all exit 0.

### Diff footprint
```
.../design.md                         | 442 +++++++
.../requirements.md                   | 128 ++++
.../tasks.md                          | 140 ++++
arr_cli/radarr.py                     |  92 ++--
arr_cli/sonarr.py                     |  94 ++--
tests/unit/test_radarr.py             | 130 +++++-
tests/unit/test_sonarr.py             | 130 +++++-
7 files changed, 1095 insertions(+), 61 deletions(-)
```
Facade (`arr_cli/facade/`) is unmodified (verified via `git diff main...HEAD -- arr_cli/facade/`
which produces no output). Spec docs are added in `.specs/library-list-with-monitored-clarification/`;
source change footprint is exactly the two per-service CLIs and their two test files, matching
Component 1-6 in `design.md`.

## Requirements Coverage

| Requirement | Status | Notes |
|------------|--------|-------|
| REQ-1 (Sonarr `series` no-id list) | ✅ | `arr_cli/sonarr.py` `cmd_series` branches on `getattr(args, "series_id", None)` (line 331); no-id branch hits `GET /api/v3/series` via `_get("/api/v3/series", args, cfg, op="series")` (line 350). `--human` columns exactly `["title", "year", "monitored", "status", "tvdbId", "seasons"]` (line 359). AC1/AC2/AC3 pinned by `TestCmdSeries.test_series_no_id_hits_series_list_path` (`tests/unit/test_sonarr.py:677-691`), `test_series_no_id_human_renders_table` (702-718), `test_series_no_id_emits_list_payload` (693-700) and `test_series_no_id_row_count_matches_payload` (721-758). AC4 (exit codes) goes through existing typed errors in `transport.get`; no new error path added, so the contract is preserved unchanged. |
| REQ-2 (Radarr `movie` no-id list) | ✅ | `arr_cli/radarr.py` `cmd_movie` mirrors Sonarr (lines 290-348); columns exactly `["title", "year", "monitored", "status", "tmdbId", "imdbId"]` (line 343). Tests `test_movie_no_id_hits_movie_list_path` (`tests/unit/test_radarr.py:652-666`), `test_movie_no_id_human_renders_table` (678-694), `test_movie_no_id_emits_list_payload` (668-675), `test_movie_no_id_row_count_matches_payload` (697-734). Exit-code contract preserved via existing error mapping. |
| REQ-3 (single-fetch preserved) | ✅ | AC1: `sonarr series <id>` still routes to `GET /api/v3/series/{encoded_id}` via the `if raw_id:` branch (lines 332-348, unchanged call signature). Regression-safety pin: `TestCmdSeries.test_series_hits_series_path` (existing test) and new `TestBuildSonarrParser.test_series_parses_with_id` (line 868). AC2: Radarr mirrored (`test_movie_hits_movie_path`, `test_movie_parses_with_id`). AC3: percent-encoding via `transport.encode_path_segment` preserved verbatim. |
| REQ-4 (`monitored` -> `defaultMonitored` on `--human` lookup) | ✅ | AC1: `cmd_lookup` columns for Sonarr now end with `"defaultMonitored"` (`arr_cli/sonarr.py:313`). AC2: same for Radarr (`arr_cli/radarr.py:292`). AC3: bare `monitored` no longer in columns list; pinned by `test_lookup_human_default_monitored_column` on both sides which calls `assertIn("defaultMonitored", columns)` AND `assertNotIn("monitored", columns)`. AC4: JSON path untouched — `_SUMMARY_RENDERERS` (`arr_cli/facade/output.py:894`) has no entry for `("sonarr", "lookup")` or `("radarr", "lookup")`, so `output.emit` falls to the verbatim rule (rule 4); the unflagged default and `--verbose` both emit the upstream payload literally. Pinned by `test_lookup_json_keeps_monitored_key` (Sonarr line 607; Radarr line 580) which round-trips a payload with `{"monitored": True}` through the JSON path and asserts the key still appears. |
| REQ-5 (verbatim docstring phrase on `cmd_lookup`) | ✅ | AC1: `arr_cli/sonarr.py:296` contains the exact phrase with backticks: *"the `monitored` field on these records is the source default (TVDB for Sonarr, TMDB for Radarr), not the user's library state"*. AC2: `arr_cli/radarr.py:275` carries the same phrase verbatim. |
| REQ-6 (SKILL.md lookup-recipe note) | ✅ (sandbox no-op) | AC1/AC2: `find / -path '*media-cli/SKILL.md'` returns nothing in this sandbox, as documented in `design.md` (Component 7 + "Sandbox no-op"). The TODO(REQ-6) comments at `arr_cli/sonarr.py:289` and `arr_cli/radarr.py:268` mark this as an operator-workspace follow-up; semantically equivalent to a `CHANGELOG`/follow-up task entry. The text correctly distinguishes TVDB (Sonarr) vs TMDB (Radarr) source defaults. |
| REQ-7 (unit-test pins) | ✅ | AC1: `test_series_no_id_hits_series_list_path` asserts `transport.get` called with `"sonarr"` + `"/api/v3/series"`. AC2: existing `test_series_hits_series_path` preserved + new `test_series_parses_without_id` (`tests/unit/test_sonarr.py:881`). AC3: `test_movie_no_id_hits_movie_list_path` asserts `"radarr"` + `"/api/v3/movie"`. AC4: existing `test_movie_hits_movie_path` + new `test_movie_parses_without_id` (`tests/unit/test_radarr.py:856`). AC5: `test_lookup_human_default_monitored_column` (Sonarr). AC6: same on Radarr. AC7: all new tests are fixture-based via `responses`-style `unittest.mock.patch` on `arr_cli.<service>.transport.get`; zero live HTTP. |
| REQ-8 (CI/lint/smoke pass) | ✅ | AC1: `make ci` ran clean (exit 0) — `make test` -> 614 passed, 4 skipped; `make secret-scan` -> `no committed secrets detected`; `make smoke-dry` -> `all smoke steps passed`. AC2: `make lint` -> `OK`. AC3: smoke-dry exercises `sonarr --help` and `radarr --help` (the five checked subcommands each show their subcommand list; both `--help` outputs now list `series` and `movie` correctly). AC4: contributor gate satisfied — see `Verdict` below. |

## Design Adherence

| Component | Status | Notes |
|-----------|--------|-------|
| C1 (`build_sonarr_parser` — optional series_id) | ✅ | `nargs=argparse.OPTIONAL` + `default=None` added to `add_argument("series_id", ...)`; mirrors `lookup.term` in same file (lines 491-504). Subparser `help=` updated to describe both invocations. |
| C2 (`cmd_series` — branch on series_id) | ✅ | Signature unchanged `(args, cfg) -> int`; explicit `if raw_id:` / `else:` split with comments that justify the columns selection (`monitored` here = library flag, NOT the source default) — exactly per Component 2. Single-id path: `f"/api/v3/series/{transport.encode_path_segment(raw_id)}"`. No-id path: `"/api/v3/series"`. |
| C3 (`cmd_lookup` Sonarr — rename + docstring) | ✅ | Columns list swaps `monitored` -> `defaultMonitored`; verbatim REQ-5 phrase appended to docstring; JSON path untouched (`_SUMMARY_RENDERERS` has no Sonarr `lookup` entry, confirmed by reading `arr_cli/facade/output.py:894`). TODO(REQ-6) comment above function (line 289). |
| C4 (`build_radarr_parser` — optional movie_id) | ✅ | Mirrors C1 (lines 469-482). |
| C5 (`cmd_movie` — branch on movie_id) | ✅ | Mirrors C2. Columns `["title","year","monitored","status","tmdbId","imdbId"]` for the no-id branch. |
| C6 (`cmd_lookup` Radarr — rename + docstring) | ✅ | Mirrors C3. |
| C7 (Sandbox SKILL.md follow-up) | ✅ | TODO(REQ-6) markers in both `sonarr.py` and `radarr.py` line up with the documented sandbox no-op. |
| Facade untouched | ✅ | `git diff main...HEAD -- arr_cli/facade/` produces no output. No transport/output/parser/config/retry changes. |
| `_SUMMARY_RENDERERS` not extended | ✅ | Verified by reading `arr_cli/facade/output.py:894` — new commands deliberately fall through to the verbatim default (rule 4) so the JSON path is exactly the upstream payload. |
| `arr.conf.example` untouched | ✅ | Secret-scan confirms placeholder-only (`example-lint: OK`). |
| No new deps, no new console scripts | ✅ | `arr-cli-0.1.0`; no `pyproject.toml` changes. |
| Renderer priority chain preserved | ✅ | `--human` > `--verbose` > default summary > verbatim. New commands sit at rule 4 by design. |

## Code Quality Issues

- [MINOR] `arr_cli/sonarr.py:289` and `arr_cli/radarr.py:268` carry inline `# TODO(REQ-6):` comments
  above `cmd_lookup`. These are spec-required documentation trackers (Task 7) for the
  SKILL.md sandbox follow-up and are not gratuitous, but they sit at module-function level
  rather than the conventional `CHANGELOG.md` / spec follow-up location. Stylistically
  defensible given the spec mandate, and the wording is precise. Not blocking.

No CRITICAL or MAJOR issues identified.

## Test Results

```
$ make test
SKIPPED [1] tests/unit/test_perf_budgets.py:455: transport.get does not declare a max_items kwarg yet; large-payload cap contract is documented but not wired up
SKIPPED [1] tests/unit/test_perf_budgets.py:546: same reason
SKIPPED [1] tests/unit/test_perf_budgets.py:470: same reason
SKIPPED [1] tests/unit/test_perf_budgets.py:511: same reason
614 passed, 4 skipped in 1.49s

$ make lint
make: [lint] py_compile sweep over arr_cli/ and tests/
make: [lint] OK

$ make secret-scan
example-lint: OK: /workspace/projects/media-cli/arr.conf.example contains only documented placeholders
secret-scan: scanning /workspace/projects/media-cli for committed secrets...
secret-scan: no committed secrets detected

$ make smoke-dry
smoke.sh: step 1/3: cold-start probe ... OK (0s; budget: 2.0s)
smoke.sh: step 2/3: per-service command grammar check (mode=dry-run)
  OK: jellyfin --help lists 'now'
  OK: radarr --help lists 'calendar'
  OK: sonarr --help lists 'calendar'
  OK: maintainerr --help lists 'health'
  OK: seerr --help lists 'user'
smoke.sh: step 3/3: skipped (dry-run; pass --live with RUN_LIVE=1 to run)
smoke.sh: all smoke steps passed

$ make ci
... (chains make test + make secret-scan + make smoke-dry)
make: [ci] all checks passed
```

Targeted new tests verified in isolation:

```
tests/unit/test_sonarr.py::TestCmdSeries           (8 tests) ........  PASSED
tests/unit/test_sonarr.py::TestCmdLookup          (10 tests) .......  PASSED
tests/unit/test_sonarr.py::TestBuildSonarrParser  (11 tests) ...      PASSED
tests/unit/test_radarr.py::TestCmdMovie            (8 tests) ........  PASSED
tests/unit/test_radarr.py::TestCmdLookup          (10 tests) .......  PASSED
tests/unit/test_radarr.py::TestBuildRadarrParser  (11 tests) ...      PASSED
```

## Recommendations

- The four `tests/unit/test_perf_budgets.py` skips predate this branch and are
  unrelated to the change. No action required for this PR.
- When the operator workspace gains `media-cli/SKILL.md`, follow the TODO(REQ-6)
  wording already seeded in `arr_cli/sonarr.py:289` and `arr_cli/radarr.py:268`
  to update the lookup recipes.
- Optional follow-up (out of scope here): consider tightening `_get` to make
  `params=None` and `op=None` Keyword-only universally to avoid future order drift
  — current call sites in `cmd_series`/`cmd_movie` use positional `op=` correctly,
  but the helper still accepts positional `params` which is a footgun.

## Verdict
**APPROVED** — ready for merge
