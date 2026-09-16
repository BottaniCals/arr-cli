# Bug Report

## Bug Summary

`jellyfin nextup` exits with code `4` (`HttpError`) on Jellyfin v12 servers
when the command is run without `--user-id`, even when `arr.conf` has a
configured `jellyfin.user_id`. v12 of Jellyfin requires the `UserId`
query parameter on `GET /Shows/NextUp`; without it the server answers
`HTTP 400` with body `"Error processing request."`. The other three
user-scoped Jellyfin commands (`recent`, `latest`, `favorites`) already
read `user_id` from config via `_require_user_id(cfg)`; `cmd_nextup` is
the only handler that ignores the config and only sends `UserId` when
the operator passes `--user-id` on the command line. The bug is a
divergence between `cmd_nextup` and its three sibling handlers.

## Bug Details

### Expected Behavior

`jellyfin nextup`, run with no flags and a working `arr.conf` that
sets `jellyfin.user_id`, hits `GET /Shows/NextUp?UserId=<configured>`
on the configured server, returns `200` with a list of next-up
episodes on stdout, and exits `0`. The default summary renderer
emits the curated per-command summary; `--verbose` emits the verbatim
service JSON; `--human` renders the documented columns (`Name`,
`SeriesName`, `ParentIndexNumber`, `IndexNumber`, `PremiereDate`).

If `arr.conf` does not declare `jellyfin.user_id`, the command exits
`1` (`ConfigError`) with the structured stderr line
`service=jellyfin op=load status=... message=...: user_id missing —
set jellyfin.user_id in arr.conf` (matching the contract pinned for
`favorites` / `resume` / `recent` / `latest`).

### Actual Behavior

`jellyfin nextup`, run with no flags against a Jellyfin v12 instance
with `jellyfin.user_id` configured, exits `4` with stderr:

```text
service=jellyfin op=nextup status=400 message=jellyfin: HTTP 400 for /Shows/NextUp; body="Error processing request."
```

No payload is emitted on stdout. The handler never consulted
`cfg.jellyfin.user_id`; the request URL had no `UserId` parameter, and
v12 rejected the request before consulting any other filter.

The same command, run with `jellyfin nextup --user-id <configured>`,
returns `200` with the expected list, confirming the bug is the
default-only behaviour, not a path or auth regression.

### Steps to Reproduce

1. Configure `arr.conf` with a valid `[jellyfin]` block:
   ```toml
   [jellyfin]
   url = "https://jellyfin.example"
   api_key = "***"
   user_id = "<configured>"
   ```
2. Run `jellyfin nextup` against a Jellyfin v12 instance (the operator
   reported failure is on v12; 10.11 historically tolerated the
   missing `UserId` and is not a useful reproduction target).
3. Observe `exit 4` and the structured `status=400 ... body="Error
   processing request."` line on stderr; stdout is empty.
4. As a control, run `jellyfin nextup --user-id <configured>` and
   confirm `exit 0` with a non-empty payload on stdout.
5. As a cross-check, run the equivalent curl against the server:
   ```text
   GET /Shows/NextUp           -> 400 "Error processing request."
   GET /Shows/NextUp?UserId=X  -> 200  <next-up items>
   ```

## Impact Assessment

### Severity

- [ ] Critical - System unusable
- [x] High - Major functionality broken
- [ ] Medium - Feature impaired but workaround exists
- [ ] Low - Minor issue or cosmetic

The `nextup` command is one of eight documented `jellyfin` commands
and is fully non-functional on Jellyfin v12 without the unintuitive
`--user-id` workaround (the operator must re-type the same value the
config already supplies). The other seven commands continue to work
on v12, so the CLI suite as a whole is still useful; the bug is
isolated to `nextup` and the workaround is one flag. That is the
boundary between **High** and **Medium**: a clean, documented
workaround exists, but the workaround exposes a config-vs-CLI
discrepancy that an operator cannot diagnose from the help text.

