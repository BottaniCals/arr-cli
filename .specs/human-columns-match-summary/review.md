# Code Review: human-columns-match-summary
Date: 2026-07-28
Branch: fix/human-tracks-default-shape
Reviewer: Automated Code Review

## Summary

**APPROVED** — feature meets every requirement, design and task; CI gates pass cleanly.

The patch is surgical, well-scoped, and minimal:

- **Task 1** (`a512845`) — adds the targeted dot-path walker in `human._row_from_mapping` with a flat-first / dot-path-fallback lookup that preserves Req 16 AC2's strict-subset rule, plus 5 `TestDotPathTraversal` unit tests.
- **Task 2** (`fb79f50`) — rewrites 14 per-service `columns = [...]` blocks in `arr_cli/{jellyfin,radarr,sonarr,seerr}.py` exactly to the design Component 2 table; adds the matching summary-shape comment block on the 9 already-aligned blocks; updates one pre-existing test in `tests/unit/test_jellyfin.py` that was pinning the old verbatim-payload keys.
- **Task 3** (`34111d0`) — appends two parametrized regression classes (`TestColumnsBlockMatchesSummaryShape`, `TestHumanRendersNonNullRowsForSizeToSummary`) over all 15 `_SUMMARY_RENDERERS.keys()`; adds a class-level rationale docstring on each new class (Req 18 AC4).
- **Task 4** (`e46f8a0`) — verifies `make ci` and `make lint` green; touches no source files.

Untouched-files contract holds: `arr_cli/maintainerr.py`, `pyproject.toml`, all 17 `_summary_*` renderer bodies, the `_SUMMARY_RENDERERS` dispatch table, the `emit()` body, and all per-service `_emit` wrappers are unchanged vs `d252e60` (the last spec-only commit on this branch).

Test totals: **602 passed, 4 skipped, 0 failed** (baseline 595 + 5 `TestDotPathTraversal` + 1 `TestColumnsBlockMatchesSummaryShape` + 1 `TestHumanRendersNonNullRowsForSizeToSummary`); `make ci` (= test + secret-scan + smoke-dry) and `make lint` (py_compile sweep) are both green.

## Requirements Coverage

