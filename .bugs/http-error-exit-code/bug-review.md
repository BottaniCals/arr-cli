# Bug Report

## Bug Summary

The reported symptom is that HTTP 4xx responses from the arr-cli services surface the correct structured `service=… op=… status=… message=…` line on stderr but exit the process with code `0` (success) instead of code `4` (HttpError) — directly violating the canonical exit-code map documented in `AGENTS.md` §6 and `README.md`. The two repros cited are `jellyfin --human recent` (HTTP 400 with body `The value 'jellyfin' is not valid`) and `seerr user` (HTTP 400 with body `request/params/userId must be number`).

## Bug Details

### Expected Behavior

Per `AGENTS.md` §6 and the facade's documented contract:

| HTTP status | Class       | Exit code |
| ----------- | ----------- | --------- |
| 401 / 403   | `AuthError`    | 2 |
| other 4xx   | `HttpError`    | 4 |
| 5xx         | `HttpError`    | 4 |
| DNS / conn  | `NetworkError` | 3 |
| malformed JSON | `ParseError` | 5 |
| config bad  | `ConfigError`  | 1 |

A 4xx response (other than 401 / 403) MUST cause the CLI to (a) print the structured `service=… op=… status=<code> message=…` line to stderr, and (b) exit with code `4`. Operators scripting against the CLI rely on `$?` to choose between retry, auth-refresh, and bug-report paths.

### Actual Behavior

Investigation result on the current `main` branch (commit `cddb754`): **the reported symptom does NOT reproduce on the current code**. Both repros surface the structured stderr line AND exit with code `4`:

- `jellyfin --human recent` against a mocked HTTP 400 → exits `4`, stderr reads `service=jellyfin op=/Users/alice/Items status=400 message=jellyfin: HTTP 400 for /Users/alice/Items; body="The value 'jellyfin' is not valid"`.
- `seerr user` against a mocked HTTP 400 (primary path fails, `_try_user_path` re-raises) → exits `4`, stderr reads `service=seerr op=/api/v1/user/me status=400 message=seerr: HTTP 400 for /api/v1/user/me; body='request/params/userId must be number'`.

That said, the bug task description and the observed fragility of one code path in the seerr fallback make it worthwhile to record this as a review and lock in the behaviour with regression tests — see the sections below for the latent-risk summary, the data-flow analysis, and the testing strategy.

### Steps to Reproduce (per the bug report)

1. Configure `arr.conf` for jellyfin with a valid URL and credentials.
2. Run `jellyfin --human recent` against the configured instance.
3. Observe `service=jellyfin op=/Users/alice/Items status=400 message=…` on stderr.
4. Inspect `$?` — per the bug report it is `0`; per my investigation on the current code it is `4`.

Equivalent steps for `seerr user` instead of `jellyfin --human recent`.

## Impact Assessment

### Severity

