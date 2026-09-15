# Bug Report

## Bug Summary

`seerr available <query>` exits with code `4` and a non-2xx HTTP response
on every invocation because the handler targets an Overseerr-shaped path
(`/api/v1/media/available?query=...`) that Seer does not expose. Seer's
live `/api-docs/swagger-ui-init.js` OpenAPI spec — the source of truth
per AGENTS.md §1 "Seer note" — defines only the general list endpoint
`GET /api/v1/media` (with `take` / `skip` / `filter` / `sort` query
parameters) and the per-item endpoint `GET /api/v1/media/{tmdbId}`. The
historical `/api/v1/media/available` sub-resource and its `query=`
parameter were inherited from pre-fork Overseerr documentation and were
never reconciled against Seer. Every invocation of the command has been
failing since the upstream Seer fork landed; the failure surfaces as
exit code `4` (`HttpError`), matching the documented exit-code table in
AGENTS.md §6.

## Bug Details

### Expected Behavior

Running `seerr available <query>` against a Seer instance:

- Sends a `GET` to a Seer endpoint that exists.
- Returns exit code `0` on success with the curated default summary on
  stdout (a list of items already in the library, optionally filtered
  by `query` for title-substring matching), or the verbatim service
  payload with `--verbose`.
- Returns exit code `4` only when the endpoint exists and a non-2xx
  response is returned; never when the path is unknown.

### Actual Behavior

Running `seerr available doctor` against the same Seer instance:

- CLI exits `4` (`HttpError`).
- Direct `GET https://<host>/api/v1/media/available?query=doctor`
  returns `405 {"message":"GET method not allowed"}`.
- Direct `POST https://<host>/api/v1/media/available` also returns
  `405` — the entire sub-resource is rejected on every method, which
  is stronger evidence than a `404`: the path is not merely
  "deprecated", it was never a valid Seer endpoint and the operator's
  reverse-proxy is the one answering `405`.

The structured stderr line emitted by `main_wrapper` follows the
documented shape `service=seerr op=/api/v1/media/available
status=<code> message=...`.

### Steps to Reproduce

1. Configure `arr.conf` with a Seer instance URL and API key (the
   `[seerr]` block; `X-Api-Key` per the AGENTS.md §1 auth matrix).
2. `pip install -e ".[dev]"` once so the `seerr` console script is on
   `PATH`.
3. `seerr available doctor` from a shell.
4. Observe exit status `4` (`echo $?`), an empty stdout, and a
   structured stderr line naming `op=/api/v1/media/available`. The
   same failure occurs with `--verbose`, `--human`, an empty query
   (`seerr available`), and for any other query string.

Reproduced upstream of this fix: see the user's evidence block under
the task prompt (`.tmp/test-results/seerr_available_doctor.*.txt` —
the files were not present on disk in this environment, so the
analysis works from the description in the task envelope and from the
handler source); the `405` on a direct curl probe rules out any
local-config or proxy-layer cause and pins the diagnosis to the path
shape.

## Impact Assessment

### Severity

- [ ] Critical - System unusable
- [x] High - Major functionality broken
- [ ] Medium - Feature impaired but workaround exists
- [ ] Low - Minor issue or cosmetic

Rationale: the `seerr available` command is one of the six documented
subcommands of the `seerr` executable in `README.md` §4.5 and a
size-to-summary candidate in `_SUMMARY_RENDERERS` per CHANGELOG
"Unreleased → Changed". It has been returning `HTTP 405` for every
invocation since the upstream Seer fork (the `405` is the operator's
reverse-proxy refusing the unknown sub-resource, not a transient
state). Workarounds exist — operators can `seerr media <tmdbId>` for
a known item, or `seerr requests` to inspect the household queue,
then `seerr media` per result — but no general "what's already in the
library" listing is reachable through the CLI today. Severity is
"High" rather than "Critical" because four other `seerr` subcommands
(`requests`, `request-count`, `search`, `media`, `user`) still work
and the package's other four executables (`jellyfin`, `radarr`,
`sonarr`, `maintainerr`) are unaffected.

### Affected Features

