# Bug Report

## Bug Summary

`seerr user` exits with code `4` and a `service=seerr op=user` line on
stderr against the live Seer deployment, instead of exiting `0` with the
authenticated user object on stdout. The CLI's two-step probe in
`arr_cli/seerr.py` was designed to try `/api/v1/user/me` first and fall
back to `/auth/me` when the primary returns `404`. On Seer, the primary
returns `400` (the OpenAPI validator's way of saying the path doesn't
exist), so the `exc.status == 404` guard inside `_try_user_path` never
fires; the bare `raise` re-raises the `HttpError` and the CLI surfaces
exit code `4` for what is, in practice, a path-divergence that the
fallback would have resolved.

## Bug Details

### Expected Behavior

Running `seerr user` against a Seer instance with a valid `X-Api-Key`
returns the authenticated user object on stdout and exits `0`:

```text
$ seerr user
{"id": 1, "email": "...", "username": "...", ...}
$ echo $?
0
```

If the (single working) self-check endpoint is unreachable, the CLI
exits `4` with the documented structured stderr line, e.g.
`service=seerr op=user status=<code> message=...`.

### Actual Behavior

```text
$ seerr user
service=seerr op=/api/v1/user/me status=400 message=seerr: HTTP 400 for /api/v1/user/me; body='request/params/userId must be number'
$ echo $?
4
```

No payload is emitted on stdout; the operator sees a `400` even though
the credential is valid and `GET /auth/me` would have returned `200`
with the expected user object.

### Steps to Reproduce

1. Configure `arr.conf` with a valid `[seerr]` section pointing at a
   live Seer instance (URL + `api_key`).
2. From a shell on the operator's box, run `seerr user`.
3. Observe `service=seerr op=/api/v1/user/me status=400 ...` on stderr
   and exit code `4`.
4. As a control, `curl -H "X-Api-Key: ..." '<seerr-url>/auth/me'`
   returns `200` with the user JSON; `curl` against `/api/v1/user/me`
   returns `400` with `{"message":"request/params/userId must be number", ...}`.

## Impact Assessment

### Severity

- [ ] Critical - System unusable
- [x] High - Major functionality broken
- [ ] Medium - Feature impaired but workaround exists
- [ ] Low - Minor issue or cosmetic

### Affected Features

- `seerr user` — the auth self-check subcommand (REQ-10 AC6). This is
  the only documented way for the operator to confirm a `X-Api-Key`
  works against Seer from the CLI without a manual `curl` probe.
- Any operator-side automation that uses `seerr user` to validate
  credentials before issuing other `seerr` commands (e.g. a credential
  rotation script that asserts a non-zero exit means "auth is wrong"
  rather than "endpoint is wrong").

## Additional Context

### Error Messages

Reproduced stderr shape from a live Seer probe (this CLI does not
currently ship a recording because `tests/unit/` is hermetic by
contract — see AGENTS.md §4.2 and §7.5):

```text
service=seerr op=/api/v1/user/me status=400 message=seerr: HTTP 400 for /api/v1/user/me; body="request/params/userId must be number"
```

Live API evidence captured against the operator's Seer instance
(`/api-docs/swagger-ui-init.js` is the source of truth per AGENTS.md
§1 "Seer note"):

```text
GET /api/v1/user/me        -> 400  {"message":"request/params/userId must be number", ...}
GET /api/v1/user/me?userId=1 -> 400 {"message":"Unknown query parameter 'userId'"}
GET /auth/me               -> 200  {<authenticated user object>}
```

The `400` from `/api/v1/user/me` is the OpenAPI validator's response
when the requested path is not declared in the spec — i.e. the
endpoint is not present on this server. Adding `?userId=1` to mimic
the validator-expected parameter produces a different `400`
("Unknown query parameter 'userId'") because the validator still
rejects the path before looking at the query.

## Analysis

### Investigation Summary

- Read `arr_cli/seerr.py` end-to-end and identified the two-step probe
  inside `cmd_user` (REQ-10 AC6) plus its helper `_try_user_path`.
- Read `arr_cli/facade/transport.py` (`HttpError` construction) and
  `arr_cli/facade/errors.py` (exit-code map: `HttpError` → exit `4`).