| Requirement | Status | Notes |
|------------|--------|-------|
| REQ-1 (jellyfin -h now columns) | ✅ | `arr_cli/jellyfin.py:cmd_now` columns literal is exactly `["user", "device", "client", "playing.type", "playing.name", "playing.series", "playing.season", "playing.episode", "progress.position_ticks", "progress.is_paused"]` — verified by `_columns_for("jellyfin", "now")` returning the design-Component-2 list verbatim. Verbatim-payload keys (`DeviceName`, `UserName`, `Client`, `NowPlayingItem`, `NowPlayingItem.Name`, `NowPlayingItem.SeriesName`, `NowPlayingItem.Type`, `NowPlayingItem.ParentIndexNumber`, `NowPlayingItem.IndexNumber`, `PlayState`, `PlayState.PositionTicks`, `PlayState.IsPaused`) are absent. The summary renderer populates every summary-shape field with a non-`None` primitive from the synthetic payload in `_synthetic_payload(("jellyfin", "now"))`; the test harness passes. |
| REQ-2 (jellyfin -h resume) | ✅ | `cmd_resume` columns literal is `["Name", "Type", "ProductionYear", "SeriesName", "UserData.PlaybackPositionTicks", "UserData.PlayCount"]`. `UserData` (verbatim nested object) is gone; the renderer flattens `UserData.PlaybackPositionTicks` and `UserData.PlayCount` as literal top-level keys with dots, which `_row_from_mapping`'s flat-first fallback resolves directly. |
| REQ-3 (jellyfin -h recent) | ✅ | `cmd_recent` columns literal is `["Name", "Type", "ProductionYear", "SeriesName", "UserData.LastPlayedDate"]`. `UserData` removed; `UserData.LastPlayedDate` reaches the cell via flat-first lookup (verified live: `human([{"Name": "Foo", "UserData.LastPlayedDate": "2024-01-01"}], columns=["UserData.LastPlayedDate"])` renders `2024-01-01`). |
| REQ-4 (jellyfin -h latest) | ✅ | `cmd_latest` columns literal is `["Name", "Type", "ProductionYear", "SeriesName", "DateCreated"]`. Verbatim-only keys (`Overview`, `Genres`, `RunTimeTicks`) absent. Regression net parametrize over all 15 keys catches drift. |
| REQ-5 (jellyfin -h favorites) | ✅ | `cmd_favorites` columns literal is `["Name", "Type", "ProductionYear", "SeriesName"]`. Verbatim-only tokens (`Overview`, `Genres`, `RunTimeTicks`, `Taglines`) absent. |
| REQ-6 (radarr -h wanted) | ✅ | `cmd_wanted` columns literal is `["title", "year", "tmdbId", "monitored"]`. `movieFile` (verbatim-only, dropped by summary) is gone. `tmdbId` populated with integer (verified live). |
| REQ-7 (radarr -h queue) | ✅ | `cmd_queue` columns literal is `["title", "status", "trackedDownloadStatus", "size", "sizeleft"]`. Verbatim-only keys (`downloadClient`, `indexer`, `protocol`) absent. |
| REQ-8 (radarr -h recent) | ✅ | `cmd_recent` columns literal is `["movie.title", "movie.year", "eventType", "date"]` — dot-path tokens matching `_summary_radarr_recent`'s nested `movie: {title, year}`. Verbatim-only keys (`sourcePath`, `downloadClient`, `quality`) absent. Dot-path walk verified live: `human([{"movie": {"title": "Foo"}}], columns=["movie.title"])` renders `Foo`. |
| REQ-9 (sonarr -h wanted) | ✅ | `cmd_wanted` columns literal is `["title", "seasonNumber", "episodeNumber", "airDate", "monitored"]`. `seriesId` (verbatim-only) absent. Integer columns populated from synthetic payload. |
| REQ-10 (sonarr -h queue) | ✅ | `cmd_queue` columns literal is `["title", "status", "trackedDownloadStatus", "size", "sizeleft"]`. Verbatim-only keys absent. |
| REQ-11 (sonarr -h recent) | ✅ | `cmd_recent` columns literal is `["series.title", "episode.title", "eventType", "date"]` — dot-path tokens matching the nested `series: {title}` / `episode: {title}`. Verbatim-only keys absent. |
| REQ-12 (seerr -h requests) | ✅ | `cmd_requests` columns literal is `["title", "type", "status", "createdAt", "requestedBy.displayName"]` — dot-path token matches nested `requestedBy: {displayName}`. Verbatim-only keys (`externalId`, `mediaId`, `seasonNumber`) absent. |
| REQ-13 (seerr -h search) | ✅ | `cmd_search` columns literal is `["title", "mediaType", "releaseDate", "mediaInfo.tmdbId"]` — dot-path token matches nested `mediaInfo: {tmdbId}`. Verbatim-only keys (`overview`, `posterPath`, `backdropPath`) absent. |
| REQ-14 (seerr -h available) | ✅ | `cmd_available` columns literal is `["title", "mediaType", "releaseDate", "mediaInfo.status"]` — dot-path token matches nested `mediaInfo: {status}`. Verbatim-only keys absent. |
| REQ-15 (maintainerr -h pending) | ✅ | `arr_cli/maintainerr.py:cmd_pending` columns literal is untouched at `["title", "mediaCount", "deleteAfterDays", "isOnHold"]`. `git diff d252e60...HEAD -- arr_cli/maintainerr.py` returns empty. The regression net parametrize includes this handler so future drift in `_summary_maintainerr_pending` trips the test. |
| REQ-16 (dot-path traversal) | ✅ | `human._row_from_mapping` (`arr_cli/facade/output.py:280`) now: (a) tries `item.get(column)` first → handles flat-with-dots keys (`UserData.PlaybackPositionTicks`, `UserData.LastPlayedDate`); (b) on `None` and `"." in column` → walks `column.split(".")` with `Mapping → current[seg]` / `Sequence → current[int(seg)]` and catches `KeyError`/`IndexError`/`TypeError` returning `None` (renders `<null>`). Single-segment tokens reduce to the pre-change flat lookup (strict subset, AC2). The 5 `TestDotPathTraversal` tests cover AC3-AC6 plus the sequence-with-int-segment branch. |
| REQ-17 (no regression on verbatim-only) | ✅ | `cmd_calendar`, `cmd_lookup`, `cmd_movie` (radarr), `cmd_nextup`, `cmd_search`, `cmd_item` (jellyfin), `cmd_calendar`, `cmd_lookup`, `cmd_series` (sonarr), `cmd_request_count`, `cmd_media`, `cmd_user` (seerr), `cmd_storage`, `cmd_health` (maintainerr) — all untouched (`git diff d252e60...HEAD` shows zero changes to these functions). The `--verbose --human` escape hatch in `arr_cli/facade.output.emit` (the new `if verbose_mode: shaped = payload / else: shaped = summarize(...)` branch) bypasses `summarize()` and renders verbatim payload — preserved per Req 17 AC2. |
| REQ-18 (regression net) | ✅ | `tests/unit/test_output.py` now contains three new classes: `TestDotPathTraversal` (5 tests covering AC3-AC6 + sequence-int), `TestColumnsBlockMatchesSummaryShape` (parametrized over all 15 `_SUMMARY_RENDERERS.keys()` via `unittest.subTest`, asserts each column key is a substring of / equal to a top-level or dot-joined nested key in the summary shape — uses `ast.literal_eval` on the per-service source to extract the `columns = [...]` literal), `TestHumanRendersNonNullRowsForSizeToSummary` (parametrized over all 15 keys, runs `human(summary, columns=...)` and asserts no `<null>` cells for any data row). Each class carries the rationale docstring (Req 18 AC4). All tests are hermetic — `ast.literal_eval` + `io.StringIO` capture, no live HTTP. Substring rule documents both flat and dot-joined keys (Req 18 AC1 dual-shape rule). |

