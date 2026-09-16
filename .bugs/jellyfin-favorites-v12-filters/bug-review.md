# Bug Report

## Bug Summary

`jellyfin favorites` exits with code `4` (`HttpError`) on Jellyfin
v12 servers. The handler hits
`GET /Users/{user_id}/Items/Favorites`, a sub-resource path that the
v12 release of Jellyfin removed; the v12 server answers
`HTTP 400` with body
`{"errors":{"itemId":["The value 'Favorites' is not valid."]}}`.
The remaining seven `jellyfin` commands work against v12, so the
breakage is isolated to `cmd_favorites`. The fix is to switch the
handler to the v12-compatible list endpoint
`GET /Users/{user_id}/Items?Filters=IsFavorite` — the same shape
that `cmd_recent` already uses with `Filters=IsPlayed`.

## Bug Details

### Expected Behavior

`jellyfin favorites`, run with no flags and a working `arr.conf`
that sets `jellyfin.user_id`, hits the configured Jellyfin v12
server at `/Users/{user_id}/Items?Filters=IsFavorite`, returns
`200` with the list of favorite items on stdout (curated summary
by default, verbatim JSON with `--verbose`, tabular view with
`--human`), and exits `0`.

If `arr.conf` does not declare `jellyfin.user_id`, the command
exits `1` (`ConfigError`) with the structured stderr line that
the sibling handlers (`resume` / `recent` / `latest`) already
emit — `service=jellyfin op=load status=... message=...: user_id
missing — set jellyfin.user_id in arr.conf`.

The `--limit` flag from the universal parent parser is forwarded
to the service as `Limit=<n>` so the page-size cap stays
consistent with `cmd_nextup` (which already does this).

### Actual Behavior

`jellyfin favorites`, run with no flags against a Jellyfin v12.1.0
instance with `jellyfin.user_id` configured, exits `4` with the
structured stderr line:

```text
service=jellyfin op=/Users/<user_id>/Items/Favorites status=400 message=jellyfin: HTTP 400 for /Users/<user_id>/Items/Favorites; body='{"errors":{"itemId":["The value \'Favorites\' is not valid."]}}'
```

`stdout` is empty. The transport layer maps the 400 to
`HttpError(exit_code=4)`; `main_wrapper` formats the stderr line
with the path that the handler asked for (`/Users/<uid>/Items/Favorites`)
and the body excerpt preserved verbatim so operators can grep the
original server response.

There is no command-local workaround inside the CLI. The operator
can fetch the same list with raw `curl` against the v12-compatible
path, but that defeats the purpose of the wrapper.

### Steps to Reproduce

1. Configure `arr.conf` with a valid `[jellyfin]` block:
   ```toml
   [jellyfin]
   url = "https://jellyfin.example"
   api_key = "***"
   user_id = "<configured>"
   ```
2. Run `jellyfin favorites` against a Jellyfin v12 instance.
3. Observe `exit 4`, the structured `status=400` line on stderr,
   and empty `stdout`.
4. Confirm via direct HTTP probes (MediaBrowser envelope is
   required; the bare `X-Emby-Token` header is ignored by v12):
   - `GET /Users/<uid>/Items/Favorites` → `400` with
     `itemId: The value 'Favorites' is not valid.`
   - `GET /Users/<uid>/Items?Filters=IsFavorite&Limit=20` → `200`
     with the favorite items in `Items` (the v12 envelope
     shape, identical to what `cmd_recent` consumes).
   - `GET /Items?Filters=IsFavorite` → `400` (filter is not
     accepted on the top-level `/Items` endpoint, so the user
     prefix is mandatory).
   - `POST /UserFavoriteItems/<itemId>?userId=<uid>` → `200`
     (this is the write toggle; the bug is about the read list).

## Impact Assessment

### Severity

- [ ] Critical - System unusable
- [x] High - Major functionality broken
- [ ] Medium - Feature impaired but workaround exists
- [ ] Low - Minor issue or cosmetic

The `favorites` command is one of the eight documented `jellyfin`
commands and is wholly non-functional on the current Jellyfin
v12 release. There is no in-CLI workaround — the operator has to
leave the CLI to recover the same data via raw `curl`. The
remaining seven commands keep working against v12, so the CLI
suite as a whole is still useful. That sits the bug on the
**High** / **Medium** boundary; it tips to **High** because the
affected command returns a non-zero exit code that breaks
cron-style automation that pipes `jellyfin favorites` into a
notifier or alert pipeline.