- Read `tests/unit/test_seerr.py::TestCmdUserHttpErrors` and confirmed
  the existing tests pin (a) the 400-bubbles-up contract and (b) the
  both-404-synthesized-error contract, but there is no
  404-then-fallback-success test and no test that the primary is dead
  on the current upstream.
- Cross-checked the module docstring of `arr_cli/seerr.py` and the
  `cmd_user` docstring; both describe the two-step probe as live
  behaviour, so any change to a single probe must update both.
- Confirmed AGENTS.md §1 "Seer note" explicitly names the live
  `/api-docs/swagger-ui-init.js` OpenAPI spec as the source of truth
  and warns that "endpoints frequently differ between Seer and its
  predecessors" — exactly the drift causing this bug.
- The .tmp/test-results evidence directory referenced by the bug
  description does not exist in this checkout (`tests/unit/` is
  hermetic by contract); the API evidence captured in the bug
  description is therefore the primary repro. The structured stderr
  shape is reproduced from the live behaviour of `transport.get`'s
  `HttpError` construction path, which is unit-tested in
  `tests/unit/test_transport.py` and `test_errors.py`.

### Root Cause

`arr_cli/seerr.py::_try_user_path` re-raises any `HttpError` whose
status is not `404`, and `cmd_user` calls the primary
`/api/v1/user/me` first. On Seer, `/api/v1/user/me` does not exist
(the path is absent from the live OpenAPI spec); the OpenAPI
validator answers with `400` rather than `404`. Because the fallback
trigger is strictly `exc.status == 404`, the `400` bubbles up to
`cmd_user` and out to `main_wrapper` as `HttpError(exit_code=4)`,
producing the observed `service=seerr op=user status=400` line and
exit code `4`. The `/auth/me` fallback path that would have resolved
the divergence is never attempted.

The probe design predates the current Seer API surface. The original
intent (design.md "Pre-locking Verifications -- Seerr /api/v1/user/me")
was to bridge Overseerr's `/auth/me` and Seer's then-documented
`/api/v1/user/me`; Seer subsequently dropped `/api/v1/user/me` in
favour of `/auth/me` only, so the primary leg is now dead weight and
the `404`-only fallback trigger is too narrow.

### Contributing Factors

1. The probe assumes the missing-endpoint shape is `404`; OpenAPI
   validators commonly signal an unknown path with `400`, so the
   trigger condition is too narrow for the upstream's actual behaviour.
2. The two-step probe is the one site outside `cli_common` that
   catches `HttpError`. That makes it easy to introduce a guard
   mismatch without affecting the rest of the error pipeline.
3. The existing tests in `TestCmdUserHttpErrors` exercise the
   400-propagates and both-404 cases but do not cover the case where
   the primary returns a non-`404` "path not found" and the fallback
   would succeed — so the dead-leg drift went unnoticed.
4. The module docstring advertises the two-step probe as live
   behaviour, which makes the divergence invisible to a reader who
   trusts the documentation.

## Technical Details

### Affected Code Locations

- **File**: `arr_cli/seerr.py`
  - **Function/Method**: `_try_user_path()`
  - **Lines**: roughly the `except HttpError as exc:` block inside
    `_try_user_path`; specifically the
    `if exc.status == 404: return False, exc / raise` branch pair.
  - **Issue**: the `raise` branch is the bug site. On Seer, the
    primary `/api/v1/user/me` returns `400` (OpenAPI "path unknown"),
    which falls into the `raise` branch and aborts the probe before
    the documented `/auth/me` fallback can run.

- **File**: `arr_cli/seerr.py`
  - **Function/Method**: `cmd_user()`
  - **Lines**: the two-step sequence — primary call to
    `_try_user_path(USER_ME_PATH, ...)` followed by the fallback call
    to `_try_user_path(AUTH_ME_FALLBACK_PATH, ...)`.
  - **Issue**: structurally correct but the upstream primary is no
    longer a real endpoint on Seer, so every successful `seerr user`
    invocation burns one extra round-trip on a path that always
    fails before reaching the working one.

- **File**: `arr_cli/seerr.py`
  - **Module docstring** at the top of the file.
  - **Issue**: documents the two-step probe as the live contract. If
    the probe is collapsed to a single path (Option B below), the
    docstring must be updated to match.

- **File**: `arr_cli/seerr.py`
  - **Constant**: `USER_ME_PATH = "/api/v1/user/me"`.
  - **Issue**: re-exported in `__all__`. After the fix it is either
    removed (Option B) or retained with an updated role (Option A).