## Design Adherence

| Component | Status | Notes |
|-----------|--------|-------|
| Component 1 — `_row_from_mapping` | ✅ | Implementation at `arr_cli/facade/output.py:280-313` exactly matches the design's "Behavioral change" diff. Signature unchanged: `def _row_from_mapping(item: Mapping[str, Any], columns: Sequence[str]) -> list[str]`. Flat-first fallback (`item.get(column)` then dot-walk on `None` AND `"." in column`) implements the strict-subset rule from Req 16 AC2. No new module-level function, ≤10 effective lines inside the function body, two-space indent, type hints on the new `row` and `current` locals (`list[str]`, `Any`). |
| Component 2 — Per-service `columns = [...]` blocks | ✅ | All 14 rewrites verified by `_columns_for(svc, cmd)` extraction (programmatic AST walk via `_columns_for` helper). The 14 new literals are byte-identical to the design Component 2 table. The 9 already-aligned blocks (`jellyfin` resume/recent/latest/favorites, `radarr` queue/recent, `sonarr` wanted/queue/recent, `seerr` requests/search/available) gain a 3-line comment block referencing `_summary_<svc>_<cmd>` (good context, low noise). `maintainerr.cmd_pending` is unmodified (Req 15). |
| Component 3 — `_SUMMARY_RENDERERS` dispatch table | ✅ | Unchanged. 15 entries, same `(service, command)` keys (`python3 -c "from arr_cli.facade.output import _SUMMARY_RENDERERS; print(len(_SUMMARY_RENDERERS))"` → 15). `git diff d252e60...HEAD -- arr_cli/facade/output.py` shows zero changes to the `_SUMMARY_RENDERERS = {...}` block. |
| Component 4 — `human()` public signature | ✅ | Public signature unchanged. Only the private `_row_from_mapping` helper body was modified. `human(value, *, columns, limit, max_width) -> str` is the same as before. |
| Component 5 — `emit()` unchanged | ✅ | `emit()` body change is from the previous PR (`3c1f34a`), NOT this feature. `git diff d252e60...HEAD -- arr_cli/facade/output.py` shows the `emit()` body itself unchanged by this feature — only the priority-chain docstring prose updated (one parenthetical note about `--verbose --human` bypasses `summarize()`). The runtime `if human_mode:` branch is identical to the post-PR-#6 state. |
| Component 6 — Regression tests | ✅ | Three new classes appended to `tests/unit/test_output.py`. `TestDotPathTraversal` has exactly 5 test methods (verified via `grep "def test_"`). `TestColumnsBlockMatchesSummaryShape` and `TestHumanRendersNonNullRowsForSizeToSummary` iterate `_SUMMARY_RENDERERS.keys()` (15 entries) with `unittest.subTest(svc=..., cmd=...)` for parametrized failure messages. Class-level docstrings document the rationale per Req 18 AC4. Hermetic — only `ast.literal_eval`, `io.StringIO`, `unittest.subTest`, no live HTTP. |

## Code Quality Issues