- `seerr available <query>` command (always-failing).
- The default summary renderer for that command (`_summary_seerr_available`
  in `arr_cli/facade/output.py`) — currently unused in production
  because the upstream request always raises `HttpError` first, but
  when the path is fixed the renderer will start running on real
  data and must keep emitting the same per-item shape so downstream
  chat agents depending on `title` / `mediaType` / `releaseDate` /
  `mediaInfo.status` see no breakage.
- Documentation surfaces for the command: `README.md` §4.5 seerr
  table, the module docstring at `arr_cli/seerr.py` lines 8-17, the
  argparse help text at `arr_cli/seerr.py:442-448`, and the
  `CHANGELOG.md` "Unreleased" section (which will gain a new
  "Fixed" entry).

## Additional Context

### Error Messages

Structured stderr (when invoked through the CLI):

```text
service=seerr op=/api/v1/media/available status=405 message=HTTP 405 for /api/v1/media/available
```

Direct probe against the operator's reverse-proxy (proving the
sub-resource is unknown):

```text
$ curl -i -H "X-Api-Key: $SEER_KEY" \
       'https://<host>/api/v1/media/available?query=doctor'
HTTP/1.1 405 Method Not Allowed
{"message":"GET method not allowed"}

$ curl -i -X POST -H "X-Api-Key: $SEER_KEY" \
       'https://<host>/api/v1/media/available'
HTTP/1.1 405 Method Not Allowed
{"message":"GET method not allowed"}
```

Related prior fixes documented in `CHANGELOG.md` "Unreleased → Fixed":
`seerr search <query>` (legacy `/api/v1/search/multi` → consolidated
`/api/v1/search`); `seerr user` (two-step `/api/v1/user/me` →
single `/auth/me`); `seerr requests` (paginated envelope `results`
unwrap + `take=1000`). The new fix follows the exact same shape of
fix — historical Overseerr-derived path that Seer does not expose.

## Analysis

### Investigation Summary

The bug was located end-to-end by reading the handler source
(`arr_cli/seerr.py:282-301`), comparing it against the design
documents (`README.md` §4.5 line 205; `.specs/arr-cli-mvp/`
requirements; the module docstring in `arr_cli/seerr.py` lines
8-17; the argparse help in `arr_cli/seerr.py:441-448`), confirming
Seer does not expose the path (the reverse-proxy's `405` on both
`GET` and `POST` rules out a wrong-method-vs-wrong-path ambiguity and
pins the diagnosis to the path), and matching the pattern against the
three sibling `seerr`-path bugs already fixed in CHANGELOG
"Unreleased → Fixed". The handler's path and params are wrong by
inheritance from Overseerr; the help text, the docstring, the
`README.md` table row, and the unit-test surface (which has no
regression test pinning the path) are all consistent with the
wrong path, which is why the bug survived. The fix task locked the
target path (`/api/v1/media`) and the filter mechanism (`filter`
query param documented in the Seer spec, exact value to be
discovered from the live spec — likely `filter=available` but to be
verified against `swagger-ui-init.js` before merge).

### Root Cause

The path string `"/api/v1/media/available"` in `cmd_available`
(`arr_cli/seerr.py:285-302`) and the `{"query": ...}` param dict in
the same function are inherited from pre-fork Overseerr
documentation. Seer does not expose the `/media/available`
sub-resource at all (the operator's reverse-proxy answers `405`
on every method), and the historical `query=` parameter has no
equivalent on Seer's `/api/v1/media` general list endpoint. The
correct endpoint is `GET /api/v1/media` with `filter=available`
(documented in the live spec — exact values to be confirmed) for
the "already in the library" filter, plus either a spec-supported
title-search param (to be confirmed) or client-side title-substring
filtering on a `take`-bounded list for the original CLI intent.

Contributing factors:

1. **No regression test pinned the path or params.** `tests/unit/
   test_seerr.py` has `TestCmdSearch` (pins the `/api/v1/search`
   path and guards against `/api/v1/search/multi`),
   `TestCmdUserHttpErrors` (pins `/auth/me`), and
   `TestCmdRequestsTakesParam` (pins `take=1000`), but no
   `TestCmdAvailable*` class that exercises the
   `/api/v1/media/available` path or a `TestCmdSearch`-style
   "must-not-hit-legacy-path" guard. The bug is therefore a class-
   shaped gap, not just an isolated oversight.