### Affected Features

- `jellyfin favorites` (REQ-6 AC8) — the affected command.
- The `--human` / `--verbose` rendering paths for `favorites` —
  unreachable in practice because the HTTP layer fails first on
  v12.
- The default summary renderer registered at
  `_SUMMARY_RENDERERS[("jellyfin", "favorites")]` in
  `arr_cli/facade/output.py` — unreachable for the same reason;
  the renderer itself does not need a change because its
  `_summary_jellyfin_favorites` function reads the same flat
  keys (`Name`, `Type`, `ProductionYear`, `SeriesName`) that the
  v12 `/Items?Filters=IsFavorite` envelope already provides.
- Any operator-side automation that pipes `jellyfin favorites`
  into a notifier or cron job; the pipeline sees `exit 4` and a
  structured `status=400` line on stderr rather than the curated
  summary it expects.

## Additional Context

### Error Messages

Live evidence from a Jellyfin v12.1.0 instance (MediaBrowser
envelope, the v12-required auth shape):

```text
$ jellyfin favorites
service=jellyfin op=/Users/<user_id>/Items/Favorites status=400 message=jellyfin: HTTP 400 for /Users/<user_id>/Items/Favorites; body='{"errors":{"itemId":["The value \'Favorites\' is not valid."]}}'
$ echo $?
4
```

The structured stderr line shape is produced by `main_wrapper`
from the `HttpError(service="jellyfin", op="/Users/<uid>/Items/Favorites", ...)`
that `transport.get` raises on the 400. The body excerpt is
truncated to the documented 500-char cap
(`_BODY_EXCERPT_LIMIT` in `arr_cli/facade/transport.py`).

## Analysis

### Investigation Summary

The investigation followed the upstream evidence trail first and
then traced back through the local code:

1. **Upstream API change.** The reporter's live evidence shows
   that v12 removed the `/Users/{userId}/Items/Favorites`
   sub-resource path. The replacement is
   `/Users/{userId}/Items?Filters=IsFavorite`, which is the same
   shape the v12 codebase already uses for other list queries
   (`cmd_recent` uses `Filters=IsPlayed`, `cmd_search` uses
   `searchTerm`, `cmd_nextup` uses `UserId`).
2. **Sibling pattern.** `cmd_recent` already follows the
   v12-compatible shape on the exact same `/Users/<uid>/Items`
   base path. The handler under investigation is a one-line
   variation of that pattern, swapping `SortBy=DatePlayed,
   Filters=IsPlayed, includeItemTypes=Movie,Episode` for
   `Filters=IsFavorite[&Limit=<n>]`.
3. **Local handler.** `arr_cli/jellyfin.py::cmd_favorites`
   (lines 351-368 of the current tree) is a five-line function
   that hardcodes the removed path. It reads `user_id` via
   `_require_user_id(cfg)` and percent-encodes the path segment
   via `transport.encode_path_segment(user_id)` — both of which
   can be reused unchanged.
4. **Local test fixture.** `tests/unit/test_jellyfin.py::TestCmdFavorites`
   exercises the old path via `_patched_get_payload` (a
   `unittest.mock.patch` of `transport.get`). The single positive
   test (`test_favorites_hits_user_path`) pins the removed path
   string; the two missing-user_id tests pin the config-error
   contract and are unaffected by the path change.
5. **Documentation.** `README.md` §4.1 row for `jellyfin favorites`
   documents the removed path; `CHANGELOG.md` does not yet
   mention this fix.

The investigation did not need to reach for the upstream OpenAPI
spec because the reporter's live evidence is authoritative —
`/Users/<uid>/Items?Filters=IsFavorite` returns 200 against
v12.1.0 with the documented payload shape, and the same shape is
already consumed by `cmd_recent`. The OpenAPI spec does include
the path; the earlier search miss was caused by the `{userId}`
template variable, not by the path being absent.

### Root Cause

`cmd_favorites` was implemented against the pre-v12 Jellyfin
contract that exposed `/Users/{user_id}/Items/Favorites` as a
dedicated sub-resource. Jellyfin v12 removed that sub-resource
and consolidated the favorites list into the general
`/Users/{user_id}/Items` query with the `Filters=IsFavorite`
parameter (the same v12 pattern that `cmd_recent` uses for
`Filters=IsPlayed`). The handler still requests the removed
sub-resource, so v12 answers 400 with the
`itemId: The value 'Favorites' is not valid.` body. The bug is
a single hardcoded path in `arr_cli/jellyfin.py`; the facade,
config layer, auth envelope, summary renderer, and parser are
all unaffected.