### Affected Features

- `jellyfin nextup` (REQ-6 AC4) — the affected command.
- The `--human` / `--verbose` rendering paths for `nextup` — never
  reached in practice because the HTTP layer fails first on v12.
- Any operator-side automation that pipes `jellyfin nextup` into a
  notifier or cron job; the pipeline sees a non-zero exit and a
  structured `status=400` line on stderr rather than the curated
  summary it expects.
- `README.md §4.x` for the jellyfin command table: it currently
  documents `GET /Shows/NextUp` without calling out the `UserId`
  requirement on v12. (Optional follow-up; the bug fix should leave
  the README accurate to the new contract.)

## Additional Context

### Error Messages

Live evidence captured against a Jellyfin v12 instance (this CLI does
not currently ship a recording because `tests/unit/` is hermetic by
contract — see AGENTS.md §7.5):

```text
$ jellyfin nextup
# exit code: 4
# stderr:
service=jellyfin op=nextup status=400 message=jellyfin: HTTP 400 for /Shows/NextUp; body="Error processing request."

$ jellyfin nextup --user-id <configured>
# exit code: 0
# stdout: <7-item next-up list>

$ curl -sS -H "Authorization: MediaBrowser Token=***" \
       'https://<host>/Shows/NextUp'
Error processing request.

$ curl -sS -H "Authorization: MediaBrowser Token=***" \
       'https://<host>/Shows/NextUp?UserId=<configured>'
<7-item JSON list>
```

The HTTP `400` from `/Shows/NextUp` (no `UserId`) is the upstream's
way of saying the parameter is required on v12; it is not a transient
error and not specific to the operator's instance (reproduced against
the documented v12 release-notes behaviour for the endpoint).

## Analysis

### Investigation Summary

- Read `arr_cli/jellyfin.py` end-to-end and confirmed the asymmetry
  between `cmd_nextup` and its three sibling user-scoped handlers
  (`cmd_recent` line 228, `cmd_latest` line 301, `cmd_favorites`
  line 356). All three call `_require_user_id(cfg)` (defined at
  line 74) before any transport call; `cmd_nextup` (line 255) does
  not.
- Read `tests/unit/test_jellyfin.py::TestCmdNextUp` to understand the
  pinned override semantics. The class covers the four param-matrix
  cases (`--limit`, `--start-index`, `--user-id`, combined) and the
  parser-level flag registration. The override test
  `test_nextup_forwards_user_id_override` (line 465) pins the
  current behaviour: when `--user-id` is supplied the override wins;
  when it is not, the param dict contains no `UserId` key.
- Cross-checked the argparse registration in `build_jellyfin_parser`
  for the `nextup` subparser: the only command-local flags are
  `--start-index` and `--user-id`; the three sibling subparsers
  register neither (their `user_id` comes entirely from config).
- Cross-checked AGENTS.md §1 (project overview — Jellyfin endpoint
  surface), §4.2 (per-service CLI shape — read-only `GET`, percent-
  encoding via the facade), §4.3 (facade ownership of auth and
  config), and §7 (command checklist — hermetic `tests/unit/` via
  `responses`).
- Confirmed that the live v12 endpoint requires `UserId` on
  `/Shows/NextUp` (per the upstream release notes cited in the bug
  report). The 10.11 behaviour of tolerating the missing parameter
  was a server-side default that v12 removed; this is the upstream
  contract change that surfaces the latent handler asymmetry.

### Root Cause

`arr_cli/jellyfin.py::cmd_nextup` (line 255) builds its `params` dict
from three sources — `args.limit`, `args.start_index`, and
`args.user_id` — and only populates `params["UserId"]` when the
operator passes `--user-id`. It does **not** call
`_require_user_id(cfg)` (line 74) the way `cmd_recent`, `cmd_latest`,
and `cmd_favorites` do. As a result, when the operator relies on the
configured `jellyfin.user_id` (the default and documented usage), the
request URL is `GET /Shows/NextUp` with no `UserId` parameter; on
Jellyfin v12 the server returns `HTTP 400 "Error processing request."`
and the transport layer maps that to `HttpError(exit_code=4)`.