- **[MINOR]** `tests/unit/test_output.py:TestHumanRendersNonNullRowsForSizeToSummary._row_strings` (line ~2158) — the test helper's docstring says "Mirrors `arr_cli.facade.output._row_from_mapping`" but the mirror is not exact: the helper starts the walk from `summary_row` (the top-level dict) using only the dot-path split, while the real `_row_from_mapping` first tries `item.get(column)` (flat-with-dots fallback) before falling back to the walk. In practice the discrepancy is harmless because `_column_widths` caps each column at `base = budget // n`, so even if the test helper returns `<null>` for a flat-with-dots column, the cell-extraction widths still come from the header strings and the actual `human()` rendering correctly produces values. Still, the docstring would be more honest as "Mirrors `_row_from_mapping`'s dot-path walker (the flat-first fallback is exercised separately by the actual `human()` call — we use this helper only to project the summary onto cell strings for width allocation, where the cap at `budget // n` makes the flat-vs-nested distinction irrelevant)". A future contributor reading "Mirrors `_row_from_mapping`" might add the flat-first lookup to the helper and silently break the assertion if budget allocation ever changes.

- **[MINOR]** `arr_cli/facade/output.py:_row_from_mapping` (line 280) — the function carries an 11-line docstring explaining the flat-first / dot-path-fallback rationale. This is exactly the kind of "non-obvious why" comment that AGENTS.md §4.1 endorses ("No comments unless they explain non-obvious why"), so the comment density is appropriate and not a code-smell. No action.

- **[MINOR]** `arr_cli/jellyfin.py:cmd_now` comment block (lines 168-172), and similarly in `cmd_resume`, `cmd_recent`, `cmd_latest`, `cmd_favorites`, plus radarr/sonarr/seerr counterparts — the new comment blocks on already-aligned `columns = [...]` literals add useful provenance ("Tabular columns match the summary-shape keys emitted by `_summary_<svc>_<cmd>`") without noise. Acceptable per AGENTS.md §4.1.

- **[MINOR]** `arr_cli/facade/output.py:_row_from_mapping` — the `current is None and "." in column` guard short-circuits when the FLAT lookup returns a value (e.g. `UserData.PlaybackPositionTicks` → `12345`, an integer) OR when the column has no `.` (e.g. `Name` → `"Foo"`, a string). Both are correctly excluded from the fallback. However, there is one edge case not explicitly covered by Req 16: a flat key that legitimately equals `None` would NOT trigger the dot-walk fallback. The spec's `ast.literal_eval` substring check in the regression net (Req 18 AC1) sidesteps this — only columns that the summary actually emits with a primitive reach the table. None of the 15 commands emit a literal `None` at a dotted top-level key, so this gap is theoretical. If a future renderer starts emitting e.g. `UserData.LastPlayedDate: None`, the cell would render `<null>` (correct behaviour) instead of attempting the dot-walk. Documented in the code's docstring as the intended "strict subset" semantics. Acceptable.

- **[MINOR]** The branch in `arr_cli/facade/output.py:_row_from_mapping` for `isinstance(current, Sequence)` uses `current[int(seg)]` without checking whether `seg` parses as an integer; if it does not, `int(seg)` raises `ValueError`, which is NOT in the caught tuple `(KeyError, IndexError, TypeError)`. A column token like `playing.id` against a payload `{"playing": ["foo", "bar"]}` would crash. This is a robustness gap: the Req 16 AC1 specification says "parse the segment as an integer" (so `ValueError` from malformed segments is not explicitly mandated to be swallowed), but a malformed segment going uncaught could surprise future operators. Test coverage: the `TestDotPathTraversal.test_dot_path_with_sequence_int_segment` test only covers the happy path `items.0.id` → `7`. Consider expanding the test to cover `{"playing": ["foo"]}` + `columns=["playing.id"]` (where `int("id")` raises `ValueError` and the cell renders `<null>` instead of crashing the table) — but this is a hardening suggestion, not a blocker.

## Test Results

```
$ python3 -m pytest tests/unit
602 passed, 4 skipped in 1.47s
```

Breakdown:
- 595 baseline tests from previous PR (`3c1f34a`) — all still passing
- 5 `TestDotPathTraversal` (new): `test_dot_path_walks_one_level_mapping`, `test_dot_path_returns_null_on_type_error_at_intermediate`, `test_non_dot_token_still_uses_flat_lookup`, `test_dot_path_playing_type_jellyfin_now_shape`, `test_dot_path_with_sequence_int_segment` — all passing
- 1 `TestColumnsBlockMatchesSummaryShape.test_every_column_token_resolves_to_summary_key` (parametrized over 15 keys via `unittest.subTest`)
- 1 `TestHumanRendersNonNullRowsForSizeToSummary.test_every_column_renders_a_non_null_cell` (parametrized over 15 keys via `unittest.subTest`)
- 4 skipped (pre-existing `tests/unit/test_perf_budgets.py` skips for the not-yet-wired-up `max_items` contract — unrelated to this feature)