- [ ] Critical - System unusable
- [x] High - Major functionality broken (per the bug report's framing)
- [ ] Medium - Feature impaired but workaround exists
- [ ] Low - Minor issue or cosmetic

Marked High based on the report's framing (a class of 4xx responses being reported as success would silently break scripted retries, kill consumer monitoring, and mask upstream-config drift). On the current code the severity downgrades to Low because the symptom does not reproduce; the real risk is a future regression on the only line that catches `HttpError` outside the wrapper.

### Affected Features

- `arr_cli.facade.cli_common` exit-code translation contract (REQ-4 AC2).
- Every per-service CLI (`jellyfin`, `radarr`, `sonarr`, `maintainerr`, `seerr`).
- The seerr two-step `user` probe (a `_try_user_path` `except HttpError` exists that, if mishandled, is the one place an `HttpError` could be swallowed).

## Additional Context

### Error Messages

The structured stderr line is built by `arr_cli.facade.errors.ArrError.__str__` and emitted by `arr_cli.facade.cli_common._handle_arr_error` via `_emit_error_line`. The line shape for `HttpError` is:

```text
service=<svc> op=<op> status=<http_status> message=<message>
```

with `message` carrying the per-class body excerpt (`HTTP <code> for <path>; body=<excerpt>!r` per `transport.py:_body_excerpt`).

### Related Issues

- `tests/unit/test_cli_common.py::TestMainWrapperExitCodes::test_http_error_returns_four` — existing positive test that asserts `main_wrapper` translates a handler that raises `HttpError` into exit code `4` (passing on the current code).
- `tests/unit/test_jellyfin.py::TestEndToEnd::test_main_item_404_exit_code` — existing test that asserts the end-to-end exit code for a `cmd_item` 404 (passing). Note that there is NO equivalent end-to-end coverage for `cmd_recent` 400 or for the seerr `user` probe.
- `pr #9 / fix(lookup-monitored-column-fix)` — adjacent fix; demonstrates the project's pattern of bundling a regression test alongside a fix.

## Analysis

### Investigation Summary

I followed the data flow for both repros end-to-end, then exercised both end-to-end with the `responses` library against a mocked HTTP 400. The transport layer (`arr_cli.facade.transport.get`) raises `HttpError(service, op, message, status=…)` from the `200 <= status < 300` else-branch (around line ~492 in `transport.py`). The exception carries `exit_code = 4` (class default in `arr_cli.facade.errors.HttpError`). From there the path is uniform:

1. `transport.get` raises `HttpError`.
2. The per-command handler (`cmd_recent`, `cmd_user`, …) does not catch the exception; it bubbles up.
3. `arr_cli.facade.cli_common.main_wrapper` catches it in `except ArrError as exc:` (line ~483) and returns `_handle_arr_error(exc, debug=debug)`.
4. `_handle_arr_error` (line ~332) writes `str(exc)` to stderr via `_emit_error_line` AND returns `exc.exit_code` (`4`).
5. The console-script wrapper at `/usr/local/bin/jellyfin`, `/usr/local/bin/seerr`, … does `sys.exit(main())`, so the process exits `4`.

For seerr's two-step user probe there is exactly one `except HttpError` outside `cli_common` (`arr_cli.seerr._try_user_path`, line ~219). That handler checks `exc.status == 404` to trigger the fallback; for ANY other status (including 400) it does a bare `raise` so the structured message and `exit_code = 4` flow through the normal path. Both branches were exercised against the mock and both return `4`.

I also checked `_try_user_path`'s "both legs 404" branch (`cmd_user`'s last three lines), which builds a fresh `HttpError(SERVICE_NAME, "user", "...both...", status=404)` and re-raises it — that also exits `4`.

So, on the current code, the reported symptom is not reproducible. The bug review is still useful because:

- The existing test net does NOT cover the two specific repros in the report. `test_jellyfin.py` covers `cmd_item` 404 but not `cmd_recent` 400; `test_seerr.py` covers neither HTTP 4xx nor any failure mode.
- The only place outside `cli_common` that catches `HttpError` (`seerr._try_user_path`) is the one place where a future refactor could plausibly swallow the exit code (e.g. by changing the `raise` to `return False, exc` for non-404, or by refactoring the surrounding logic so the exception path is no longer a bare `raise`).
- The existing CLI smoke test (`scripts/smoke.sh` --dry-run) does not exercise the HTTP-4xx exit-code contract at all; only `make integration-test` does, and only against an opt-in live instance.

### Root Cause

**On the current `main` branch, no root cause is found.** Both repro paths flow through `arr_cli.facade.cli_common._handle_arr_error`, which returns `exc.exit_code` (`HttpError.exit_code == 4`); the console scripts wrap that with `sys.exit`. Running the reproducer end-to-end against mocked 4xx responses yields exit code `4`, not `0`.

**On any future regression that touches the exception pipeline without a guard test, the most likely cause would be one of:**

