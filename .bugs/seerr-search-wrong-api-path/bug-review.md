# Bug Report

## Bug Summary

`seerr search <query>` exits with code `4` (HttpError) on every invocation against a Seer instance because `cmd_search` targets the legacy Overseerr path `GET /api/v1/search/multi`, which Seer does not expose. Seer consolidates search into `GET /api/v1/search`, so the request returns `404 not found` and the operator-visible stderr line names the rejected path.

## Bug Details

### Expected Behavior

`seerr search doctor` (and `seerr search <anything>`) should hit a Seer endpoint that exists on the operator's instance, return a `2xx` response with a list of search hits, and exit `0`. The default output should be the curated per-command summary, `--verbose` should emit the verbatim service JSON, and `--human` should render the documented columns (`title`, `mediaType`, `releaseDate`, `mediaInfo.tmdbId`).

### Actual Behavior

`seerr search doctor` exits `4`. Stderr shows:

```
service=seerr op=/api/v1/search/multi status=404 message=seerr: HTTP 404 for /api/v1/search/multi; body='{"message":"not found","errors":[{"path":"/api/v1/search/multi","message":"not found"}]}'
```

A direct `GET /api/v1/search/multi?query=doctor` against the operator's Seer instance returns the same `404` with the body `{"message":"not found","errors":[{"path":"/api/v1/search/multi","message":"not found"}]}`.

### Steps to Reproduce

1. Configure `arr.conf` with a working `[seerr]` block pointing at a live Seer instance.
2. Run `seerr search doctor`.
3. Observe the exit code `4` and the `404` stderr line above.
4. As a cross-check, run `curl` against the operator's instance at `/api-docs/swagger-ui-init.js` and confirm that `/api/v1/search/multi` is absent while `/api/v1/search` is present.
5. As a second cross-check, run a direct `curl` to `/api/v1/search?query=doctor` (with the documented `X-Api-Key`) and confirm a `2xx` response.

## Impact Assessment

### Severity

- [ ] Critical - System unusable
- [x] High - Major functionality broken
- [ ] Medium - Feature impaired but workaround exists
- [ ] Low - Minor issue or cosmetic

The only search command in the entire `seerr` CLI is completely non-functional on the upstream the project targets (Seer). The other five `seerr` commands (`requests`, `request-count`, `available`, `media`, `user`) work, so the suite as a whole is still useful, but the documented `seerr search <query>` surface is fully broken. No workaround exists inside the CLI; the operator has to drop to `curl` against the correct endpoint.

### Affected Features

- `seerr search <query>` (REQ-10 AC3) — the single command that hits the broken path.
- The default size-to-summary renderer for `seerr search` (`_summary_seerr_search` in `arr_cli/facade/output.py`) — never reached in practice because the HTTP layer fails first.
- `--human` / `--verbose` rendering for `seerr search` — never reached.

## Additional Context

### Error Messages

```text
$ seerr search doctor
# exit code: 4
# stderr:
service=seerr op=/api/v1/search/multi status=404 message=seerr: HTTP 404 for /api/v1/search/multi; body='{"message":"not found","errors":[{"path":"/api/v1/search/multi","message":"not found"}]}'

# direct API call (cross-check):
$ curl -sS -H "X-Api-Key: ***" 'https://<seerr-host>/api/v1/search/multi?query=doctor'
{"message":"not found","errors":[{"path":"/api/v1/search/multi","message":"not found"}]}
```

Captured evidence under `.tmp/test-results/seerr_search_doctor.{cli_default,cli_verbose,api,stderr}.txt` matches the above.

## Analysis

### Investigation Summary

The starting point was the operator-reported failure (`exit 4` on `seerr search doctor`) plus a direct API confirmation that `GET /api/v1/search/multi` returns `404` with a body naming the rejected path. The investigation then mapped the data flow from `seerr search <query>` through argparse → `_DISPATCH["search"]` → `cmd_search` → `transport.get` → the live Seer endpoint, and identified the single line in `cmd_search` that picks the path.

Cross-checks performed:

- `arr_cli/seerr.py` line ~358: `cmd_search` calls `_get("/api/v1/search/multi", ...)`. This is the Overseerr-legacy path; Seer consolidates search into `/api/v1/search`.
- `arr_cli/seerr.py` `build_seerr_parser`: the `search` subparser help text also references `/api/v1/search/multi?query=...` and must move with the fix.
- `README.md` §4.5: the per-service command table documents `seerr search <query>` as `GET /api/v1/search/multi?query=<query>`. This must move with the fix.
- `arr_cli/facade/output.py` `_summary_seerr_search`: the renderer already iterates a top-level list payload. The Seer consolidated `/api/v1/search` endpoint returns a list-shaped result, so the renderer is compatible with the fix and does not need to change.
- `tests/unit/test_seerr.py`: no `TestCmdSearch` class exists today. PR #16 added `TestCmdRequestsPaginatedEnvelope` / `TestCmdRequestsTakesParam` as the model for new handler tests; the fix should add a parallel `TestCmdSearch` class.
- `AGENTS.md` §1 "Seer note": the project explicitly requires endpoint paths to be cross-checked against the live `/api-docs/swagger-ui-init.js` OpenAPI spec rather than historical Overseerr / Jellyseerr docs. This bug is exactly the failure mode the guard paragraph warns about.

### Root Cause

`cmd_search` in `arr_cli/seerr.py` requests the legacy Overseerr path `/api/v1/search/multi`. Seer (the unified fork the CLI targets) does not expose that path; Seer exposes `/api/v1/search` for the same operation. The path string was inherited from the pre-fork Overseerr documentation and was never reconciled against the live Seer OpenAPI spec.

## Technical Details

### Affected Code Locations

- **File**: `arr_cli/seerr.py`
  - **Function/Method**: `cmd_search()`
  - **Lines**: ~342-377 (the `path="/api/v1/search/multi"` argument passed to `_get`)
  - **Issue**: Hardcoded `/api/v1/search/multi` path that does not exist on Seer.

- **File**: `arr_cli/seerr.py`
  - **Function/Method**: `build_seerr_parser()` → `search` subparser help text
  - **Lines**: ~528-538 (the `help="multi-source search by query (GET /api/v1/search/multi?query=...)"` string)
  - **Issue**: Help text advertises the wrong path. Must move with the handler fix to keep `--help` accurate.

- **File**: `README.md`
  - **Section**: §4.5 "Seer (`seerr` — 6 commands)"
  - **Lines**: ~194-229 (the `seerr search <query>` row in the per-service command table)
  - **Issue**: Table row documents the wrong path. Must move with the handler fix per AGENTS.md §7 step 7 ("Update the per-service command table in `README.md §4`").

