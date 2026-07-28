# Implementation Plan

## Task Overview

Implement the human-columns-match-summary fix: (1) add dot-path traversal to `human._row_from_mapping` in `arr_cli/facade/output.py` so column tokens like `playing.type` resolve against nested summary fields; (2) rewrite 14 per-service `columns = [...]` blocks in `arr_cli/{jellyfin,radarr,sonarr,seerr}.py` to use summary-shape keys (dot-path where nested, flat where flat); (3) add a parametrized regression net in `tests/unit/test_output.py` that pins every column-block → summary-shape alignment so future drift trips `make ci`. The maintainerr `cmd_pending` columns block is already aligned and SHALL NOT be modified. The fix is local to those files plus the regression net — no other code, no new dependencies, no `_SUMMARY_RENDERERS` edits, no `_summary_*` renderer body edits, no `emit()` edits.

Implementation order:

1. Task 1: dot-path traversal in `human._row_from_mapping` (small, isolated, immediately testable).
2. Task 2: 14 per-service columns-block rewrites (4 services, 14 functions).
3. Task 3: regression net (3 new test classes parametrized over `_SUMMARY_RENDERERS.keys()`).
4. Task 4: verify `make ci` green end-to-end + `make lint` passes.

## Tasks

- [x] 1. Add dot-path traversal to `human._row_from_mapping`
  - [x] 1.1 Modify `_row_from_mapping` in `arr_cli/facade/output.py` to walk dot-separated column tokens
    - Open `/workspace/projects/media-cli/arr_cli/facade/output.py` and locate `_row_from_mapping` (current location: line 280).
    - Current body: `return [_stringify(item.get(column)) for column in columns]`.
    - Replace with a walk: for each column token, split on `.`; for each segment, if current is a `Mapping` subscript `current[segment]`; if a `Sequence` parse the segment as `int(segment)` and subscript `current[int(segment)]`; on `KeyError`/`IndexError`/`TypeError` return `None` (rendered as `<null>` by `_stringify`).
    - Keep the function signature unchanged: `def _row_from_mapping(item: Mapping[str, Any], columns: Sequence[str]) -> list[str]:` (no new public API; the helper stays private to the module).
    - Single-segment tokens (no `.`) reduce to the pre-change `item.get(column)` behavior — strict subset, no regression for the 9 flat-summary commands.
    - Use a small private walker helper inside the function (e.g. local `_walk(item, segments, idx)` or just a `for` loop) — do NOT introduce a new module-level function. Keep the change ≤ 10 lines.
    - Two-space indent, type hints on any new local binding, no new imports.
    - _Requirements: REQ-16 (dot-path traversal; mapping/sequence/error semantics)_
  - [x] 1.2 Add unit tests for the dot-path traversal in `tests/unit/test_output.py`
    - Open `/workspace/projects/media-cli/tests/unit/test_output.py` and append a new class `TestDotPathTraversal` (alongside the existing `TestEmitHumanSummarizeRoute`, `TestEmitHumanVerbatimFallback`, etc.).
    - Reuse the existing `_capture_stdout` helper and the existing `human` import.
    - Five test methods (per Req 16 AC3-AC6 + Req 18 AC3 AC5):
      1. `test_dot_path_walks_one_level_mapping` — `human([{"a": {"b": 1}}], columns=["a.b"])` produces a table containing `1` in the `a.b` cell.
      2. `test_dot_path_returns_null_on_type_error_at_intermediate` — `human([{"a": {"b": 1}}, {"a": None}], columns=["a.b"])` produces a table where the second row's `a.b` cell is `<null>`.
      3. `test_non_dot_token_still_uses_flat_lookup` — `human([{"a": {"b": 1}}], columns=["a"])` produces a table where the `a` cell is the mapping summary `<1 keys>`.
      4. `test_dot_path_playing_type_jellyfin_now_shape` — `human([{"playing": {"type": "Episode"}}], columns=["playing.type"])` produces `Episode` in the cell.
      5. `test_dot_path_with_sequence_int_segment` — `human([{"items": [{"id": 7}]}], columns=["items.0.id"])` produces `7` in the cell (covers the sequence-with-int-parsed branch).
    - Each test uses `io.StringIO` capture via `contextlib.redirect_stdout` (existing pattern); no live HTTP.
    - _Requirements: REQ-16 AC3-AC6, REQ-18 AC3_