- **File**: `tests/unit/test_seerr.py`
  - **Class/Method**: `TestCmdUserHttpErrors` — both
    `test_cmd_user_400_propagates_with_exit_four` and
    `test_cmd_user_both_404_exits_four`.
  - **Issue**: the 400-propagates test asserts the current (broken)
    behaviour as the contract. After Option B, the 400 test must be
    deleted and replaced with a single successful-request test that
    pins the new shape.

### Data Flow Analysis

```
seerr user -> cmd_user(args, cfg)
            -> _try_user_path(USER_ME_PATH="/api/v1/user/me")
                 -> _get(path, args, cfg, op="user probe ...")
                      -> transport.get(SERVICE_NAME, path, ...)
                           -> requests GET <base>/api/v1/user/me
                           -> response.status_code == 400
                           -> raises HttpError(status=400)  # transport.py
                 <- HttpError(status=400)
                 -> except HttpError as exc:
                        if exc.status == 404: ...  # False on Seer
                        raise  # <-- bubbles up unchanged
            <- HttpError(status=400) bubbles out of cmd_user
            -> main_wrapper sees HttpError, exit_code == 4
            -> emits `service=seerr op=/api/v1/user/me status=400 ...` on stderr
            -> sys.exit(4)
```

If the `raise` were absent or the `== 404` check were broadened to
include `400`, the same call would proceed to the second leg:

```
seerr user -> _try_user_path(AUTH_ME_FALLBACK_PATH="/auth/me")
                 -> transport.get(...) -> 200 -> user JSON
            <- (True, user JSON)
            -> _emit(user JSON, args, columns=None)
            -> sys.exit(0)
```

### Dependencies

- `arr_cli.facade.transport.get` — owns the HTTP call, status-code to
  typed-error mapping, and percent-encoding (no change required; this
  fix is in the per-service probe helper, which is the correct home
  per AGENTS.md §4.3).
- `arr_cli.facade.errors.HttpError` — exit code `4`, structured stderr
  line. Preserved by the fix; the single-probe variant re-raises it
  for any non-2xx response.
- `arr_cli.facade.output.emit` — renderer chain (`--human` /
  `--verbose` / default summary). Unaffected; the renderer sees a
  successful payload exactly as before.
- `responses` (tests-only) — `TestCmdUserHttpErrors` already uses it
  to mock HTTP. The rewritten tests continue to use `responses` and
  stay hermetic per AGENTS.md §7.5.

## Solution Approach

### Fix Strategy

Adopt **Option B** (recommended by the maintainer): collapse the probe
to a single `GET /auth/me` call. `USER_ME_PATH` is removed (or
retained only as a comment-level breadcrumb if useful for the next
maintainer), and `_try_user_path` and `cmd_user` are simplified to
call `_get("/auth/me", ...)` directly and forward the result (or any
`HttpError`) to `_emit` / `main_wrapper`.

The two-step probe was a defensive measure for the historical
Overseerr/Seerr split; on the live Seer deployment the primary leg
is dead weight and adds one noisy round-trip per call. Keeping the
probe would require either widening the trigger (e.g. treat `400`
from the OpenAPI validator as a fallback trigger) or special-casing
"OpenAPI validator error" bodies — both add complexity without
delivering value on the current upstream. Option B is the smallest
change in spirit: it acknowledges the upstream shift and removes the
no-longer-useful probe.

The error model is preserved: `_get` raises `HttpError` for non-2xx
responses, which propagates to `main_wrapper` and surfaces as exit
code `4` with the documented `service=seerr op=... status=...`
stderr line. A successful `/auth/me` response returns the user JSON
and exit `0`, exactly as expected.

**Why Option B over Option A** (broaden the trigger to fall back on
`400`):

- Option A would silently mask future real client errors (auth,
  server, body-shape) by falling back to a different endpoint that
  may also fail and produce a misleading "both paths failed"
  message.
- Option A keeps a noisy extra round-trip on every `seerr user` call
  for a primary path that does not exist on the current upstream.
- Option A complicates the contract: operators triaging via
  `--debug` would see two probe legs and have to reason about which
  one triggered the fallback.

### Alternative Approaches Considered