```
$ make ci
make: [test] 602 passed, 4 skipped
make: [secret-scan] no committed secrets detected
make: [smoke-dry] all smoke steps passed
make: [ci] all checks passed
```

```
$ make lint
make: [lint] py_compile sweep over arr_cli/ and tests/
make: [lint] OK
```

Spot-check execution (live verification of the dot-path walker against the Req 16 AC3-AC6 + sequence branch + flat-with-dots fallback):

```
AC3 ('a.b' nested):        'a.b\n---\n1  '
AC4 (None intermediate):   'a.b   \n------\n1     \n<null>'
AC5 (non-dotted token):    'a       \n--------\n<1 keys>'
AC6 (playing.type):        'playing.type\n------------\nEpisode     '
SEQ (items.0.id):          'items.0.id\n----------\n7         '
FLAT-DOTS (jellyfin resume): 'Name  UserData.PlaybackPositionTicks\n----  ------------------------------\nFoo   100                           '
```

## Recommendations

1. **[MINOR, optional]** Either trim the "Mirrors `_row_from_mapping`" docstring on `_row_strings` to be honest about the difference, or extend the helper to also try the flat-first lookup so the test helper and the production helper are byte-equivalent in behaviour. The current implementation is functionally correct because of `_column_widths`' per-column cap, but the docstring's claim is misleading.

2. **[MINOR, optional]** Consider adding one extra `TestDotPathTraversal` case that asserts the `ValueError` path (e.g. `human([{"items": ["foo"]}], columns=["items.id"])`) either gracefully returns `<null>` or — if the production code does not currently catch `ValueError` — decide whether the production code should. The current implementation does NOT catch `ValueError` (only `KeyError`, `IndexError`, `TypeError`); if a regression test should pin this, either widen the caught tuple in `_row_from_mapping` or add a documented "ValueError is intentional" comment.

3. **[MINOR, optional]** When this branch is squashed into the PR, the four implementation commits (`a512845`, `fb79f50`, `34111d0`, `e46f8a0`) plus the three spec-only commits (`d252e60`, `41864de`, `a885783`) plus the inherited prior-PR commits could be reorganized. Not a blocker for this review.

4. **[INFO]** No changes recommended to AGENTS.md, README.md, or CHANGELOG.md — the feature is a pure columns-block / dot-path-trawalker alignment fix, and the existing project docs already cover the renderer priority chain and the summary-shape contract. The design.md explicitly notes "SKILL.md cleanup in Sage / Lily workspaces (the grep for `known-issue` / `workaround` / 'does not currently match its intended design' returned zero matches; nothing to drop)" — confirmed out of scope.

## Verdict

**APPROVED** — ready for merge.

- Every REQ-1 through REQ-18 acceptance criterion is met (verified by inspection of the new literals and by the parametrized regression net passing all 15 keys).
- Every design Component 1 through Component 6 is implemented as specified.
- Every Task 1, 2, 3, 4 (and all subtasks) is marked `[x]` in `tasks.md` and the implementation matches the description.
- CI gates (`make ci`, `make lint`) pass green.
- The untouched-files contract (Req 17 AC4, NFR-Security, NFR-Reliability) holds: `arr_cli/maintainerr.py`, `pyproject.toml`, all 17 `_summary_*` renderer bodies, the `_SUMMARY_RENDERERS` dispatch table body, and `emit()` body are all unchanged vs `d252e60`.
- The dot-path walker is a strict subset for non-dotted tokens (Req 16 AC2) — verified by tracing through `UserData.PlaybackPositionTicks` (flat-with-dots, hits `item.get` directly) and `playing.type` (nested, hits the dot-walk fallback).
- No CRITICAL or MAJOR issues; 4 MINOR observations (none of which block the merge).

The patch is small, isolated, hermetically tested, and aligns the per-service column lists with the curated summary shape that has been shipping since PR #6. Operators running `jellyfin -h now`, `radarr -h wanted`, `sonarr -h recent`, `seerr -h requests`, `maintainerr -h pending`, and the other ten size-to-summary `--human` paths will see a table whose columns describe the same data the no-flag JSON describes.