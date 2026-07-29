# Implementation Plan

## Task Overview

Add two read-only library-list invocations to `arr-cli` (`sonarr series` no-id,
`radarr movie` no-id) by extending the existing optional-positional pattern
(`lookup.term`, `calendar.start/end`) and branching in `cmd_series` / `cmd_movie`
on whether the id was supplied. Rename the `--human` lookup column from
`monitored` to `defaultMonitored` on both services and append the verbatim
source-default phrase to each `cmd_lookup` docstring (REQ-5). Pin the new
routing, column shape, and regression-safety with hermetic `responses`-style
fixture tests. The facade, transport, output, and parser infrastructure stay
untouched; the SKILL.md recipe update is documented as a sandbox no-op
follow-up because no `media-cli/SKILL.md` exists in this sandbox.

## Tasks

- [x] 1. Make `sonarr series_id` an optional positional
  - [x] 1.1 Modify `series` subparser in `arr_cli/sonarr.py` (`build_sonarr_parser`, lines ~454–466)
    - Add `nargs=argparse.OPTIONAL` and `default=None` to the existing `add_argument("series_id", ...)` call
    - Mirror the existing `lookup.term` pattern in the same file
    - Update the subparser `help=` string to describe both invocations (e.g. `fetch a single series by id, or list all series when no id is given (GET /api/v3/series[/{id}])`)
    - Do NOT add new imports; `argparse` is already in scope
    - _Requirements: REQ-1 AC1, REQ-3 AC1_
  - [x] 1.2 Branch `cmd_series` in `arr_cli/sonarr.py` (lines ~319–341) on whether `series_id` is supplied
    - Keep the existing signature `(args: argparse.Namespace, cfg: ServiceConfig) -> int`
    - When `getattr(args, "series_id", None)` is truthy, call `_get(f"/api/v3/series/{transport.encode_path_segment(raw_id)}", args, cfg, op=f"series id={raw_id}")` (preserve existing REQ-3 single-fetch behaviour verbatim)
    - When `None`, call `_get("/api/v3/series", args, cfg, op="series")`
    - Do NOT change the helper imports (`_get`, `_emit`); they already exist in the module
    - _Requirements: REQ-1 AC1, REQ-3 AC1, REQ-3 AC3_
  - [x] 1.3 Set the no-id `--human` column list in `cmd_series` (`arr_cli/sonarr.py`)
    - Use exactly `["title", "year", "monitored", "status", "tvdbId", "seasons"]` for the no-id branch (REQ-1 AC2)
    - Keep the existing single-id column list unchanged (operator's library row, NOT a lookup-source default)
    - `monitored` here is the operator's library flag and intentionally keeps its name; REQ-4 rename applies only to `lookup`
    - _Requirements: REQ-1 AC2, REQ-1 AC3_

- [x] 2. Make `radarr movie_id` an optional positional
  - [x] 2.1 Modify `movie` subparser in `arr_cli/radarr.py` (`build_radarr_parser`, lines ~436–448)
    - Add `nargs=argparse.OPTIONAL` and `default=None` to the existing `add_argument("movie_id", ...)` call
    - Mirror the existing `lookup.term` pattern in the same file
    - Update the subparser `help=` string to describe both invocations (e.g. `fetch a single movie by id, or list all movies when no id is given (GET /api/v3/movie[/{id}])`)
    - Do NOT add new imports; `argparse` is already in scope
    - _Requirements: REQ-2 AC1, REQ-3 AC2_
  - [x] 2.2 Branch `cmd_movie` in `arr_cli/radarr.py` (lines ~289–311) on whether `movie_id` is supplied
    - Keep the existing signature `(args: argparse.Namespace, cfg: ServiceConfig) -> int`
    - When `getattr(args, "movie_id", None)` is truthy, call `_get(f"/api/v3/movie/{transport.encode_path_segment(raw_id)}", args, cfg, op=f"movie id={raw_id}")` (preserve existing REQ-3 single-fetch behaviour verbatim)
    - When `None`, call `_get("/api/v3/movie", args, cfg, op="movie")`
    - Do NOT change the helper imports (`_get`, `_emit`); they already exist in the module
    - _Requirements: REQ-2 AC1, REQ-3 AC2, REQ-3 AC3_
  - [x] 2.3 Set the no-id `--human` column list in `cmd_movie` (`arr_cli/radarr.py`)
    - Use exactly `["title", "year", "monitored", "status", "tmdbId", "imdbId"]` for the no-id branch (REQ-2 AC2)
    - Keep the existing single-id column list unchanged (operator's library row, NOT a lookup-source default)
    - `monitored` here is the operator's library flag and intentionally keeps its name; REQ-4 rename applies only to `lookup`
    - _Requirements: REQ-2 AC2, REQ-2 AC3_

- [x] 3. Rename `monitored` → `defaultMonitored` on `sonarr lookup` `--human` output
  - [x] 3.1 Update `cmd_lookup` columns in `arr_cli/sonarr.py` (lines ~284–304)
    - Change the `columns` list from `["title", "year", "tvdbId", "tvMazeId", "monitored"]` to `["title", "year", "tvdbId", "tvMazeId", "defaultMonitored"]`
    - Do NOT modify the JSON path: `_SUMMARY_RENDERERS` has no entry for `("sonarr", "lookup")`, so the unflagged default and `--verbose` continue to emit the verbatim upstream payload with the literal `monitored` key (REQ-4 AC4)
    - Do NOT touch the `_get(...)` call signature or the `params` dict
    - _Requirements: REQ-4 AC1, REQ-4 AC3, REQ-4 AC4_
  - [x] 3.2 Append the verbatim REQ-5 phrase to the `cmd_lookup` docstring in `arr_cli/sonarr.py` (lines ~284–290)
    - Add a new paragraph to the existing docstring containing exactly: *"the `monitored` field on these records is the source default (TVDB for Sonarr, TMDB for Radarr), not the user's library state"* (verbatim, with the backticks around `monitored`)
    - Keep all existing wording; append rather than rewrite
    - _Requirements: REQ-5 AC1_

- [x] 4. Rename `monitored` → `defaultMonitored` on `radarr lookup` `--human` output
  - [x] 4.1 Update `cmd_lookup` columns in `arr_cli/radarr.py` (lines ~262–282)
    - Change the `columns` list from `["title", "year", "tmdbId", "imdbId", "monitored"]` to `["title", "year", "tmdbId", "imdbId", "defaultMonitored"]`
    - Do NOT modify the JSON path: `_SUMMARY_RENDERERS` has no entry for `("radarr", "lookup")`, so the unflagged default and `--verbose` continue to emit the verbatim upstream payload with the literal `monitored` key (REQ-4 AC4)
    - Do NOT touch the `_get(...)` call signature or the `params` dict
    - _Requirements: REQ-4 AC2, REQ-4 AC3, REQ-4 AC4_
  - [x] 4.2 Append the verbatim REQ-5 phrase to the `cmd_lookup` docstring in `arr_cli/radarr.py` (lines ~262–268)
    - Add a new paragraph to the existing docstring containing exactly: *"the `monitored` field on these records is the source default (TVDB for Sonarr, TMDB for Radarr), not the user's library state"* (verbatim, with the backticks around `monitored`)
    - Keep all existing wording; append rather than rewrite
    - _Requirements: REQ-5 AC2_

- [x] 5. Extend Sonarr unit tests for new routing, columns, and parser
  - [x] 5.1 Add no-id tests to `TestCmdSeries` in `tests/unit/test_sonarr.py` (currently lines ~593–645; insert new tests adjacent to existing ones)
    - `test_series_no_id_hits_series_list_path`: `args = _namespace(series_id=None)`; assert `transport.get` called with `"sonarr"` and `"/api/v3/series"`; assert no `params` arg (REQ-1 AC1, REQ-7 AC1)
    - `test_series_no_id_emits_list_payload`: canned array `[{"title": "X"}, {"title": "Y"}]` round-trips through `output.emit` verbatim (REQ-1 AC3)
    - `test_series_no_id_human_renders_table`: `human=True`; patch `arr_cli.sonarr.output.emit`; assert `columns` is exactly `["title", "year", "monitored", "status", "tvdbId", "seasons"]` (REQ-1 AC2)
    - `test_series_no_id_row_count_matches_payload`: canned payload of N items; rendered output has N data rows (assert via captured stdout row count after `--human`)
    - Keep the existing `test_series_hits_series_path` as the single-fetch regression pin (REQ-3 AC1, REQ-7 AC2)
    - Reuse the existing helpers `_service_config`, `_namespace`, `_patched_get_payload`, `_capture_stdout`; do NOT introduce new helpers
    - _Requirements: REQ-1 AC1–AC3, REQ-3 AC1, REQ-7 AC1, REQ-7 AC2, REQ-7 AC7_
  - [x] 5.2 Add `defaultMonitored`-column tests to `TestCmdLookup` in `tests/unit/test_sonarr.py` (currently lines ~539–589; append new tests to the class)
    - `test_lookup_human_default_monitored_column`: canned payload `[{"title": "X", "monitored": true, "tvdbId": 1, "year": 2020}]`; patch `arr_cli.sonarr.output.emit`; assert `kwargs["columns"]` contains `"defaultMonitored"` and does NOT contain the bare string `"monitored"` (REQ-4 AC1, REQ-4 AC3, REQ-7 AC5)
    - `test_lookup_json_keeps_monitored_key`: `human=False`; canned payload with `{"monitored": True}` round-trips unchanged through the JSON path (literal `monitored` key still present in stdout) (REQ-4 AC4)
    - Reuse `_service_config`, `_namespace`, `_patched_get_payload`, `_capture_stdout`; do NOT introduce new helpers
    - _Requirements: REQ-4 AC1, REQ-4 AC3, REQ-4 AC4, REQ-7 AC5_
  - [x] 5.3 Add the parser regression test to `TestBuildSonarrParser` in `tests/unit/test_sonarr.py` (currently lines ~684–793; insert next to existing `test_series_parses_with_id`)
    - `test_series_parses_without_id`: `parser.parse_args(["series"])` succeeds; `args.series_id is None` (REQ-3 AC1, REQ-7 AC2)
    - Update the existing `test_series_requires_id` (currently asserts `SystemExit`) so it now asserts that `parse_args(["series"])` succeeds (REQ-3 AC1) — OR keep it removed and rely on `test_series_parses_without_id` as the new pin; do not duplicate assertions
    - _Requirements: REQ-3 AC1, REQ-7 AC2_

- [ ] 6. Extend Radarr unit tests for new routing, columns, and parser
  - [ ] 6.1 Add no-id tests to `TestCmdMovie` in `tests/unit/test_radarr.py` (currently lines ~568–642; insert new tests adjacent to existing ones)
    - `test_movie_no_id_hits_movie_list_path`: `args = _namespace(movie_id=None)`; assert `transport.get` called with `"radarr"` and `"/api/v3/movie"`; assert no `params` arg (REQ-2 AC1, REQ-7 AC3)
    - `test_movie_no_id_emits_list_payload`: canned array `[{"title": "A"}, {"title": "B"}]` round-trips through `output.emit` verbatim (REQ-2 AC3)
    - `test_movie_no_id_human_renders_table`: `human=True`; patch `arr_cli.radarr.output.emit`; assert `columns` is exactly `["title", "year", "monitored", "status", "tmdbId", "imdbId"]` (REQ-2 AC2)
    - `test_movie_no_id_row_count_matches_payload`: canned payload of N items; rendered output has N data rows
    - Keep the existing `test_movie_hits_movie_path` as the single-fetch regression pin (REQ-3 AC2, REQ-7 AC4)
    - Reuse the existing helpers in `tests/unit/test_radarr.py`; do NOT introduce new helpers
    - _Requirements: REQ-2 AC1–AC3, REQ-3 AC2, REQ-7 AC3, REQ-7 AC4, REQ-7 AC7_
  - [ ] 6.2 Add `defaultMonitored`-column tests to `TestCmdLookup` in `tests/unit/test_radarr.py` (currently lines ~510–565; append new tests to the class)
    - `test_lookup_human_default_monitored_column`: canned payload `[{"title": "X", "monitored": true, "tmdbId": 1, "year": 1999}]`; patch `arr_cli.radarr.output.emit`; assert `kwargs["columns"]` contains `"defaultMonitored"` and does NOT contain the bare string `"monitored"` (REQ-4 AC2, REQ-4 AC3, REQ-7 AC6)
    - `test_lookup_json_keeps_monitored_key`: `human=False`; canned payload with `{"monitored": True}` round-trips unchanged through the JSON path (REQ-4 AC4)
    - Reuse the existing helpers; do NOT introduce new helpers
    - _Requirements: REQ-4 AC2, REQ-4 AC3, REQ-4 AC4, REQ-7 AC6_
  - [ ] 6.3 Add the parser regression test to `TestBuildRadarrParser` in `tests/unit/test_radarr.py` (currently lines ~659–758; insert next to existing `test_movie_parses_with_id`)
    - `test_movie_parses_without_id`: `parser.parse_args(["movie"])` succeeds; `args.movie_id is None` (REQ-3 AC2, REQ-7 AC4)
    - Update the existing `test_movie_requires_id` (currently asserts `SystemExit`) so it now asserts that `parse_args(["movie"])` succeeds — OR remove it and rely on `test_movie_parses_without_id`; do not duplicate assertions
    - _Requirements: REQ-3 AC2, REQ-7 AC4_

- [ ] 7. Document the SKILL.md lookup-recipe follow-up
  - [ ] 7.1 Add an inline TODO note in `arr_cli/sonarr.py` next to `cmd_lookup` (lines ~284–304)
    - One-line comment: `# TODO(REQ-6): when an operator workspace contains media-cli/SKILL.md, update the Sonarr lookup recipe to call out that the monitored column reflects the TVDB source default, not the user's library state.`
    - No code change; documentation follow-up only
    - This is a sandbox no-op — `find / -path '*media-cli/SKILL.md'` returns no results in this sandbox
    - _Requirements: REQ-6 AC1_
  - [ ] 7.2 Add the analogous inline TODO note in `arr_cli/radarr.py` next to `cmd_lookup` (lines ~262–282)
    - One-line comment: `# TODO(REQ-6): when an operator workspace contains media-cli/SKILL.md, update the Radarr lookup recipe to call out that the monitored column reflects the TMDB source default, not the user's library state.`
    - No code change; documentation follow-up only
    - _Requirements: REQ-6 AC2_

- [ ] 8. Run the full CI gate to confirm no regressions
  - [ ] 8.1 Run `make ci` from the repo root (`/workspace/projects/media-cli`)
    - Equivalent to `make test && make secret-scan && make smoke-dry`
    - All three must exit 0 before the change is considered done (REQ-8 AC1, REQ-8 AC4)
    - No new dependencies, no new console scripts, no new config keys (REQ-8 NFRs)
    - _Requirements: REQ-8 AC1, REQ-8 AC4_
  - [ ] 8.2 Run `make lint` from the repo root
    - `py_compile` sweep over `arr_cli/` and `tests/`
    - Must exit 0; confirms the new branches use only existing Python ≥ 3.11 syntax (REQ-8 AC2)
    - _Requirements: REQ-8 AC2_
  - [ ] 8.3 Run `scripts/smoke.sh --dry-run` (or `make smoke-dry`)
    - The grammar check invokes `sonarr --help` and `radarr --help`; both must list `series` and `movie` (REQ-8 AC3)
    - Verify the help text for `series` / `movie` reads as a single description covering both the no-id and with-id invocations (mirrors the existing `calendar` help text shape)
    - _Requirements: REQ-8 AC3_