- **Option A**: broaden `_try_user_path`'s fallback trigger to
  include `400` (and possibly other OpenAPI-validator signatures),
  keeping both legs. Rejected — see above; masks real errors and
  keeps a dead leg.
- **Option C**: parse the response body for "must be number" /
  "Unknown query parameter" and treat that as the fallback trigger.
  Rejected — body-string matching is brittle and tightly couples the
  CLI to a specific validator version.

## Implementation Plan

### Changes Required

1. **Change 1**: collapse `cmd_user` to a single `/auth/me` call.
   - File: `arr_cli/seerr.py`
   - Modification: replace the two-step probe with a direct
     `_get(AUTH_ME_FALLBACK_PATH, args, cfg, op="user")` (or a
     simplified `_emit` of the payload). The `_try_user_path` helper
     becomes unused; remove it from `__all__` and the module. The
     synthesized "both paths 404" `HttpError` is no longer reachable
     and is removed.

2. **Change 2**: remove `USER_ME_PATH` from the module-level
   constants and `__all__`. Retain `AUTH_ME_FALLBACK_PATH` (or rename
   to `USER_ME_PATH` if a single constant is preferred for the new
   contract). If renamed, update any tests / docs that reference the
   constant by name.
   - File: `arr_cli/seerr.py`
   - Modification: delete the `USER_ME_PATH` constant and its
     `__all__` entry; either rename `AUTH_ME_FALLBACK_PATH` to a
     neutral name (e.g. `USER_ME_PATH = "/auth/me"`) or keep the
     fallback name with a docstring noting it is now the only path.

3. **Change 3**: update the module-level docstring of `arr_cli/seerr.py`
   to describe a single-path probe (`GET /auth/me`) and drop the
   two-step probe paragraph. Mention, in a single sentence if useful,
   that the historical two-step probe was a defensive measure for
   the Overseerr/Seerr split and was removed when the upstream path
   was confirmed to be `/auth/me` only.
   - File: `arr_cli/seerr.py`
   - Modification: rewrite the bullet for the `user` command and the
     "Seerr-specific behaviour" paragraph to match the new contract.

4. **Change 4**: update `cmd_user`'s docstring to match. Remove the
   references to the requirements path, the Overseerr spec path, and
   the 404-trigger language. Keep the auth-transparency sentence.
   - File: `arr_cli/seerr.py`
   - Modification: rewrite the `cmd_user` docstring.

5. **Change 5**: rewrite the `TestCmdUserHttpErrors` tests.
   - File: `tests/unit/test_seerr.py`
   - Modification: delete
     `test_cmd_user_400_propagates_with_exit_four` (the 400 on the
     primary path can no longer occur — `/api/v1/user/me` is no
     longer called). Delete
     `test_cmd_user_both_404_exits_four` (the both-404 synthesized
     error path is gone). Add a single successful-request test that
     pins the new contract: `GET /auth/me` → `200` → exit `0` →
     JSON user object on stdout. Optionally add a single failing
     test that pins the new contract: `GET /auth/me` → non-2xx →
     exit `4` → structured `service=seerr op=user status=<code>`
     stderr line. All tests must remain hermetic via `responses`
     (AGENTS.md §7.5).

6. **Change 6**: re-run `make ci` locally (== `make test &&
   make secret-scan && make smoke-dry`) before considering the
   change done. AGENTS.md §3 makes this the canonical local CI chain.
   - File: (no source change)
   - Modification: N/A — verification step.

### Testing Strategy

- **Unit (hermetic, `responses`-backed)**: rewrite
  `TestCmdUserHttpErrors` to cover (a) a successful
  `GET /auth/me → 200 → exit 0 → JSON payload on stdout` and (b) a
  failing `GET /auth/me → non-2xx → exit 4 → structured stderr line
  naming the op`. Update the class docstring accordingly. Keep the
  hermetic contract: no live HTTP in `tests/unit/` (AGENTS.md §7.5).
- **Manual smoke (opt-in)**: `make smoke-live` against the
  operator's Seer instance, gated by `RUN_LIVE=1` per AGENTS.md §3,
  to confirm the rewritten `seerr user` returns the user JSON on
  stdout and exits `0`. This is the only test that proves the fix
  against the actual divergent upstream; it stays opt-in.
- **CI gate**: `make ci` must pass (== `make test &&
  make secret-scan && make smoke-dry`).