## Technical Details

### Affected Code Locations

- **File**: `arr_cli/jellyfin.py`
  - **Function/Method**: `cmd_favorites`
  - **Lines**: 351-368
  - **Issue**: Hardcodes the removed v12 path
    `/Users/<user_id>/Items/Favorites`. The handler also drops
    the universal `--limit` value on the floor, even though
    `cmd_nextup` already demonstrates the defensive `Limit`
    forwarding pattern.

- **File**: `tests/unit/test_jellyfin.py`
  - **Function/Method**: `TestCmdFavorites`
  - **Lines**: 638-667
  - **Issue**: `test_favorites_hits_user_path` pins the removed
    path string `/Users/jf-user-1/Items/Favorites`. The two
    missing-user_id tests are unaffected by the path change but
    should be kept so the `ConfigError` contract stays pinned.

- **File**: `README.md`
  - **Section**: §4.1, row for `jellyfin favorites`
  - **Issue**: Documents the removed path. Needs to be updated
    to `GET /Users/{user_id}/Items?Filters=IsFavorite` with a
    note that the optional `Limit` query parameter is forwarded
    from `--limit`.

- **File**: `CHANGELOG.md`
  - **Section**: Unreleased
  - **Issue**: The fix is not yet recorded. Should follow the
    format of the existing `recent` entry that pinned the
    `includeItemTypes` v12 change.

### Data Flow Analysis

1. Operator runs `jellyfin favorites` from the shell.
2. `main()` parses the args via `build_jellyfin_parser()`; the
   `favorites` subparser uses `universal_parents()`, so
   `--limit` is already present on the namespace.
3. `main_wrapper()` loads the config with `load_config`, raises
   `ConfigError(exit_code=1)` if `jellyfin.user_id` is missing
   (path terminates here).
4. `_dispatch()` looks up `cmd_favorites` in the `_DISPATCH`
   table and invokes it.
5. `cmd_favorites` calls `_require_user_id(cfg)` to obtain
   `user_id`, percent-encodes it via
   `transport.encode_path_segment(user_id)`, and calls
   `_get(f"/Users/{user_id}/Items/Favorites", args, cfg,
   op="favorites")`.
6. `_get` forwards to `transport.get("jellyfin", path, ...)`,
   which builds the URL, injects the `Authorization: MediaBrowser
   ***` envelope, and issues the `GET`.
7. The v12 server answers 400 with
   `{"errors":{"itemId":["The value 'Favorites' is not valid."]}}`.
8. `transport.get` raises `HttpError(service="jellyfin",
   op="/Users/<user_id>/Items/Favorites", message="...", status=400)`.
9. `main_wrapper` catches the `HttpError`, formats the structured
   `service=jellyfin op=... status=400 message=...` stderr line,
   and exits `4`.

The failure point is step 6 — the URL is wrong because step 5
hardcodes the removed sub-resource. Steps 1-4 and 7-9 work
correctly; the upstream contract change is the only thing that
broke the chain.

### Dependencies

- **Jellyfin v12** upstream — the only dependency whose contract
  changed. Jellyfin 10.11 still accepts the old path; v12 does
  not. The reporter's evidence is on v12.1.0.
- `arr_cli/facade/transport.py` — unaffected. The transport
  layer already supports arbitrary query parameters via
  `params=...`, percent-encodes both keys and values via
  `_encode_params`, and maps 4xx/5xx to `HttpError`. No new
  transport code is needed.
- `arr_cli/facade/output.py` — unaffected. The
  `_SUMMARY_RENDERERS[("jellyfin", "favorites")]` renderer reads
  the same flat keys (`Name`, `Type`, `ProductionYear`,
  `SeriesName`) that the v12 `/Items?Filters=IsFavorite`
  envelope already provides.
- `tests/unit/test_jellyfin.py` — affected. The single positive
  test pins the removed path; needs updating to the new shape.

## Solution Approach

### Fix Strategy

Replace the hardcoded `/Users/<user_id>/Items/Favorites` path in
`cmd_favorites` with the v12-compatible
`/Users/<user_id>/Items` path and the `Filters=IsFavorite` query
parameter. Reuse the existing facade helpers
(`_require_user_id`, `transport.encode_path_segment`, `_get`,
`_emit`) and the existing sibling pattern from `cmd_recent` so
the fix is a five-line swap, not a new code path. Optionally
forward the universal `--limit` value as `Limit=<n>` using the
same defensive `getattr` + `try/except (TypeError, ValueError)`
pattern that `cmd_nextup` already uses for the same forwarding
decision — silently drop a bad value rather than failing the
whole command.