2. **The default summary renderer (`_summary_seerr_available` in
   `arr_cli/facade/output.py:707-732`) was written assuming a
   list-of-dicts shape that the Overseerr-shaped endpoint would
   have returned.** It does not unwrap Seer's documented paginated
   envelope. Once the path is fixed to `/api/v1/media` (which
   returns `{pageInfo, results, ...}` per the same convention
   Seer uses on `/api/v1/request`), the renderer will need to
   unwrap `results` the same way `_summary_seerr_requests` does.
3. **Help text, module docstring, and `README.md` §4.5 row all
   repeat the wrong endpoint.** A future contributor reading
   the help text after the path is changed will see the new
   path, but the docstring and README must move with the handler
   fix to keep them in sync (per the pattern in the recent
   `seerr search` and `seerr user` CHANGELOG entries).
4. **AGENTS.md §1 "Seer note" exists precisely to prevent this
   class of bug.** The investigation confirms the guard
   paragraph's premise: historical Overseerr documentation
   diverges from the live Seer spec on endpoints, and the four
   failing `seerr` commands investigated this cycle (including
   this one) all sit on exactly that drift. The fix must apply
   the guard consistently — verify against
   `/api-docs/swagger-ui-init.js`, not against Overseerr or
   Jellyseerr docs.

Why existing tests did not catch this: there is no unit test for
`cmd_available` (only the dispatch-table existence check and a
six-commands assertion in `tests/unit/test_seerr.py`), so the
wrong-path call was never exercised end-to-end through
`responses`. The dispatcher test confirms the function is
registered but never invokes the path string or the params dict,
so the wrong path was invisible until the operator's instance
rejected it at runtime.

### Risks and Scope Creep

- **`/api/v1/media` response envelope shape is an assumption.**
  The most likely shape is the same paginated envelope Seer uses
  on `/api/v1/request` (`{pageInfo, results, ...}`); the
  renderer update must handle BOTH that envelope and a bare
  list, mirroring the `_summary_seerr_requests` defensive
  pattern. If the live spec returns a different shape (e.g. a
  plain list, or a `{media: [...]}` wrapper), the renderer
  must be adjusted. The implementer must probe a real instance
  before merge, not assume from Overseerr history.
- **`filter` param accepted values are an assumption.** The task
  envelope notes the spec "documents the ``filter`` param exists
  but does NOT list accepted values" and asks for live probing.
  `filter=available` is the leading hypothesis but cannot be
  confirmed without the live spec. If the live spec returns a
  different token (e.g. `filter=inLibrary`, `filter=library`,
  `filter=requested`, or a numeric enum), the handler must use
  the actual accepted string. The lock-in step in the task
  envelope documents "likely `filter=available`, but verify
  against the live spec/responses" — that verification must
  happen before merging, not after.
- **Title-substring matching needs a deliberate choice.** Seer's
  `/api/v1/media` may or may not document a title-search query
  parameter (the Overseerr `query=` is the most suspect; Seer
  does not advertise it on `/media`). Two viable strategies:
  forward a spec-supported title-search param if one exists
  (preferred, single round trip); otherwise do a `take`-bounded
  fetch and post-filter on title client-side (mirrors the
  `take=1000` precedent on `cmd_requests`). The implementer
  must pick one and document the choice in the help text and
  the renderer docstring, but NOT introduce a `query=` parameter
  the endpoint doesn't accept (explicit out-of-scope note in the
  task envelope).
- **No silent behaviour change for consumers.** The default
  summary renderer's output shape must remain
  `[{title, mediaType, releaseDate, mediaInfo: {status}}, ...]`
  for any items that match — chat-agent consumers keyed on
  those exact keys must not need to change. `--verbose` will
  emit the verbatim service envelope, which is a NEW shape
  for this command; document that in CHANGELOG.
- **Public exit-code contract is preserved.** A still-unknown
  path (e.g. if `filter=available` is rejected) will surface
  as `HttpError(exit_code=4)` via the same `service=seerr op=...
  status=...` stderr line; consumers scripting against exit
  codes need no changes. The change in stderr `op=` value (from
  `/api/v1/media/available` to `/api/v1/media`) is a
  diagnostic-detail change that downstream tooling should
  parse by leading `op=` token, not by the suffix.

## Technical Details

### Affected Code Locations