1. A per-service handler catching `HttpError` and returning an integer literal (e.g. `return 0`) instead of `raise` / re-raising. The only current site that could mutate into this is `arr_cli.seerr._try_user_path` (line ~219) — its `raise` is the single line standing between a swallowed exit code and the structured-error-and-exit-4 path.
2. `arr_cli.facade.cli_common.main_wrapper` losing the `return _handle_arr_error(...)` chain (e.g. a future maintainer adds another `try` block and forgets the `ArrError` arm).
3. A new wrapper / decorator around `handler(args, cfg)` swallowing the exception (none exists today, but the design has a single funnel through `main_wrapper` so a future one would be the obvious place for a regression).

In the absence of a reproducible regression the right behaviour is to lock the current correct behaviour in with regression tests (see Testing Strategy), turning this from "an unverifiable historical bug" into "a guard against a plausible future one".

## Technical Details

### Affected Code Locations

These are the call sites that participate in the reported symptom. None is currently broken; the list is the auditable surface for a future regression.

- **File**: `arr_cli/facade/errors.py`
  - **Function/Method**: `HttpError` (class) and `ArrError.__init__` (line ~67)
  - **Lines**: ~144-162 (class body), ~75-92 (`exit_code` instance attribute defaulting)
  - **Issue**: Defines `exit_code = 4`. Verification only — keep this declaration.

- **File**: `arr_cli/facade/transport.py`
  - **Function/Method**: `get` — HTTP 4xx raise site
  - **Lines**: ~483-498 (the `if not (200 <= status < 300): raise HttpError(...)` branch)
  - **Issue**: The one place the 4xx message is built and the `HttpError` is instantiated. Verification only — keep.

- **File**: `arr_cli/facade/cli_common.py`
  - **Function/Method**: `_handle_arr_error` — stderr + exit-code translator
  - **Lines**: ~332-345
  - **Issue**: Returns `exc.exit_code` at the end. The single line below is critical: `return exc.exit_code`. A future maintainer replacing this with `return 0` would introduce the reported symptom for every `ArrError` subclass.

- **File**: `arr_cli/facade/cli_common.py`
  - **Function/Method**: `main_wrapper` — handler dispatch
  - **Lines**: ~480-486 (the `try: result = handler(args, cfg)` / `except ArrError as exc:` arm)
  - **Issue**: Returns `_handle_arr_error(exc, debug=debug)`. Verification only.

- **File**: `arr_cli/seerr.py`
  - **Function/Method**: `_try_user_path` — `except HttpError` fallback decoder
  - **Lines**: ~210-233
  - **Issue**: Catches `HttpError`, returns `(False, exc)` for `exc.status == 404` and re-raises for everything else. The bare `raise` at line ~232 is the single line that has to remain a `raise` and not become a `return False, exc`. Wrapping it in any logging / metrics decorator that absorbs the exception would be enough to introduce the reported symptom.

- **File**: `arr_cli/{jellyfin,radarr,sonarr,maintainerr}.py`
  - **Function/Method**: per-command handlers (`cmd_recent`, `cmd_item`, `cmd_series`, …)
  - **Lines**: handler bodies
  - **Issue**: None today. Verification only — none of these files catch `HttpError`. If a future command needs structured logging around a `_get` call, that log handler must NOT catch `HttpError`.

### Data Flow Analysis

```
[console script]  sys.exit(main())
      │
      ▼
[main()]          parses argparse, calls main_wrapper
      │
      ▼
[main_wrapper]    ┌─ handler(args, cfg)
      │           │       │
      │           │       ▼
      │           │   [transport.get]
      │           │       │  HTTP 4xx
      │           │       ▼
      │           │   raises HttpError(status, exit_code=4)
      │           │
      │           ▼  bubbles up
      │     except ArrError as exc
      │           │
      │           ▼
      │     _handle_arr_error(exc)
      │           │
      │           ├── print(str(exc))         (stderr service=… op=… status=… message=…)
      │           └── return exc.exit_code    (4)
      ▼
sys.exit(4)
```