The three sibling handlers do not have this bug because they read
`user_id` from config unconditionally and forward it as a path
segment (`/Users/{user_id}/Items/...`), where the absence of
`user_id` is a `ConfigError` rather than a silent server-side
`400`. `cmd_nextup` is structurally different (the endpoint does
not embed `user_id` in the path; it takes `UserId` as a query
parameter), so the missing-config path was never wired up.

### Contributing Factors

1. `cmd_nextup` is the only user-scoped Jellyfin handler that does
   not embed `user_id` in the URL path. That makes it the only
   handler where a config read can be silently skipped without
   breaking the URL shape; the divergence went unnoticed.
2. The override flag was registered alongside `--start-index` to
   give operators a multi-user escape hatch. The flag was intended
   as additive (override when needed; default still works) but the
   default path was never wired up, so the flag became the only
   way to use the command.
3. v10.11 of Jellyfin silently tolerated a missing `UserId` on
   `/Shows/NextUp`, so the latent bug was invisible against older
   upstreams. The v12 contract change made the bug surface.
4. The existing `TestCmdNextUp` class exercises the four param
   combinations but never asserts the default case (`args.user_id
   is None`) sends `UserId` from config; the current default-case
   test only asserts `params == {"Limit": 20}`, which pins the
   broken behaviour as the contract.

## Technical Details

### Affected Code Locations

- **File**: `arr_cli/jellyfin.py`
  - **Function/Method**: `cmd_nextup()`
  - **Lines**: 255-289 (the param-building block before the
    `_get("/Shows/NextUp", ...)` call)
  - **Issue**: the handler builds `params` only from `args.limit`,
    `args.start_index`, and `args.user_id`. It never calls
    `_require_user_id(cfg)`, so the configured `jellyfin.user_id`
    is never sent. The line
    `user_id_override = getattr(args, "user_id", None)` followed by
    `if user_id_override: params["UserId"] = user_id_override`
    pins the override-only contract.

- **File**: `arr_cli/jellyfin.py`
  - **Function/Method**: `build_jellyfin_parser()` — the
    `nextup = subparsers.add_parser(...)` block.
  - **Lines**: roughly the `nextup.add_argument("--user-id", ...)`
    registration (the sibling subparsers do not register this flag).
  - **Issue**: the override flag exists on `nextup` only. After the
    fix (Option B below) the registration is removed and the
    subparser matches the three siblings.

- **File**: `arr_cli/jellyfin.py`
  - **Function/Method**: `_require_user_id(cfg)` (helper, line 74).
  - **Lines**: 74-95
  - **Issue**: no change required. The fix is to call this helper
    from `cmd_nextup`, exactly the way the three siblings do.

- **File**: `tests/unit/test_jellyfin.py`
  - **Class/Method**: `TestCmdNextUp` (multiple methods covering
    the param matrix; the override test is `test_nextup_forwards_user_id_override`
    at line 465; the parser-level registration test is
    `test_nextup_parses_with_flags`).
  - **Issue**: the override test pins the broken contract (it
    asserts `params == {"Limit": 20, "UserId": "alt-user-2"}`,
    which can no longer happen once the override flag is dropped);
    the default-case test (`test_nextup_hits_shows_nextup`) asserts
    `params == {"Limit": 20}` — i.e. no `UserId` at all — which
    pins the bug as the contract.

### Data Flow Analysis

Current (broken) flow for `jellyfin nextup` with `jellyfin.user_id`
configured:

```
jellyfin nextup
  -> cmd_nextup(args, cfg)
       params = {}
       if args.limit:        params["Limit"]      = int(args.limit)
       if args.start_index:  params["StartIndex"] = int(args.start_index)
       if args.user_id:      params["UserId"]     = args.user_id
                             # <-- no _require_user_id(cfg) call;
                             #     args.user_id defaults to None
                             #     when --user-id is omitted
       payload = _get("/Shows/NextUp", args, cfg, params=params)
                    -> transport.get("jellyfin", "/Shows/NextUp", ...)
                         -> requests GET <base>/Shows/NextUp
                         -> response.status_code == 400
                              body="Error processing request."
                         -> raises HttpError(status=400)  # transport.py
       <- HttpError(status=400) bubbles out of cmd_nextup
  -> main_wrapper sees HttpError(exit_code=4)
  -> emits `service=jellyfin op=nextup status=400 ...` on stderr
  -> sys.exit(4)
```

After the fix (Option B — `cmd_nextup` reads `user_id` from config
and drops the override flag):

```
jellyfin nextup
  -> cmd_nextup(args, cfg)
       user_id = _require_user_id(cfg)
            # raises ConfigError(exit_code=1) if not configured
       params = {}
       if args.limit:        params["Limit"]      = int(args.limit)
       if args.start_index:  params["StartIndex"] = int(args.start_index)
       params["UserId"] = user_id
       payload = _get("/Shows/NextUp", args, cfg, params=params)
                    -> transport.get("jellyfin", "/Shows/NextUp",
                                     params={"UserId": "...", "Limit": 20})
                         -> requests GET <base>/Shows/NextUp?UserId=...&Limit=20
                         -> response.status_code == 200
                         -> <items list>
  -> _emit(payload, args, columns=[...])
  -> sys.exit(0)
```

If `jellyfin.user_id` is not configured:

```
jellyfin nextup
  -> cmd_nextup(args, cfg)
       user_id = _require_user_id(cfg)
            # raises ConfigError(exit_code=1,
            #   "jellyfin: user_id missing — set jellyfin.user_id in arr.conf")
  -> main_wrapper sees ConfigError(exit_code=1)
  -> emits `service=jellyfin op=load status=... message=...: user_id missing ...`
  -> sys.exit(1)
```

### Dependencies

- `arr_cli.facade.transport.get` — owns the HTTP call, status-code to
  typed-error mapping, and percent-encoding. No change required; the
  fix is in the per-service handler, which is the correct home per
  AGENTS.md §4.3.
- `arr_cli.facade.transport.encode_path_segment` — not used by
  `cmd_nextup` (the path is `/Shows/NextUp`, no user-scoped
  segment); no change.
- `arr_cli.facade.errors.ConfigError` — exit code `1`, raised by
  `_require_user_id(cfg)` when `user_id` is missing. The fix surfaces
  this error path for `cmd_nextup`, mirroring the three siblings.
- `arr_cli.facade.errors.HttpError` — exit code `4`, raised by
  `transport.get` for non-2xx responses. Preserved unchanged; the
  fix removes the trigger for the current `400` on v12 (because
  `UserId` is always sent) but leaves the error model intact for any
  future v12 4xx.
- `arr_cli.facade.output.emit` — renderer chain (`--human` /
  `--verbose` / default summary). Unaffected; the renderer sees a
  successful payload exactly as before.
- `responses` (tests-only) — `TestCmdNextUp` already uses it
  indirectly via `_patched_get_payload` (which mocks
  `arr_cli.jellyfin.transport.get`); the rewritten tests continue to
  use the same hermetic pattern per AGENTS.md §7.5.

## Solution Approach

### Fix Strategy

Adopt **Option B**: drop the `--user-id` override flag from the
`nextup` subparser and make `cmd_nextup` read `user_id` from config
via `_require_user_id(cfg)`, matching the three sibling handlers
exactly. Concretely:

1. At the top of `cmd_nextup` (after the existing `params: dict` =
   `{}` line), insert `user_id = _require_user_id(cfg)` — exactly
   the same call the three siblings make.
2. Replace the existing `if user_id_override: params["UserId"] =
   user_id_override` block with `params["UserId"] = user_id` (no
   override check; the override flag is gone).