The fix is intentionally minimal: no new transport code, no new
auth handling, no new renderer, no new CLI flag, no new runtime
dependency. The per-service CLI stays thin; the facade stays the
single source of HTTP/auth/encoding truth.

### Other Approaches Considered

- **POST + filter client-side.** Use `POST /UserFavoriteItems/<id>`
  for each id we discover. Rejected: there is no list endpoint
  behind that POST (it is the write toggle), so this would
  require a pre-existing list of ids from elsewhere.
- **Switch to `/Items?Filters=IsFavorite` (top-level).** Rejected
  by the reporter's evidence — the top-level endpoint returns 400
  because the filter is only accepted on the user-scoped path.
- **Branch on detected Jellyfin version.** Rejected: the project
  does not have a version probe today, and adding one for a
  single endpoint is heavier than the one-line swap. v10.11 still
  accepts the v12 query shape against `/Users/<uid>/Items`, so
  the new request works on both major versions.
- **Add `includeItemTypes=Movie,Episode` to mirror `cmd_recent`.**
  Considered but rejected by the reporter's evidence: the
  `Filters=IsFavorite` probe without `includeItemTypes` returned
  the favorite item as expected. `cmd_recent` needed the
  `includeItemTypes` parameter because `IsPlayed` is recursive
  by nature and v12's GetItems does the recursive roll-up only
  when filters are paired with `includeItemTypes`. Favorites are
  already flat (the user explicitly marked each item), so the
  roll-up is not needed. The implementer should re-verify against
  the operator's v12 instance after the fix is in place and add
  `includeItemTypes` only if the rendered list is missing items
  the operator expects to see.

## Implementation Plan

### Changes Required

1. **Change 1**: Replace the `cmd_favorites` path and add the
   `Filters=IsFavorite` query parameter; optionally forward
   `--limit` to the service as `Limit=<n>` using the same
   defensive pattern that `cmd_nextup` uses.
   - File: `arr_cli/jellyfin.py`
   - Modification: Replace the body of `cmd_favorites` (lines
     351-368) with the v12-compatible shape:

     ```python
     def cmd_favorites(args: argparse.Namespace, cfg: ServiceConfig) -> int:
         """Jellyfin ``favorites`` -- items the user has marked as favorite (REQ-6 AC8).

         The v12 release removed the dedicated
         ``/Users/<user_id>/Items/Favorites`` sub-resource path; the
         v12-compatible replacement is
         ``/Users/<user_id>/Items?Filters=IsFavorite``, the same shape
         ``cmd_recent`` uses with ``Filters=IsPlayed``.
         """
         user_id = _require_user_id(cfg)
         params: dict[str, Any] = {"Filters": "IsFavorite"}
         limit = getattr(args, "limit", None)
         if limit is not None:
             try:
                 params["Limit"] = int(limit)
             except (TypeError, ValueError):
                 pass
         payload = _get(
             f"/Users/{transport.encode_path_segment(user_id)}/Items",
             args,
             cfg,
             params=params,
             op="favorites",
         )
         columns = ["Name", "Type", "ProductionYear", "SeriesName"]
         return _emit(payload, args, columns=columns)
     ```

     The `columns`, `_require_user_id`, `transport.encode_path_segment`,
     `_get`, and `_emit` calls are reused unchanged. No new HTTP
     code, no new transport code, no new auth code.