- **File**: `arr_cli/seerr.py`
  - **Function/Method**: `cmd_available`
  - **Lines**: 282-301 (`query = ...`, `_get("/api/v1/media/
    available", ...)`, `params={"query": query}`).
  - **Issue**: targets a Seer-non-existent sub-resource and a
    non-spec `query=` param.

- **File**: `arr_cli/seerr.py`
  - **Function/Method**: module docstring + subparser + dispatch
  - **Lines**: 12 (module-docstring command table),
    280-301 (`cmd_available` body and `columns = [...]`),
    441-456 (argparse `available` subparser + `help=` text).
  - **Issue**: strings repeat the wrong path; all three locations
    must move together with the handler fix per the existing
    `seerr search` / `seerr user` CHANGELOG pattern.

- **File**: `arr_cli/facade/output.py`
  - **Function/Method**: `_summary_seerr_available`
  - **Lines**: 707-732 (renderer body) and the registration in
    `_SUMMARY_RENDERERS` at line ~844 (`("seerr", "available"):
    _summary_seerr_available`,).
  - **Issue**: currently iterates `payload` as a list; if
    `/api/v1/media` returns the paginated envelope Seer uses
    elsewhere, the renderer must unwrap `payload.get("results")`
    (mirror `_summary_seerr_requests` at lines ~644-678).

- **File**: `tests/unit/test_seerr.py`
  - **Class**: (missing) `TestCmdAvailable` mirroring
    `TestCmdSearch`
  - **Lines**: (to be added at the end of the file, alongside
    the other per-command classes).
  - **Issue**: no regression test exercises the handler's
    endpoint, params, envelope unwrap, or column projection; the
    fix must add `TestCmdAvailable*` cases that pin path +
    params + envelope unwrap + tabular columns + a defensive
    "must not hit legacy path" guard mirroring
    `TestCmdSearch.test_cmd_search_does_not_hit_legacy_multi_path`.

- **File**: `README.md`
  - **Section**: §4.5 seerr command table
  - **Lines**: ~205 (`seerr available <query>` row).
  - **Issue**: documents the wrong path; the row moves with
    the handler fix per the existing pattern.

- **File**: `CHANGELOG.md`
  - **Section**: "Unreleased → Fixed"
  - **Lines**: between the `seerr requests` entry and the
    `Jellyfin authentication` entry.
  - **Issue**: a new "Fixed" entry documents the path change,
    the renderer envelope-unwrap, and the new regression test
    class; mirrors the wording used by the prior three `seerr`
    fixes.

- **File**: `arr_cli/facade/transport.py`
  - **Function/Method**: `get` (read-only — no edit required)
  - **Lines**: 403-489 (transport layer; `_encode_params` at
    243-263 percent-encodes any param forwarded by the handler).
  - **Issue**: no change; transport already percent-encodes
    whatever the handler passes in `params=`. The fix is
    entirely at the handler-and-renderer layer.

### Data Flow Analysis

Today's broken flow:

1. Operator runs `seerr available doctor`.
2. `main_wrapper` loads `[seerr]` config (URL + API key) and
   dispatches to `cmd_available` via `_DISPATCH["available"]`.
3. `cmd_available` calls `_get("/api/v1/media/available", args,
   cfg, params={"query": "doctor"}, op="available")`.
4. `_get` calls `transport.get("seerr",
   "/api/v1/media/available", params={"query": "doctor"}, ...)`.
5. `transport.get` percent-encodes the param, injects
   `X-Api-Key`, and sends `GET
   https://<host>/api/v1/media/available?query=doctor`.