3. Remove the `nextup.add_argument("--user-id", ...)` registration
   from `build_jellyfin_parser`. The `nextup` subparser will then
   register only `--start-index` as its command-local flag (which
   the three siblings also lack, but it is documented on `nextup`
   for pagination).
4. Update `cmd_nextup`'s docstring to describe the v12 contract:
   `UserId` is always sent from config; `Limit` and `StartIndex`
   remain optional command-local overrides.
5. Rewrite the affected unit tests (see Testing Strategy below).
6. Update `README.md §4.x` for the jellyfin command table to call
   out `GET /Shows/NextUp?UserId=<configured>&Limit=<n>` as the
   documented path on v12.

The fix is the smallest change in spirit that aligns `cmd_nextup`
with the three siblings. The error model is preserved: missing
config still raises `ConfigError(exit_code=1)` with the documented
stderr shape; non-2xx responses still raise `HttpError(exit_code=4)`
with the documented stderr shape. The single observable contract
change is that `jellyfin nextup --user-id alt` no longer parses —
an operator who genuinely needs to target a different user for one
invocation can do so with `--config /path/to/alt.conf` (the
universal per-invocation config override documented in AGENTS.md §5).

**Why Option B over Option A** (keep the override flag with
config default + CLI override wins):

- The three sibling commands (`recent`, `latest`, `favorites`) are
  all config-only. Keeping `nextup` as the lone exception makes
  the jellyfin CLI surface inconsistent and forces future maintainers
  to remember that `nextup` is special.
- The override flag was originally added as a multi-user escape
  hatch. With `_require_user_id(cfg)` reading from a single source
  of truth, the per-invocation config override (`--config`) covers
  the same use case without registering a second knob.
- Option A silently masks a future regression: if the override
  branch is ever restored and a future maintainer forgets to add
  the `else: params["UserId"] = _require_user_id(cfg)` arm, the
  bug returns. Option B removes the entire branch.
- The two options are within one line of each other in code size;
  Option B is not measurably bigger. The "minimal" axis here is
  consistency, not line count.

### Alternative Approaches Considered

- **Option A**: keep `--user-id` as an override flag, fall back to
  `_require_user_id(cfg)` when the flag is absent. Rejected —
  inconsistent with the three sibling commands; silently masks a
  future regression if the fallback arm is ever dropped.
- **Option C**: keep the override flag and send *both* `UserId`
  values (CLI override on the URL, config as a fallback param).
  Rejected — the endpoint accepts a single `UserId` parameter;
  passing both would produce a duplicate-key URL the transport
  layer has to dedupe and the behaviour is undefined.
- **Option D**: detect the upstream version and conditionally
  send `UserId`. Rejected — the facade does not own version
  detection, the version-aware logic would have to live in
  `cmd_nextup` and would be the only such site in the package.

## Implementation Plan

### Changes Required

1. **Change 1**: read `user_id` from config in `cmd_nextup` and
   forward it unconditionally as `params["UserId"]`.
   - File: `arr_cli/jellyfin.py`
   - Modification: insert `user_id = _require_user_id(cfg)` after
     the existing `params: dict[str, Any] = {}` line. Replace the
     block
     ```python
     user_id_override = getattr(args, "user_id", None)
     if user_id_override:
         params["UserId"] = user_id_override
     ```
     with
     ```python
     params["UserId"] = user_id
     ```
     The helper raises `ConfigError(exit_code=1)` with the
     documented `user_id missing — set jellyfin.user_id in arr.conf`
     message when the config does not declare one.

2. **Change 2**: drop the `--user-id` flag registration from the
   `nextup` subparser.
   - File: `arr_cli/jellyfin.py`
   - Modification: in `build_jellyfin_parser`, remove the
     `nextup.add_argument("--user-id", ...)` block (and only that
     block; `--start-index` stays). The `nextup` subparser then
     matches the three sibling subparsers structurally.