A regression that introduces exit `0` would have to break the `_handle_arr_error → return exc.exit_code` link or the `main_wrapper → _handle_arr_error(exc)` link. The seerr `_try_user_path` is the only place outside `cli_common` that intercepts an `HttpError`, and the way for it to drop the exit code is to swallow the `raise` (line ~232).

### Dependencies

- `requests` (HTTP client) — owns the `response.status_code` that drives the raise.
- `responses` (tests only) — already a dev dep per `AGENTS.md` §4.1; needed for the regression tests.
- `argparse` (stdlib) — owns `--human` / `--verbose` flag parsing; the bug's `--human` framing is incidental (the same exit code path runs without `--human`).

## Solution Approach

### Fix Strategy

There is no source-code fix to apply on the current branch — `_handle_arr_error` and `main_wrapper` already return the documented exit code, and the per-service handlers do not intercept `HttpError` outside the documented seerr probe.

The actionable fix is to **lock in the correct behaviour with regression tests** so the next refactor that touches the exception pipeline (e.g. a logger decorator wrapping `_get`, or a metrics hook in `_try_user_path`) cannot silently flip the exit code to `0` without breaking CI. Concretely:

1. Add an end-to-end regression test per documented service (`jellyfin`, `radarr`, `sonarr`, `maintainerr`, `seerr`) that mocks a 4xx response with the `responses` library, runs `main(...)`, and asserts `exit_code == 4` AND that stderr contains `service=… op=… status=<code> message=…`.
2. Add a regression test for the seerr `user` probe that mocks the primary path with a non-404 status (e.g. 400) and asserts the exception bubbles up unchanged (asserting `exc.exit_code == 4`, the structured stderr line, and the `main()` exit code).
3. Add a regression test for the seerr `user` probe that mocks both paths with 404 and asserts the fallback `HttpError(SERVICE_NAME, "user", "…", status=404)` surfaces unchanged (same assertions).

These tests are the practical "fix" the bug review can deliver — they pin the contract and would have caught a regression if one had existed.

### Alternative Solutions

- **Wrap every `cmd_*` handler in `try / except HttpError as exc: return exc.exit_code`**: rejected. Adds noise, duplicates the wrapper's job, and is the kind of change a future maintainer could easily break.
- **Move `_try_user_path`'s 404 detection into the wrapper**: rejected. The two-step probe is a seerr-specific design choice (REQ-10 AC6, design.md "Pre-locking Verifications -- Seerr") and `_try_user_path` is the documented seam.
- **Replace `HttpError` propagation with a return-code convention**: rejected. The whole facade is built around typed exceptions (REQ-4, AGENTS.md §6) and the "Errors always include `service: op=...` (and an `id=` when relevant) on stderr. Consumers scripting against this CLI depend on those stable codes and shapes" sentence is explicit.

### Risks and Trade-offs

- Adding more end-to-end `main(...)`-based tests raises the per-test wall-clock cost slightly (each goes through the full argparse / config-load / dispatch funnel). The added cost is small (a handful of milliseconds each) and is the right price for a regression net.
- The seerr `user` tests need to mock BOTH probe paths to test the "both 404" branch; using `responses.add_passthrough` for everything else keeps the mock hermetic.
- Choosing NOT to change source code is a deliberate trade — leaving the code alone prevents any drift; locking the behaviour with tests gives the agent / reviewer the same safety net that a code fix would.

## Implementation Plan

### Changes Required

1. **Change 1**: Add an end-to-end regression test for `jellyfin --human recent` against an HTTP 400.
   - File: `tests/unit/test_jellyfin.py` (new test alongside `TestEndToEnd`)
   - Modification: New method `test_main_recent_400_exits_four_with_structured_stderr(self)`. Uses `responses.RequestsMock` to stub `GET http://example/Users/alice/Items` → status 400, body `"The value 'jellyfin' is not valid"`. Calls `main(["--config", str(self.cfg_path), "--human", "recent"])`. Asserts `exit_code == 4` and that the captured stderr starts with `service=jellyfin op=/Users/alice/Items status=400 message=`.