2. **Change 2**: Update the `TestCmdFavorites` positive test to
   pin the new path and query parameter.
   - File: `tests/unit/test_jellyfin.py`
   - Modification: Replace `test_favorites_hits_user_path` with
     two tests — one for the default case (`Filters=IsFavorite`
     only) and one for when `--limit` is passed (adds `Limit=<n>`).
     Use the `responses` library as the recent 400 regression test
     already does, so the mock exercises the actual query-string
     serialization:

     ```python
     class TestCmdFavorites(unittest.TestCase):
         """REQ-6 AC8: ``GET /Users/{user_id}/Items?Filters=IsFavorite``
         on Jellyfin v12+ (the v10 ``/Items/Favorites`` sub-resource was
         removed)."""

         def test_favorites_hits_items_path_with_filter(self) -> None:
             cfg = _service_config(user_id="jf-user-1")
             args = _namespace()
             with _patched_get_payload([]) as mock_get:
                 cmd_favorites(args, cfg)
             positional = mock_get.call_args.args
             kwargs = mock_get.call_args.kwargs
             self.assertEqual(positional[1], "/Users/jf-user-1/Items")
             self.assertEqual(kwargs["params"], {"Filters": "IsFavorite"})

         def test_favorites_forwards_limit(self) -> None:
             cfg = _service_config(user_id="jf-user-1")
             args = _namespace(limit=50)
             with _patched_get_payload([]) as mock_get:
                 cmd_favorites(args, cfg)
             kwargs = mock_get.call_args.kwargs
             self.assertEqual(
                 kwargs["params"],
                 {"Filters": "IsFavorite", "Limit": 50},
             )

         def test_favorites_silently_drops_bad_limit(self) -> None:
             # Defensive: a non-int --limit is dropped rather than
             # crashing the whole command (matches cmd_nextup).
             cfg = _service_config(user_id="jf-user-1")
             args = _namespace(limit="not-a-number")
             with _patched_get_payload([]) as mock_get:
                 cmd_favorites(args, cfg)
             kwargs = mock_get.call_args.kwargs
             self.assertEqual(kwargs["params"], {"Filters": "IsFavorite"})

         def test_favorites_missing_user_id_raises_config_error(self) -> None:
             cfg = _service_config(user_id=None)
             args = _namespace()
             with self.assertRaises(ConfigError) as ctx:
                 cmd_favorites(args, cfg)
             self.assertEqual(ctx.exception.exit_code, 1)
             self.assertIn("user_id", ctx.exception.message)

         def test_favorites_missing_service_section_raises_config_error(self) -> None:
             cfg = _service_config(include_jellyfin=False)
             args = _namespace()
             with self.assertRaises(ConfigError) as ctx:
                 cmd_favorites(args, cfg)
             self.assertEqual(ctx.exception.exit_code, 1)
     ```

     The two existing missing-user_id tests are kept verbatim
     because the `ConfigError` contract is unaffected by the path
     change. The reporter's preference for the `responses`
     library is acceptable, but `_patched_get_payload` (the
     `unittest.mock.patch` style used by every other handler test
     in the file) is sufficient and consistent with the rest of
     `TestCmdFavorites`. Whichever style the implementer picks,
     the assertions are identical.

3. **Change 3**: Update the `jellyfin favorites` row in the
   `README.md` §4.1 command table.
   - File: `README.md`
   - Modification: Replace
     `| `jellyfin favorites`      | GET  | `/Users/{user_id}/Items/Favorites`                          | Requires `jellyfin.user_id`. |`
     with the new shape, mirroring the `jellyfin recent` row
     format. Include the `Limit` note from `jellyfin nextup` so
     operators know the flag is forwarded.

4. **Change 4**: Record the fix in `CHANGELOG.md` under the
   unreleased section.
   - File: `CHANGELOG.md`
   - Modification: Add a bullet mirroring the existing
     `jellyfin recent` entry that pinned the v12
     `includeItemTypes` change. Reference the removed
     `/Users/{user_id>/Items/Favorites` sub-resource and the new
     `Filters=IsFavorite` query so operators who skim the
     changelog can grep for it. Keep the wording neutral; do
     not reference the operator's internal environment or any
     private detail.

### Testing Strategy

The unit test changes in Change 2 are the primary verification:
they pin the new path string, the new query parameter, the
`Limit` forwarding, the silent-drop behaviour for a bad limit,
and the unchanged `ConfigError` contract for a missing
`user_id`. All assertions run against `unittest.mock.patch` (or
`responses`, per implementer preference), so the suite stays
hermetic — no live HTTP, no `pytest --run-integration`.

Run the full local CI chain before opening the PR per the
project convention:

```text
make ci
```

which expands to `make test && make secret-scan && make
smoke-dry`. The test target picks up the updated
`TestCmdFavorites`; the secret-scan target is unaffected
because no new credentials or example-config changes are
introduced; the smoke-dry target exercises the CLI grammar
without touching a live service.

If the operator's v12 instance is reachable from the
implementer's workstation, also exercise the change manually:

```text
jellyfin favorites
jellyfin favorites --limit 5
jellyfin favorites --human
```

Expected output: a JSON list (curated summary by default) of
the user's favorite items on stdout, `exit 0`, no stderr.
Confirm against the operator's evidence table — the response
shape should match the `cmd_recent` v12 envelope so the
summary renderer continues to work without a change.