- **File**: `tests/unit/test_seerr.py`
  - **Section**: New `TestCmdSearch` class (modeled on `TestCmdRequestsTakesParam` added in PR #16)
  - **Issue**: No test asserts the path/params for `cmd_search`, so the drift was not caught by `make test`. Adding the regression test is part of the fix.

- **File**: `CHANGELOG.md`
  - **Section**: `[Unreleased] > Fixed`
  - **Issue**: Needs a one-line entry under the same heading used by PR #16.

### Data Flow Analysis

1. Operator runs `seerr search <query>`.
2. `build_seerr_parser` matches the `search` subparser; `args.query` is the positional `query` string (default `""` if absent).
3. `_dispatch` looks up `_DISPATCH["search"]` → `cmd_search`.
4. `cmd_search` reads `args.query`, builds `params={"query": query}`, and calls `_get("/api/v1/search/multi", ...)`.
5. `_get` delegates to `transport.get(SERVICE_NAME, "/api/v1/search/multi", params=..., cfg=...)`.
6. The facade builds the URL (`base + path + "?query=<urlencoded>"`), injects `X-Api-Key`, sends the request, and receives a `404` from Seer because `/api/v1/search/multi` is not registered.
7. The facade raises `HttpError(service="seerr", op="/api/v1/search/multi", status=404, message=...)`.
8. `main_wrapper` maps the exception to exit code `4` and prints the structured stderr line.

The break is at step 4-5: the path is wrong. Steps 6-8 are correct and surface the failure as the documented `exit 4` + structured stderr line. After the fix, step 4 uses `/api/v1/search`; steps 5-8 proceed normally and the renderer (`_summary_seerr_search`) consumes the Seer response unchanged because the response shape is already a list of `{title, mediaType, releaseDate, mediaInfo}` objects.

### Dependencies

- **Seer (Overseerr + Jellyseerr unified fork)** — upstream service; path comes from its live OpenAPI spec at `/api-docs/swagger-ui-init.js`. The fix must re-verify the path against this spec, per AGENTS.md §1.
- **`requests`**, **`PyYAML`** — already vendored; no new runtime deps.
- **`unittest` / `unittest.mock`** — for the new regression test (mirrors PR #16).
- **`responses`** — already vendored for `tests/unit/`; available if a `responses`-based HTTP-level test is preferred, but the existing seerr tests use `unittest.mock.patch("arr_cli.facade.transport.get")` exclusively, so the new test should follow the same pattern.

## Solution Approach

### Fix Strategy

Minimal, targeted change: point `cmd_search` at the Seer-consolidated `/api/v1/search` path and keep the existing `query` parameter. Update the help text in `build_seerr_parser`, the `README.md` §4.5 row, and the `CHANGELOG.md` `[Unreleased] > Fixed` section in lock-step. Add a `TestCmdSearch` class to `tests/unit/test_seerr.py` modeled on `TestCmdRequestsTakesParam` (added in PR #16) so future drift of this exact line will fail the unit suite.

Other approaches considered and rejected:

- **Add a fallback probe (`/api/v1/search` then `/api/v1/search/multi`)** — mirrors the `cmd_user` two-step pattern, but unnecessary: Seer is the upstream the project targets (AGENTS.md §1), and `/api/v1/search/multi` will not reappear. A probe would add code, exit-code ambiguity, and operator confusion for no benefit.
- **Expose `--page` / `--language` parity with the spec** — the spec accepts `page` and `language`, but neither is required for the documented operator surface ("multi-source search by query"). Out of scope for this fix; can land as a separate enhancement if requested.

## Implementation Plan

### Changes Required

1. **Change 1**: Drop `/multi` from the `cmd_search` path.
   - File: `arr_cli/seerr.py`
   - Modification: in `cmd_search`, change the `_get` path argument from `"/api/v1/search/multi"` to `"/api/v1/search"`. Keep `params={"query": query}` and `op="search"` unchanged. The columns list (`title`, `mediaType`, `releaseDate`, `mediaInfo.tmdbId`) is already correct for the Seer response shape.

2. **Change 2**: Update the `search` subparser help text to match the new path.
   - File: `arr_cli/seerr.py`
   - Modification: in `build_seerr_parser`, replace the `help="multi-source search by query (GET /api/v1/search/multi?query=...)"` string with the equivalent that uses `GET /api/v1/search?query=...`.

3. **Change 3**: Update the per-service command table.
   - File: `README.md` §4.5
   - Modification: change the `seerr search <query>` row from `GET /api/v1/search/multi?query=<query>` to `GET /api/v1/search?query=<query>`.

4. **Change 4**: Add a regression test class for `cmd_search`.
   - File: `tests/unit/test_seerr.py`
   - Modification: add a `TestCmdSearch` class mirroring `TestCmdRequestsTakesParam`. Cover at minimum: (a) handler hits `/api/v1/search` and forwards `params={"query": <value>}`, (b) empty query still hits the endpoint with `params={"query": ""}`, (c) special characters in the query are forwarded raw (encoding is the transport layer's job, verified elsewhere), (d) defensive guard asserting the handler does NOT hit `/api/v1/search/multi` (would catch a regression in seconds).

5. **Change 5**: Add a CHANGELOG entry.
   - File: `CHANGELOG.md` `[Unreleased] > Fixed`
   - Modification: one bullet under the same heading PR #16 used, naming the corrected endpoint and noting the live-spec cross-check that motivated the fix.

### Testing Strategy

Verification levels, in order:

1. **Unit (hermetic)** — `pytest tests/unit/test_seerr.py`. The new `TestCmdSearch` class asserts the path is exactly `/api/v1/search` and the params contain `query`. The defensive guard (`assertNotEqual(..., "/api/v1/search/multi")`) ensures any future copy-paste back to the legacy path fails the suite immediately. No live HTTP; mirrors PR #16.
2. **Lint + secret scan + smoke-dry** — `make ci`. Verifies the package still imports cleanly, the parser still surfaces the six documented commands, no placeholder violation, and the smoke script's grammar check still passes.
3. **Live (opt-in)** — `RUN_LIVE=1 scripts/smoke.sh` against the operator's Seer instance, plus an opt-in integration test under `tests/integration/` gated by `skip_unless_run_integration` that hits the real `/api/v1/search?query=doctor` endpoint and asserts a `2xx` response with a non-empty `results` array. Never chained into `make ci` per AGENTS.md §7 step 6.
4. **Manual** — `seerr search doctor` (expect exit `0`, summary rows) and `seerr search doctor --human` (expect exit `0`, tabular view) on the operator's instance. Confirms the user-visible behaviour end-to-end.