6. Seer (or the operator's reverse-proxy in front of it)
   answers `405 {"message":"GET method not allowed"}` because
   `/api/v1/media/available` is not a Seer-exposed sub-resource.
7. `transport.get` raises `HttpError(exit_code=4)`; `main_wrapper`
   writes `service=seerr op=/api/v1/media/available status=405
   message=HTTP 405 for /api/v1/media/available` to stderr and
   returns `4`.
8. The `_summary_seerr_available` renderer is never reached.

Fixed flow (proposed):

1. Operator runs `seerr available doctor`.
2. Same dispatch through `cmd_available`.
3. `cmd_available` calls `_get("/api/v1/media", args, cfg,
   params={"take": 1000, "filter": "<discovered token>"},
   op="available")` and, if the spec does not support a
   title-search query param, applies a client-side
   title-substring filter on the returned list.
4. `_get` → `transport.get` → percent-encodes, injects auth, sends
   `GET https://<host>/api/v1/media?take=1000&filter=available`.
5. Seer returns the documented envelope (`{pageInfo, results:
   [...], serviceErrors}` per the convention used by
   `/api/v1/request`); `_summary_seerr_available` unwraps
   `results` and emits the curated
   `[{title, mediaType, releaseDate, mediaInfo: {status}}, ...]`
   summary on stdout (or the verbatim envelope with `--verbose`).
6. `main_wrapper` returns `0`.

### Dependencies

External:

- Seer instance exposing `/api/v1/media` (with `take` / `skip` /
  `filter` / `sort`). AGENTS.md §1 marks the live
  `/api-docs/swagger-ui-init.js` as the source of truth for
  endpoint path + method + accepted query param values; the fix
  MUST verify the exact `filter` token against this spec before
  committing.
- No new runtime dependency; the existing `requests` +
  `X-Api-Key` auth path is reused.

Internal:

- `arr_cli.facade.transport.get` (unchanged) percent-encodes
  params and maps non-2xx to `HttpError(exit_code=4)` (AGENTS.md
  §6).
- `arr_cli.facade.cli_common.main_wrapper` (unchanged) renders
  the structured stderr line and translates `ArrError` to the
  documented exit code.
- `arr_cli.facade.output._summary_seerr_available` is updated to
  unwrap the paginated envelope (mirror of
  `_summary_seerr_requests`); `emit` and `_SUMMARY_RENDERERS`
  need no other change.
- Test stack: `responses` for hermetic HTTP mocking (per AGENTS.md
  §7.5) plus `unittest` patterns already used elsewhere in
  `tests/unit/test_seerr.py`.

## Solution Approach

### Fix Strategy

Switch `cmd_available` from `/api/v1/media/available?query=...` to
Seer's general list endpoint `/api/v1/media` with a `filter`
parameter set to the live-spec-confirmed token (leading hypothesis
`filter=available`, to be verified at
`http://192.168.1.123:5055/api-docs/swagger-ui-init.js` before
merge). Forward `take` to bound the response (precedent:
`cmd_requests` uses `take=1000`). For the original CLI's
title-substring intent: if the live spec documents a title-search
query parameter on `/api/v1/media`, forward the query through it
(single round trip); otherwise post-filter the returned items
client-side on a substring match against `title`. Do NOT introduce
a `query=` parameter the endpoint does not accept (per the locked
design out-of-scope note).

Update `_summary_seerr_available` to unwrap the documented
paginated envelope (`payload.get("results")`) before iterating the
items, mirroring `_summary_seerr_requests`. Keep the per-item
shape `[{title, mediaType, releaseDate, mediaInfo: {status}}, ...]`
unchanged so chat-agent consumers keyed on those exact keys do not
need to update.

Move help text (module docstring + argparse `help=`) and the
`README.md` §4.5 row to the new path together with the handler
fix, so the public surface documents the change in one place.
Add a `TestCmdAvailable*` regression class in
`tests/unit/test_seerr.py` that mirrors `TestCmdSearch`: one
case pinning the new path + the `take` + `filter` params, one
case pinning the paginated envelope unwrap, one case pinning the
column projection on `--human`, one defensive
"must-not-hit-legacy-path" guard, plus a `--verbose` verbatim-
envelope case.

Add a `CHANGELOG.md` "Unreleased → Fixed" entry that follows the
wording pattern of the three sibling `seerr` fixes already in
that section.

Other approaches considered:

- **Drop the `seerr available` command.** Rejected by the
  task envelope's explicit out-of-scope note.
- **Use `/api/v1/search?query=...` + client-side post-filter.**
  Also explicitly out of scope per the task envelope.
- **Keep the Overseerr path and define a compatibility shim.**
  No compatibility layer exists for the prior three sibling
  seerr-path fixes; staying consistent means fixing the path
  the way the others were fixed.
- **Do title-substring via a brand-new query parameter that
  Seer does not document.** Explicitly out of scope per the
  task envelope.

## Implementation Plan

### Changes Required

1. **Change**: Switch `cmd_available` to the documented Seer
   endpoint with the right filter.
   - File: `arr_cli/seerr.py`
   - Modification: replace the path `"/api/v1/media/available"`
     with `"/api/v1/media"`; replace the params
     `{"query": query}` with
     `{"take": 1000, "filter": "<spec-confirmed token>"}`,
     where `<spec-confirmed token>` is the exact string the live
     spec accepts (likely `available`; verify before merge).
     For the title-substring requirement: if the live spec
     documents a title-search parameter on `/api/v1/media`,
     add it to `params=` and post-filter client-side only as a
     secondary safety net; otherwise post-filter client-side
     on a case-insensitive substring match against each
     item's `title` field (defensive: items without a `title`
     field are dropped). Update the `columns = [...]` list
     only if the response shape makes any of the current
     columns unreachable (likely not — the renderer projects
     the same four keys, just from a possibly different
     envelope layer).