3. **Change 3**: update `cmd_nextup`'s docstring.
   - File: `arr_cli/jellyfin.py`
   - Modification: rewrite the docstring to describe the new
     contract. Drop the line "``--start-index`` and ``--user-id``
     are command-local flags" and replace with "``--start-index``
     is a command-local flag; ``UserId`` is read from
     `jellyfin.user_id` and is required on Jellyfin v12+". Keep
     the `Limit` / `StartIndex` / `UserId` parameter summary.

4. **Change 4**: rewrite the affected `TestCmdNextUp` tests.
   - File: `tests/unit/test_jellyfin.py`
   - Modification:
     - Update `test_nextup_hits_shows_nextup` (the default-case
       test) to assert the new contract: with the configured
       `user_id` and the default `args`, the params dict is
       `{"Limit": 20, "UserId": "jf-user-1"}` (the configured
       value, not a literal string).
     - Update `test_nextup_forwards_limit`,
       `test_nextup_forwards_start_index`, and
       `test_nextup_combined_params` to also assert the
       `UserId` key when `user_id` is configured. The new shape
       is `{"Limit": N, "StartIndex": M, "UserId": "jf-user-1"}`
       for the combined case.
     - Delete `test_nextup_forwards_user_id_override` — the
       override flag is gone, so the test no longer has a target.
     - Add a new test `test_nextup_uses_configured_user_id_by_default`
       that asserts the default case (no `--user-id` on the
       command line) sends `UserId=<cfg.jellyfin.user_id>`. This
       pins the bug fix as the contract.
     - Add a new test `test_nextup_missing_user_id_raises_config_error`
       that asserts the missing-config path raises `ConfigError`
       with `exit_code=1` and the documented `user_id missing`
       message. Mirrors the three siblings' "missing user_id"
       tests.
     - Update `test_nextup_parses_with_flags` in
       `TestBuildJellyfinParser` to drop the `--user-id alt`
       tokens and assert `args.user_id is None` (the namespace
       will no longer carry the attribute, so the helper
       `_namespace` should not set it). If the attribute is
       accessed via `getattr(args, "user_id", None)` (as the
       handler does), the test should assert the absence cleanly.

5. **Change 5**: re-run `make ci` locally (== `make test &&
   make secret-scan && make smoke-dry`) before considering the
   change done. AGENTS.md §3 makes this the canonical local CI
   chain.
   - File: (no source change)
   - Modification: N/A — verification step.

### Testing Strategy

- **Unit (hermetic, `responses`-backed)**: rewrite the affected
  `TestCmdNextUp` methods per Change 4 above. The default-case
  test must assert `UserId` comes from config (the bug fix as
  the new contract); the missing-config test must assert
  `ConfigError(exit_code=1)` with the documented message
  (mirroring the three siblings). The override test is deleted.
  The `--user-id alt` parser test in `TestBuildJellyfinParser`
  is updated to drop the flag and assert `args.user_id is None`.
  All tests remain hermetic via `_patched_get_payload` /
  `responses` (AGENTS.md §7.5).
- **Focused regression**: run
  `pytest tests/unit/test_jellyfin.py::TestCmdNextUp -v` to
  exercise the rewritten test class in isolation. Also run
  `pytest tests/unit/test_jellyfin.py -v` to confirm the change
  does not regress the other six Jellyfin commands.
- **Manual smoke (opt-in)**: `make smoke-live` against the
  operator's v12 Jellyfin instance, gated by `RUN_LIVE=1` per
  AGENTS.md §3, to confirm the rewritten `jellyfin nextup`
  returns a `200` with a non-empty next-up list and exits `0`
  when `arr.conf` has `jellyfin.user_id` set. This is the only
  test that proves the fix against the actual divergent
  upstream; it stays opt-in.
- **CI gate**: `make ci` must pass (== `make test &&
  make secret-scan && make smoke-dry`).