2. **Change 2**: Add an end-to-end regression test for `cmd_item` against the seerr-style "param validation" 400 (mirror of `test_main_item_404_exit_code` but at HTTP 400, to cover the non-404 path explicitly).
   - File: `tests/unit/test_jellyfin.py`
   - Modification: New method `test_main_item_400_exits_four(self)`. Mocks `transport.get` to raise `HttpError("jellyfin", "item id=42", "jellyfin: HTTP 400 for /Items/42", status=400)`. Calls `main(["--config", str(self.cfg_path), "item", "42"])`. Asserts `exit_code == 4` and stderr `status=400`.

3. **Change 3**: Add an end-to-end regression test for `seerr user` against an HTTP 400 on the primary path.
   - File: `tests/unit/test_seerr.py` (new class `TestCmdUserHttpErrors`)
   - Modification: New method `test_cmd_user_400_propagates_with_exit_four`. Uses `responses.RequestsMock` to register only `GET http://example.com/api/v1/user/me` → status 400, body `'request/params/userId must be number'`. Calls `main(["--config", str(self.cfg_path), "user"])`. Asserts `exit_code == 4` AND that stderr contains `service=seerr op=/api/v1/user/me status=400 message=`.

4. **Change 4**: Add an end-to-end regression test for `seerr user` against "both paths 404" to lock the fallback `HttpError`.
   - File: `tests/unit/test_seerr.py`
   - Modification: New method `test_cmd_user_both_404_exits_four`. Registers both `GET http://example.com/api/v1/user/me` and `GET http://example.com/auth/me` to status 404. Calls `main([...])`. Asserts `exit_code == 4` AND stderr contains the fallback message naming both paths.

5. **Change 5**: Add a regression test for the ratcheting 4xx-exit-code claim in `tests/unit/test_transport.py`.
   - File: `tests/unit/test_transport.py`
   - Modification: New method alongside the existing 401 / 403 / 500 tests asserting that an HTTP 400 / 404 / 500 from `transport.get` raises `HttpError(status=N, exit_code=4)` (already mostly covered; tighten by adding an explicit `assert exc.exit_code == 4` for each status).

### Testing Strategy

The `make test` chain (per `AGENTS.md` §3) is `pytest tests/unit`; `responses` is already a dev dependency. The new tests fit cleanly into that pipeline:

- **`responses.RequestsMock`** — registers both `api/v1/user/me` and `auth/me` URLs with `match_querystring=False`. Set `assert_all_requests_are_fired=False` for tests where only the primary leg fires (the secondary should never be called).
- **Minimum coverage matrix** — for each of the five services, add at least one 4xx end-to-end test (jellyfin/radarr/sonarr/maintainerr `cmd_*` returning 400 → exit 4; seerr `user` returning 400 on primary → exit 4; seerr `user` returning 404 on both → exit 4; one explicit 401 → 2 and 403 → 2 check; one explicit 5xx → 4 check).
- **Stderr capture** — use the existing `_capture_stderr_stdout` / `_capture_stderr` helpers that already exist in the test files; they redirect `sys.stderr` to a `StringIO`.
- **Hermetic guardrails** — every test must keep `responses.RequestsMock` strict (no `assert_all_requests_are_fired=False` shortcuts) except where multiple legs are intentional, in which case the comment must explain why.
- **`make ci` integration** — these tests are `make test`-eligible; no `RUN_LIVE` env var is needed, no live HTTP is performed.

The lock-in set is the deliverable: a clean PR introducing the five (or more) regression tests would prevent any future regression of the reported symptom, and would have failed loudly if the current code had ever silently returned `0`.