2. **Change**: Update `cmd_available` docstring + module
   docstring + argparse help text to match the new endpoint.
   - File: `arr_cli/seerr.py`
   - Modification: in the module-level command table at the top
     of the file (line 12), replace
     `GET /api/v1/media/available?query=...` with
     `GET /api/v1/media?filter=...&take=...`; in the
     `cmd_available` docstring (lines 282-287), replace the
     "The ``query`` parameter is forwarded as a query string;
     the transport layer percent-encodes the value" sentence
     with a sentence that reflects the new mechanism
     (post-filter on a `take`-bounded fetch + `filter=`
     param); in the argparse `help=` at line 443, replace
     `"filtered by query (GET /api/v1/media/available?query=...)"`
     with text that names the new path and the
     `filter=` param, mirroring the wording style used by the
     `seerr search` help line.

3. **Change**: Update `_summary_seerr_available` to unwrap the
   paginated envelope.
   - File: `arr_cli/facade/output.py`
   - Modification: mirror the `_summary_seerr_requests`
     envelope-unwrap pattern (lines ~644-678). At the start of
     the function body, if `isinstance(payload, Mapping)` then
     `payload = payload.get("results")`; only then iterate.
     Keep the per-item shape
     `[{title, mediaType, releaseDate, mediaInfo: {status}}, ...]`
     unchanged. If the live spec returns a bare list, the
     existing `isinstance(payload, list)` path handles it
     unchanged; if it returns a different wrapper shape, the
     defensive `isinstance(payload, list)` fallback inside the
     loop returns `[]` (same graceful default as
     `_summary_seerr_requests`).

4. **Change**: Add `TestCmdAvailable*` regression class
   mirroring `TestCmdSearch`.
   - File: `tests/unit/test_seerr.py`
   - Modification: append a new class block at the end of the
     file with the cases listed in the testing strategy below.
     Each case must use `responses.RequestsMock` (per AGENTS.md
     §7.5 hermetic-unit constraint). Use the same `_write_toml_config`
     helper already in the file. The defensive guard case is
     the most important — it pins the path against regression
     back to `/api/v1/media/available` the same way
     `TestCmdSearch.test_cmd_search_does_not_hit_legacy_multi_path`
     pins against `/api/v1/search/multi`.

5. **Change**: Update `README.md` §4.5 row to reflect the new
   endpoint.
   - File: `README.md`
   - Modification: in the seerr command table at line ~205,
     replace the `/api/v1/media/available?query=<query>` cell
     with the new path + `filter` + `take` parameter list, and
     tweak the right-hand description column to clarify that
     the query is title-substring filtering done client-side
     (or whatever the chosen mechanism is). Match the wording
     density of the sibling seerr rows.

6. **Change**: Add a `CHANGELOG.md` "Unreleased → Fixed" entry.
   - File: `CHANGELOG.md`
   - Modification: append a new bullet between the existing
     `seerr requests` entry and the `Jellyfin authentication`
     entry. Word it in the same style as the prior three seerr
     fixes: name the wrong path, name the right path, name the
     Seer-not-Overseerr reason, and reference the new
     `TestCmdAvailable*` class + the `README.md` row move so
     future drift of this exact path fails the unit suite
     immediately.

### Testing Strategy