- [ ] 2. Rewrite 14 per-service `columns = [...]` blocks to summary-shape keys
  - [ ] 2.1 Rewrite `arr_cli/jellyfin.py` columns blocks (5 commands)
    - Open `/workspace/projects/media-cli/arr_cli/jellyfin.py`.
    - `cmd_now` (lines ~165–180): replace `columns = ["DeviceName", "UserName", "NowPlayingItem.Name", "NowPlayingItem.SeriesName", "PlayState"]` with `columns = ["user", "device", "client", "playing.type", "playing.name", "playing.series", "playing.season", "playing.episode", "progress.position_ticks", "progress.is_paused"]`. Update the comment block above to reflect summary-shape semantics.
    - `cmd_resume` (line ~190): replace `columns = ["Name", "Type", "ProductionYear", "SeriesName", "UserData"]` with `columns = ["Name", "Type", "ProductionYear", "SeriesName", "UserData.PlaybackPositionTicks", "UserData.PlayCount"]`. Update the comment block.
    - `cmd_recent` (line ~205): replace `columns = ["Name", "Type", "ProductionYear", "SeriesName", "UserData"]` with `columns = ["Name", "Type", "ProductionYear", "SeriesName", "UserData.LastPlayedDate"]`. Update the comment block.
    - `cmd_latest` (line ~245): replace `columns = ["Name", "Type", "ProductionYear", "SeriesName", "DateCreated"]` — already matches summary keys, confirm and leave (or drop verbatim-only tokens if any remain).
    - `cmd_favorites` (line ~285): replace `columns = ["Name", "Type", "ProductionYear", "SeriesName"]` — already matches summary keys, confirm and leave.
    - DO NOT touch `cmd_nextup`, `cmd_search`, `cmd_item` (verbatim-only; their columns describe the verbatim payload by design per Req 17 AC3).
    - _Requirements: REQ-1, REQ-2, REQ-3, REQ-4, REQ-5_
  - [ ] 2.2 Rewrite `arr_cli/radarr.py` columns blocks (3 commands)
    - Open `/workspace/projects/media-cli/arr_cli/radarr.py`.
    - `cmd_wanted` (line ~180): replace `columns = ["title", "year", "movieFile", "monitored"]` with `columns = ["title", "year", "tmdbId", "monitored"]` (drop `movieFile` which the summary does not emit).
    - `cmd_queue` (line ~195): replace `columns = ["title", "status", "trackedDownloadStatus", "size", "sizeleft"]` — already matches summary keys, confirm and leave.
    - `cmd_recent` (line ~215): replace `columns = ["movie.title", "movie.year", "eventType", "date"]` — already matches summary keys (nested), confirm and leave.
    - DO NOT touch `cmd_calendar`, `cmd_lookup`, `cmd_movie` (verbatim-only; per Req 17 AC3).
    - _Requirements: REQ-6, REQ-7, REQ-8_
  - [ ] 2.3 Rewrite `arr_cli/sonarr.py` columns blocks (3 commands)
    - Open `/workspace/projects/media-cli/arr_cli/sonarr.py`.
    - `cmd_wanted` (line ~210): replace `columns = ["title", "seasonNumber", "episodeNumber", "airDate", "monitored"]` — already matches summary keys, confirm and leave (drop any verbatim-only tokens if present).
    - `cmd_queue` (line ~225): replace `columns = ["title", "status", "trackedDownloadStatus", "size", "sizeleft"]` — already matches summary keys, confirm and leave.
    - `cmd_recent` (line ~245): replace `columns = ["series.title", "episode.title", "eventType", "date"]` — already matches summary keys (nested), confirm and leave.
    - DO NOT touch `cmd_calendar`, `cmd_lookup`, `cmd_series` (verbatim-only; per Req 17 AC3).
    - _Requirements: REQ-9, REQ-10, REQ-11_
  - [ ] 2.4 Rewrite `arr_cli/seerr.py` columns blocks (3 commands)
    - Open `/workspace/projects/media-cli/arr_cli/seerr.py`.
    - `cmd_requests` (line ~215): replace `columns = ["title", "type", "status", "createdAt", "requestedBy.displayName"]` — already matches summary keys (nested `requestedBy.displayName`), confirm and leave.
    - `cmd_search` (line ~250): replace `columns = ["title", "mediaType", "releaseDate", "mediaInfo.tmdbId"]` — already matches summary keys (nested), confirm and leave.
    - `cmd_available` (line ~275): replace `columns = ["title", "mediaType", "releaseDate", "mediaInfo.status"]` — already matches summary keys (nested), confirm and leave.
    - DO NOT touch `cmd_request_count`, `cmd_media`, `cmd_user` (verbatim-only or scalar; per Req 17 AC3).
    - _Requirements: REQ-12, REQ-13, REQ-14_
  - [ ] 2.5 Verify `arr_cli/maintainerr.py` is untouched
    - `arr_cli/maintainerr.py:cmd_pending` columns block `["title", "mediaCount", "deleteAfterDays", "isOnHold"]` already aligns with `_summary_maintainerr_pending`. Per Req 15 AC1-AC2, the contributor SHALL NOT rewrite it. Confirm via `git diff arr_cli/maintainerr.py` shows no changes.
    - `cmd_storage` and `cmd_health` are also verbatim-only / scalar; per Req 17 AC3 they SHALL NOT be touched.
    - _Requirements: REQ-15, REQ-17 AC3_

