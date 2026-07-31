# Bug Report

## Bug Summary

`sonarr --human lookup "<term>"` and `radarr --human lookup "<term>"` render the trailing flag column as `<null>` for every row. The bug was introduced in PR #7 (workstream `library-list-with-monitored-clarification`, commits `1691d35` and `d37a052`) which renamed the `--human` column from `monitored` to `defaultMonitored` on both `sonarr lookup` and `radarr lookup`. The Sonarr `/api/v3/series/lookup` and Radarr `/api/v3/movie/lookup` JSON payloads still return the field under the key `monitored`, so the renderer's `item.get(column)` call resolves to `None` for every row and the table column is uniformly useless.

The bug is cosmetic-looking (one column shows `<null>`) but it actually breaks the operator's mental model: the column was *meant* to surface the candidate-source default, and PR #7 correctly tried to rename it to communicate that. The renaming was the right intent; the wrong assumption was that the JSON key would also need to be renamed. The underlying JSON key is fixed by the upstream API and cannot be renamed by this CLI.

## Bug Details

### Expected Behavior

Running `sonarr --human lookup "Doctor Who"` should render a tabular view whose last column shows the candidate-source default (TVDB's `monitored` flag) for each candidate row — e.g. `True` / `False` — and where present, the row's numeric Sonarr `id` should make it visibly clear whether the candidate is already in the user's library (id present) or merely a candidate (id absent, placeholder `added='0001-01-01T00:01:00Z'`, no `path`).

Running `radarr --human lookup "dune"` should render the same shape for movies: the last column shows TMDB's `monitored` flag (the candidate-source default), and an `id` column makes the library-vs-candidate distinction visible.

The intended column order, per the fix being proposed:
`title, year, tvdbId/tmdbId, tvMazeId/imdbId, id, monitored`

### Actual Behavior

The trailing column (the one PR #7 named `defaultMonitored`) renders as `<null>` on every row because the renderer does `item.get("defaultMonitored")` and the upstream JSON does not contain that key. The column is silently useless and the operator cannot tell the source-default flag from anything else.

The rows' `monitored` field is still present in the raw JSON payload (the `--verbose` / default-JSON path is unaffected by the rename), so the data is there — only the `--human` column header is misaligned with the JSON key.

The fix-history tests `test_lookup_human_default_monitored_column` in `tests/unit/test_sonarr.py` and `tests/unit/test_radarr.py` are the bug's own tests: they assert the column list contains `defaultMonitored` and does NOT contain `monitored`. Those tests pass and lock the bug in.

### Steps to Reproduce

1. From a checked-out `media-cli` working tree on any branch, run `sonarr --human lookup "Doctor Who"`.
2. Observe the rendered table. The trailing column header reads `defaultMonitored` and every cell in that column reads `<null>`.
3. Compare with `sonarr --human lookup "Doctor Who"`'s raw JSON output (run without `--human` or with `--verbose`). The JSON payload contains `"monitored": true/false` on each row — the data is there.
4. Repeat with `radarr --human lookup "dune"` and observe the same `<null>` column on the rendered table; the raw JSON payload again contains `"monitored"` on each row.
5. Confirm with the existing tests: `pytest tests/unit/test_sonarr.py -k default_monitored` and `pytest tests/unit/test_radarr.py -k default_monitored` both pass, asserting the column list contains `defaultMonitored` and not `monitored`. These tests are the bug's own regression net.

## Impact Assessment

### Severity

- [ ] Critical - System unusable
- [ ] High - Major functionality broken
- [x] Medium - Feature impaired but workaround exists
- [ ] Low - Minor issue or cosmetic

The actual data is still accessible via the JSON path (drop `--human`, or use `--verbose`), so the CLI is not "unusable". However, the `--human` column PR #7 introduced is uniformly useless on the lookup path, and the silent `None` resolution misleads the operator about what the column is supposed to show. This is also a regression from the pre-PR-#7 behavior, where the same column at least rendered `true`/`false` (under the arguably-misleading name `monitored`). The fix-history tests in `tests/unit/test_sonarr.py` and `tests/unit/test_radarr.py` lock the bug in, so without a follow-up the regression will not be caught by CI.

### Affected Features

- `sonarr --human lookup <term>` — trailing column shows `<null>` on every row.
- `radarr --human lookup <term>` — trailing column shows `<null>` on every row.
- `sonarr lookup <term>` (no `--human`) and `--verbose` — unaffected; raw JSON still carries the literal `monitored` key and `_SUMMARY_RENDERERS` has no entry for `("sonarr", "lookup")` so the verbatim pass-through is intact.
- `radarr lookup <term>` (no `--human`) and `--verbose` — unaffected, for the same reason.
- `sonarr series` and `radarr movie` (no-id library-list paths) — unaffected; their column list still uses `monitored` and that key is correct on the library-list endpoint.
- `sonarr series <id>` and `radarr movie <id>` (single-fetch paths) — unaffected; their column list still uses `monitored` and that key is correct on the single-fetch endpoint.

## Additional Context

### Error Messages

There is no error message. The renderer (`_row_from_mapping` in `arr_cli/facade/output.py`) does `item.get(column)` and then `_stringify(None)`, which renders as the literal string `<null>`. The CLI returns exit code 0 and stdout is a perfectly formed table — it just contains a uniformly `<null>` column. The bug is silent.

### Related Issues

- PR #7 (workstream `library-list-with-monitored-clarification`) introduced the regression. Relevant commits:
  - `1691d35` — `feat(library-list-with-monitored-clarification): [Task 3] rename monitored to defaultMonitored on sonarr lookup --human`
  - `d37a052` — `feat(library-list-with-monitored-clarification): [Task 4] rename monitored to defaultMonitored on radarr lookup --human`
- The matching spec lives at `.specs/library-list-with-monitored-clarification/` (`requirements.md`, `design.md`, `tasks.md`, `review.md`). It carries the original (wrong) assumption that the JSON key would also be renamed, and the per-task test names (`test_lookup_human_default_monitored_column`, `test_lookup_json_keeps_monitored_key`) encoded that assumption.
- The `TODO(REQ-6)` markers in `arr_cli/sonarr.py` (line ~286) and `arr_cli/radarr.py` (line ~266) reference a SKILL.md follow-up that was never completed because no `SKILL.md` exists in the tree. This bug-review includes that follow-up in the implementation plan.

## Analysis

### Investigation Summary

The investigation followed the data path from the rendered table back to the JSON payload:

1. Confirmed `defaultMonitored` appears as a literal column header in the rendered table for both `sonarr lookup` and `radarr lookup` under `--human`.
2. Confirmed the rename is in `arr_cli/sonarr.py` (`cmd_lookup`, line ~312) and `arr_cli/radarr.py` (`cmd_lookup`, line ~291) and is the only difference from the pre-PR-#7 column list.
3. Confirmed the renderer in `arr_cli/facade/output.py` (`_row_from_mapping`, lines ~280–316) does `item.get(column)` for each column token. There is no nested-lookup fallback for `defaultMonitored` because the token does not contain a `.`.
4. Confirmed the upstream `/api/v3/series/lookup` and `/api/v3/movie/lookup` endpoints return the JSON key as `monitored` (not `defaultMonitored`). The Sonarr / Radarr APIs do not expose a `defaultMonitored` field at all — the field is a CLI-side alias proposed in the spec, never a real JSON key.
5. Confirmed `_SUMMARY_RENDERERS` (`arr_cli/facade/output.py`, lines ~894–911) has no entry for `("sonarr", "lookup")` or `("radarr", "lookup")`, so the verbatim pass-through is unaffected and the JSON path still surfaces the literal `monitored` key (this is also asserted by `test_lookup_json_keeps_monitored_key` in both test files).
6. Confirmed the empirical-evidence claim in the task brief: `sonarr series 87` (single-fetch on a library row) and `sonarr lookup "Doctor Who"` (the same series appearing as a candidate) return byte-identical per-season `monitored` for `Doctor Who (2005)`. The library state and the source default are the same field on the lookup endpoint.
7. As a corollary, the disambiguator between a library row and a candidate is the numeric `id` field: a library row has a real `id`, real `added`, and a real `path`; a candidate has no `id` key, the placeholder `added='0001-01-01T00:01:00Z'`, and no `path`. The `monitored` field on the lookup endpoint is the source default in both cases.

The fix is therefore not a JSON rename (the upstream API does not support that) but a column-list revert plus an `id` column to make the library-vs-candidate distinction visible.

### Root Cause

The renderer column name `defaultMonitored` does not match the JSON key `monitored` returned by Sonarr's `/api/v3/series/lookup` and Radarr's `/api/v3/movie/lookup` endpoints. `arr_cli.facade.output._row_from_mapping` does `item.get(column)` for each column token, so every cell resolves to `None` and renders as `<null>`.

The original spec (`library-list-with-monitored-clarification/requirements.md`, REQ-4) was the source of the bug: it assumed the JSON key could be renamed to match the column header. The upstream Sonarr and Radarr APIs do not expose any `defaultMonitored` field — the field is a CLI-side intention, not a real JSON key. The spec's intent was right (the `monitored` field on lookup results is the source default, not the user's library state) but the implementation (renaming the column header) was wrong because the column-header rename has no effect on the JSON-key lookup.

### Contributing Factors

- **Spec assumption was untested against a live API.** REQ-4 AC1/AC2/AC3 assert the column header is `defaultMonitored`; AC4 explicitly says the JSON path is unaffected because `_SUMMARY_RENDERERS` has no entry for lookup. None of the ACs assert that the JSON payload actually contains a `defaultMonitored` key — because it does not. The pre-PR-#7 fixture payloads in the test files use `{"monitored": True}` (the real JSON key), but the new tests assert the column is `defaultMonitored` and merely assume the JSON will adapt. The tests confirm the header rename without confirming the data path.
- **Empirical-evidence check was a single-shot claim in the spec.** The spec notes that `sonarr series <id>` and `sonarr lookup "Doctor Who"` return byte-identical per-season `monitored` for the same series, but this observation was used as a *justification* for the rename rather than as a stop sign that the rename was wrong. The empirical evidence in fact contradicts the assumption that the column needed renaming at the JSON level.
- **The fix-history tests lock the bug in.** `test_lookup_human_default_monitored_column` (both test files) asserts the column list contains `defaultMonitored` and does NOT contain `monitored`. Replacing those tests is part of the fix, not a side effect of it.
- **No SKILL.md exists.** The `TODO(REQ-6)` markers in both `arr_cli/sonarr.py` and `arr_cli/radarr.py` reference a SKILL.md follow-up that was deferred because no `media-cli/SKILL.md` exists in the tree. The fix should either create SKILL.md with the lookup recipes (per REQ-6 AC1/AC2) or remove the TODOs and record the SKILL.md follow-up as out-of-scope for this bug fix.

## Technical Details

### Affected Code Locations

- **File**: `arr_cli/sonarr.py`
  - **Function/Method**: `cmd_lookup`
  - **Lines**: ~292–315 (the `columns` literal at line ~312 contains `"defaultMonitored"`; the preceding docstring at lines ~287–291 contains the source-default clarification that should stay)
  - **Issue**: The `columns` literal names `defaultMonitored` but the upstream JSON returns `monitored`. `output.emit` → `_row_from_mapping` does `item.get("defaultMonitored")` and renders `<null>` for every row.

- **File**: `arr_cli/radarr.py`
  - **Function/Method**: `cmd_lookup`
  - **Lines**: ~268–295 (the `columns` literal at line ~291 contains `"defaultMonitored"`; the preceding docstring at lines ~263–267 contains the source-default clarification that should stay)
  - **Issue**: Same as Sonarr — the renderer can't find `defaultMonitored` in the JSON payload and renders `<null>`.

- **File**: `arr_cli/facade/output.py`
  - **Function/Method**: `_row_from_mapping`
  - **Lines**: ~280–316
  - **Issue**: Not a bug per se — the renderer is correct. `item.get(column)` is the documented lookup path for the `--human` column projection. The fix is on the column-list side, not the renderer side. The dot-path fallback at lines ~301–314 only kicks in for tokens containing a `.`, so it does not rescue `defaultMonitored`.

- **File**: `tests/unit/test_sonarr.py`
  - **Function/Method**: `TestCmdLookup.test_lookup_human_default_monitored_column`
  - **Lines**: ~587–609
  - **Issue**: This test asserts the column list contains `defaultMonitored` and does NOT contain `monitored`. It is the bug's own regression net and must be replaced.

- **File**: `tests/unit/test_sonarr.py`
  - **Function/Method**: `TestCmdLookup.test_lookup_json_keeps_monitored_key`
  - **Lines**: ~610–624
  - **Issue**: This test is correct as written (it asserts the JSON path preserves the literal `monitored` key) and should stay.

- **File**: `tests/unit/test_radarr.py`
  - **Function/Method**: `TestCmdLookup.test_lookup_human_default_monitored_column`
  - **Lines**: ~562–584
  - **Issue**: Same as the sonarr test — locks the bug in. Must be replaced.

- **File**: `tests/unit/test_radarr.py`
  - **Function/Method**: `TestCmdLookup.test_lookup_json_keeps_monitored_key`
  - **Lines**: ~585–599
  - **Issue**: Correct as written (asserts the JSON path preserves the literal `monitored` key), should stay.

- **File**: `arr_cli/sonarr.py` (TODO marker)
  - **Function/Method**: `cmd_lookup` (preceding TODO comment)
  - **Lines**: ~286
  - **Issue**: TODO(REQ-6) placeholder for a SKILL.md follow-up that the bug-fix plan must address (either create the file or remove the TODO).

- **File**: `arr_cli/radarr.py` (TODO marker)
  - **Function/Method**: `cmd_lookup` (preceding TODO comment)
  - **Lines**: ~266
  - **Issue**: Same TODO(REQ-6) placeholder — must be addressed by the bug-fix plan.

### Data Flow Analysis

```
operator terminal
       │
       ▼
sonarr --human lookup "Doctor Who"
       │  (argparse → cmd_lookup via _DISPATCH)
       ▼
transport.get("/api/v3/series/lookup", params={"term": "Doctor Who"})
       │  (Sonarr hit; returns JSON array, each item has "monitored": true/false)
       ▼
output.emit(payload, args, columns=[
    "title", "year", "tvdbId", "tvMazeId", "defaultMonitored"  ← BUG
])
       │
       ▼
human(payload, columns=[...])
       │
       ▼
_row_from_mapping(item, columns)
       │  for column in columns:
       │      current = item.get(column)            ← item.get("defaultMonitored") → None
       │      ... (no dot in token, so no fallback)
       │      row.append(_stringify(None))          ← "<null>"
       ▼
rendered table with one <null> column on every row
```

The JSON path (no `--human`) bypasses `_row_from_mapping` entirely and the upstream payload is emitted verbatim on stdout. The `monitored` key is present in that payload because the upstream API returns it. The bug is purely in the `--human` column projection.

### Dependencies

- **Sonarr `/api/v3/series/lookup`** — upstream endpoint; JSON key is `monitored`, no `defaultMonitored` field is exposed. Cannot be changed by this CLI.
- **Radarr `/api/v3/movie/lookup`** — upstream endpoint; JSON key is `monitored`, no `defaultMonitored` field is exposed. Cannot be changed by this CLI.
- **`arr_cli.facade.output._row_from_mapping`** — the rendering primitive used by `--human`; operates on a flat `item.get(column)` lookup for tokens without a dot. Behaviour is correct as-is.
- **`arr_cli.facade.output._SUMMARY_RENDERERS`** — lookup is intentionally absent from this dispatch table (lookup is a "safe-to-leave-alone" command per the AGENTS.md docstring on `_SUMMARY_RENDERERS`). The JSON path is therefore the verbatim upstream payload. No change is needed here.
- **`pytest` + `unittest.mock`** — the test framework already used by `tests/unit/`. The replacement tests can use the existing `_patched_get_payload`, `_capture_stdout`, and `_namespace` helpers.

## Solution Approach

### Fix Strategy

Drop the `defaultMonitored` rename. Restore `monitored` as the column on both `sonarr lookup` and `radarr lookup` `--human` output. The JSON key is `monitored` (it cannot be changed), and the renderer simply does `item.get("monitored")`, so the column will render `True` / `False` again.

To preserve the spirit of PR #7 (the operator's mental model — "is this a library row or a candidate?") without the misleading rename, add an `id` column to the lookup `--human` column list. The `id` field is the documented disambiguator: a library row has a real numeric `id`, a real `added`, and a real `path`; a candidate has no `id` key, the placeholder `added='0001-01-01T00:01:00Z'`, and no `path`. The `id` cell renders blank when absent (`_stringify(None)` returns the literal string `<null>`, but the surrounding row context makes the empty-id case visually obvious as "candidate" rather than "library row").

Suggested column order:
- `sonarr lookup`: `title, year, tvdbId, tvMazeId, id, monitored`
- `radarr lookup`: `title, year, tmdbId, imdbId, id, monitored`

The docstring clarifying that the `monitored` field reflects the source default (TVDB for Sonarr, TMDB for Radarr) STAYS — the empirical evidence confirms that observation. The clarification is the right thing to surface; the rename was the wrong way to surface it.

The JSON path needs no change; `_SUMMARY_RENDERERS` already returns the payload unchanged for lookup, and the upstream `monitored` key is preserved verbatim.

### Alternative Solutions

1. **Rename the JSON key in the projection layer** (e.g. add a project step that maps `item["monitored"]` → `{"defaultMonitored": item["monitored"]}`). Rejected — this is a heavier change for a cosmetic issue, and the rename misleads operators into thinking the field is meaningfully different from the library-list field. The empirical evidence shows they are the same field on the lookup endpoint.

2. **Keep `defaultMonitored` and add a translation step in `_row_from_mapping`** (e.g. a known-aliases map). Rejected — `_row_from_mapping` is a generic projection primitive; injecting service-specific aliases into it leaks lookup-specific concerns into the facade. The simpler fix is to use the real JSON key as the column name.

3. **Drop the column entirely.** Rejected — the operator does want to see the source default on a candidate row, and the column is the simplest way to surface it. The bug is the header name, not the column's existence.

4. **Leave the rename and add a workaround docstring only.** Rejected — the data is still missing in the rendered table, which makes the column useless. The operator reading the table deserves to see `true`/`false` for the source default, not `<null>`.

### Risks and Trade-offs

- **Column order change is visible to anyone using `--human` on the lookup path.** The pre-PR-#7 order was `title, year, tvdbId, tvMazeId, monitored` (sonarr) and `title, year, tmdbId, imdbId, monitored` (radarr). The fix brings back `monitored` and inserts `id` before it. Operators consuming the table output (e.g. piping into `awk` or `cut`) will see a different column layout. This is acceptable because:
  - the pre-PR-#7 layout is the one operators have been using for the longest;
  - PR #7 was wrong and the regression should be reverted;
  - the addition of `id` is a small, useful surface-area expansion.
- **The renamed column has been in production for a short window** (the PR #7 merge). Operators who learned the new column name will need to be re-onboarded. The SKILL.md recipe update (or creation) is the right place to handle this.
- **Existing fixture-based tests in `tests/unit/test_sonarr.py` and `tests/unit/test_radarr.py` need to be updated to the new column list.** The `test_lookup_human_default_monitored_column` tests are the bug's own tests and must be replaced — not extended — because they assert the wrong column. The `test_lookup_json_keeps_monitored_key` tests are correct and stay.
- **The empirical-evidence claim in the spec is fact correct, but the remediation was wrong.** The fix does not invalidate the spec's observation that the `monitored` field on lookup is the source default; it invalidates the column-rename remediation. The spec's observation is preserved in the `cmd_lookup` docstring (which stays) and in the SKILL.md recipe (which is created or updated).
- **`<null>` vs empty cell for the `id` column.** The renderer renders `None` as the literal string `<null>` (see `_stringify` in `arr_cli/facade/output.py`). For a candidate row with no `id`, the `id` cell will read `<null>`, which is visually unambiguous but does not match the "renders blank when absent" wording in the brief. Two acceptable resolutions: (a) document the `<null>` rendering as the expected candidate-row indicator; (b) special-case the `id` column to render blank rather than `<null>` in `_row_from_mapping`. Option (a) is consistent with how the rest of the table renders missing values and avoids special-casing. The plan below adopts option (a).
- **No write-path or auth changes.** The fix is a column-list revert and an `id` insertion. No facility in `arr_cli.facade` is touched. The exit-code contract is unchanged.

## Implementation Plan

### Changes Required

1. **Change 1**: Restore `monitored` as the column on `sonarr lookup` `--human` output and add `id` before it.
   - File: `arr_cli/sonarr.py`
   - Modification: In `cmd_lookup` (lines ~292–315), replace the `columns` literal:
     - FROM: `["title", "year", "tvdbId", "tvMazeId", "defaultMonitored"]`
     - TO:   `["title", "year", "tvdbId", "tvMazeId", "id", "monitored"]`
   - The `TODO(REQ-6)` comment at line ~286 and the REQ-5 source-default clarification in the docstring at lines ~287–291 stay (the clarification is correct).

2. **Change 2**: Restore `monitored` as the column on `radarr lookup` `--human` output and add `id` before it.
   - File: `arr_cli/radarr.py`
   - Modification: In `cmd_lookup` (lines ~268–295), replace the `columns` literal:
     - FROM: `["title", "year", "tmdbId", "imdbId", "defaultMonitored"]`
     - TO:   `["title", "year", "tmdbId", "imdbId", "id", "monitored"]`
   - The `TODO(REQ-6)` comment at line ~266 and the REQ-5 source-default clarification in the docstring at lines ~263–267 stay.

3. **Change 3**: Address the `TODO(REQ-6)` SKILL.md follow-up.
   - File: `SKILL.md` (create at the repo root if absent; otherwise edit in place)
   - Modification: Add a lookup recipe for both `sonarr` and `radarr` that surfaces the source-default semantics of the `monitored` column. Per REQ-6 AC1/AC2:
     - "the `monitored` column in the rendered table reflects the source default (TVDB), not the user's library state" for sonarr.
     - "the `monitored` column in the rendered table reflects the source default (TMDB), not the user's library state" for radarr.
   - If creating `SKILL.md` is out-of-scope for this bug fix, the alternative is to remove the `TODO(REQ-6)` comment from both `arr_cli/sonarr.py` and `arr_cli/radarr.py` and record the SKILL.md follow-up as a separate workstream. The implementation plan should prefer the creation path because the TODOs have been outstanding since PR #7.

4. **Change 4**: Replace the `test_lookup_human_default_monitored_column` tests in `tests/unit/test_sonarr.py` and `tests/unit/test_radarr.py` with tests that assert the new column list. See the Testing Strategy section for the exact assertions.

5. **Change 5**: Add regression tests for the `id` column rendering as `<null>` on candidate rows (no `id` key) and as the numeric `id` on library rows. See the Testing Strategy section.

### Testing Strategy

The fix-history tests (`test_lookup_human_default_monitored_column` in both test files) must be replaced with the new column-list assertions. The replacement tests should:

1. **Assert the new column list for `sonarr lookup` `--human` output.**
   - File: `tests/unit/test_sonarr.py`
   - Test class: `TestCmdLookup`
   - Test name: `test_lookup_human_column_list` (replace `test_lookup_human_default_monitored_column`)
   - Assertion: `self.assertEqual(columns, ["title", "year", "tvdbId", "tvMazeId", "id", "monitored"])`
   - Assertion: `self.assertNotIn("defaultMonitored", columns)` (regression net against re-introducing the buggy rename)

2. **Assert the new column list for `radarr lookup` `--human` output.**
   - File: `tests/unit/test_radarr.py`
   - Test class: `TestCmdLookup`
   - Test name: `test_lookup_human_column_list` (replace `test_lookup_human_default_monitored_column`)
   - Assertion: `self.assertEqual(columns, ["title", "year", "tmdbId", "imdbId", "id", "monitored"])`
   - Assertion: `self.assertNotIn("defaultMonitored", columns)` (regression net)

3. **Assert `monitored` extracts `item.get("monitored")` from the payload.**
   - File: `tests/unit/test_sonarr.py` and `tests/unit/test_radarr.py`
   - Test class: `TestCmdLookup`
   - Test name: `test_lookup_human_monitored_cell_renders_value`
   - Setup: canned payload `[{"title": "X", "monitored": True, "tvdbId": 1, "year": 2020, "id": 42}]` (sonarr) and the radarr equivalent.
   - Assertion: capture stdout via `_capture_stdout`, parse the rendered table, and assert the `monitored` cell equals `True` (stringified) and the `id` cell equals `42`.

4. **Assert `id` renders blank (or `<null>`) when omitted.**
   - File: `tests/unit/test_sonarr.py` and `tests/unit/test_radarr.py`
   - Test class: `TestCmdLookup`
   - Test name: `test_lookup_human_id_cell_renders_blank_when_absent`
   - Setup: canned payload `[{"title": "X", "monitored": True, "tvdbId": 1, "year": 2020}]` (no `id` key) for sonarr; equivalent for radarr.
   - Assertion: the `id` cell renders as `<null>` (the existing `_stringify(None)` behaviour), confirming the candidate-row visualization.

5. **Fixture-based test for in-library and candidate rows in the same payload.**
   - File: `tests/unit/test_sonarr.py` and `tests/unit/test_radarr.py`
   - Test class: `TestCmdLookup`
   - Test name: `test_lookup_human_distinguishes_library_and_candidate_rows`
   - Setup: canned payload with two rows:
     - Row A (library): `{"title": "Doctor Who (2005)", "year": 2005, "tvdbId": 78804, "tvMazeId": 210, "id": 87, "monitored": true, "added": "2020-01-01T00:00:00Z", "path": "/tv/Doctor Who (2005)"}`
     - Row B (candidate): `{"title": "Doctor Who (2005)", "year": 2005, "tvdbId": 78804, "tvMazeId": 210, "monitored": true, "added": "0001-01-01T00:01:00Z"}` (no `id`, no `path`, placeholder `added`)
   - Assertion: the rendered table contains both rows; row A's `id` column shows `87`; row B's `id` column shows `<null>`; both rows' `monitored` column shows `True`.

6. **Existing `test_lookup_json_keeps_monitored_key` tests stay as-is.** They are correct (the JSON path is unchanged by the bug fix) and act as a regression net against accidentally wiring the rename into the JSON path.

7. **Run `make ci` and `make lint` to confirm no regressions.** Per AGENTS.md §7 step 8, this is the final gate before considering the change done.

8. **Run `pytest tests/unit/test_sonarr.py tests/unit/test_radarr.py` directly to confirm the new tests pass.** The change is hermetic (no live HTTP), so this should complete in well under a second per test.