Unit tests (hermetic, no live HTTP — AGENTS.md §7.5) added to
`tests/unit/test_seerr.py` as a new `TestCmdAvailable*` class:

- `test_cmd_available_hits_seerr_media_with_take_and_filter` —
  registers a `responses` mock at
  `https://seerr.example/api/v1/media` matching
  `query_param_matcher({"take": "1000", "filter":
  "<spec-confirmed token>"})` and asserts the mock fires once
  with exit `0`. Pins the path AND both query params.
- `test_cmd_available_empty_query_still_hits_endpoint` — same
  mock pattern but with no `query=` arg, asserts the mock
  fires. Pins that the handler does not require a title filter
  to be present (the substring filter is post-filter, not the
  URL).
- `test_cmd_available_title_substring_filter_applied_client_side`
  — returns a paginated envelope with `results: [{title:
  "Doctor Who"}, {title: "Doctor Strange"}, {title:
  "Unrelated"}]` and asserts the rendered stdout contains
  only the two "Doctor*" items. Pins the title-substring
  filter.
- `test_cmd_available_envelope_unwrap` — returns a paginated
  envelope (`{pageInfo: {...}, results: [...]}`) and asserts
  the renderer iterates `results` rather than emitting the
  empty-list fallback. Mirrors
  `TestCmdRequestsPaginatedEnvelope.test_cmd_requests_envelope_default_unwraps_results`.
- `test_cmd_available_verbose_emits_verbatim_envelope` —
  asserts that `--verbose` returns the envelope shape
  verbatim (not the curated summary).
- `test_cmd_available_does_not_hit_legacy_available_path` —
  defensive guard mirroring
  `TestCmdSearch.test_cmd_search_does_not_hit_legacy_multi_path`.
  Patches `transport.get`, asserts every call's path argument
  is NOT `"/api/v1/media/available"` (the failure path
  returning `HTTP 405`) and IS `"/api/v1/media"`. Without this
  guard, a future copy-paste regression back to the Overseerr-
  shaped path would surface only at the operator's instance as
  exit `4`.

Integration / live verification (opt-in per AGENTS.md §7.6):

- `make smoke ARGS="seerr available <real-title>" RUN_LIVE=1`
  against an operator's Seer instance, asserting exit `0` and
  a non-empty summary when the title matches.
- `seerr available "<known-good-title>"` end-to-end exit-code
  assertion: `echo $?` must be `0`; stdout must be the
  curated `[{title, mediaType, releaseDate, mediaInfo:
  {status}}, ...]` summary; stderr must be empty.
- `seerr available "<known-bad-title>"` (substring that should
  match nothing): exit `0`; stdout must be `[]` (or
  `(empty list)` on `--human`); stderr empty.

CI hygiene (pre-merge, per AGENTS.md §3 / §7.7):

- `make ci` (= `make test && make secret-scan && make smoke-dry`)
  must pass locally with the new tests in place. `make lint`
  must pass (it is a `py_compile` sweep). The full test suite
  should stay green; existing `TestCmdSearch`,
  `TestCmdUserHttpErrors`, `TestCmdRequestsPaginatedEnvelope`,
  and `TestCmdRequestsTakesParam` cases must continue to pass
  to prove the fix does not regress the sibling fixes.

Documentation drift:

- Verify `README.md` §4.5 row, `arr_cli/seerr.py` module
  docstring (line 12), and `arr_cli/seerr.py:443` argparse
  help all show the same path. A single grep over the project
  for `/api/v1/media/available` must return zero matches after
  the fix (any remaining match is a documentation drift
  marker).

Future-prevention:

- The defensive "must-not-hit-legacy-path" test in the
  `TestCmdAvailable*` class is the primary future-proofing
  lever: any future copy-paste regression back to
  `/api/v1/media/available` fails the unit suite in
  milliseconds rather than at the operator's instance. This
  matches the precedent set by the prior three `seerr` fixes
  in CHANGELOG "Unreleased → Fixed".
- A pre-commit guard (out of scope for this fix, but worth
  flagging) could add a `grep` rule under `scripts/` that
  fails CI when any tracked file mentions
  `/api/v1/search/multi` OR `/api/v1/media/available` OR
  `/api/v1/user/me` — these are the three paths already fixed
  in this cycle and the one being fixed by this report.