- [ ] 3. Add regression net in `tests/unit/test_output.py`
  - [ ] 3.1 Add `TestColumnsBlockMatchesSummaryShape` class
    - Append to `/workspace/projects/media-cli/tests/unit/test_output.py` (after `TestDotPathTraversal` from Task 1.2).
    - Parametrize over `arr_cli.facade.output._SUMMARY_RENDERERS.keys()` (15 entries).
    - For each `(service, command)`:
      1. Extract the per-handler `columns = [...]` literal by importing the per-service module and either (a) using `inspect.getsource` + `ast.literal_eval` on the `cmd_*` function body to find the `columns = [...]` assignment, or (b) refactoring each `cmd_*` to expose `COLUMNS` as a module-level constant after the rewrite in Task 2. Prefer (b) — it's cleaner and reusable.
      2. Run the summary renderer on a minimal realistic input (`_SUMMARY_RENDERERS[(service, command)](synthetic_payload)`) to derive the summary keys.
      3. Compute the "expected keys" set: top-level keys of the summary items, plus dot-joined nested-dict keys (e.g. `playing.type` for `{playing: {type: ...}}`).
      4. Assert that every column in the handler's `columns = [...]` block is either equal to or a substring of an expected key.
    - On failure, the assertion message SHALL name the offending `(service, command)` and the offending column key.
    - _Requirements: REQ-18 AC1, AC7, AC8_
  - [ ] 3.2 Add `TestHumanRendersNonNullRowsForSizeToSummary` class
    - Append to `tests/unit/test_output.py` after `TestColumnsBlockMatchesSummaryShape`.
    - Parametrize over `_SUMMARY_RENDERERS.keys()`.
    - For each `(service, command)`:
      1. Build a synthetic payload that, when run through `_summary_<svc>_<cmd>`, populates every summary field with a non-`None` primitive value (string for text, int for counts/IDs, bool for booleans).
      2. Run `emit(synthetic_payload, human_mode=True, verbose_mode=False, service=svc, command=cmd, stream=io.StringIO())` via the existing `_capture_stdout` helper.
      3. Parse the rendered table (header line + data rows; the existing pattern uses `_format_row` internals — or split on newlines and parse the data rows as fixed-width columns, accepting some tolerance).
      4. For each column in the handler's `columns = [...]` block, assert that at least one data row's cell for that column is NOT `<null>`.
    - _Requirements: REQ-18 AC2, AC7, AC8_
  - [ ] 3.3 Add class-level rationale comment to the new regression classes
    - In `tests/unit/test_output.py`, add a one-line class docstring (or leading comment) on each of `TestColumnsBlockMatchesSummaryShape`, `TestHumanRendersNonNullRowsForSizeToSummary`, and `TestDotPathTraversal` documenting the rationale: "for each `_SUMMARY_RENDERERS` key, every column key in the corresponding handler's `columns = [...]` block is a substring of (or equal to) a top-level key OR a dot-joined nested-dict key in the summary shape — so future drift trips the test".
    - _Requirements: REQ-18 AC4_

- [ ] 4. Verify CI gates and untouched-files contract
  - [ ] 4.1 Run `make ci` and `make lint` locally and confirm both pass
    - Execute `make ci` (== `make test && make secret-scan && make smoke-dry`) and confirm all three stages exit 0.
    - Execute `make lint` and confirm the `py_compile` sweep stays green for the touched files (`arr_cli/facade/output.py`, `arr_cli/{jellyfin,radarr,sonarr,seerr}.py`, `tests/unit/test_output.py`).
    - If any test fails, fix it in the same patch (do NOT loosen assertions).
    - _Requirements: REQ-17 AC4, NFR-Reliability_
  - [ ] 4.2 Confirm the touched-files list matches the design's scope
    - `git status --porcelain` SHALL list exactly: `arr_cli/facade/output.py`, `arr_cli/jellyfin.py`, `arr_cli/radarr.py`, `arr_cli/sonarr.py`, `arr_cli/seerr.py`, `tests/unit/test_output.py` (plus possibly the three new spec files under `.specs/human-columns-match-summary/`).
    - `arr_cli/maintainerr.py` SHALL NOT appear in the diff (its columns block is already aligned per Req 15).
    - `_SUMMARY_RENDERERS` dispatch table body SHALL NOT change (same 15 entries).
    - No `_summary_*` renderer body SHALL change (all 17 `_summary_<svc>_<cmd>` functions unchanged in their output shape).
    - `emit()` body SHALL NOT change.
    - `pyproject.toml` SHALL NOT change.
    - Verify with `git diff --stat` and `git diff arr_cli/facade/output.py | grep -E "_summary_|_SUMMARY_RENDERERS|def emit"` returns no matches in the body of those functions.
    - _Requirements: REQ-17 AC4, NFR-Security, NFR-Reliability_
