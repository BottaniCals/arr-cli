# fix_result

kind: let

source:
```prose
let fix_result = session: bug-fixer
  prompt: "Implement the fix ..."
```

---

## Outcome

FIX APPLIED

Files modified: 2 (`arr_cli/seerr.py`, `tests/unit/test_seerr.py`)
Test results: `make ci` passes — 647 unit tests passed, secret-scan clean, smoke-dry OK
Summary: Collapsed the `seerr user` two-step probe (`/api/v1/user/me` → `/auth/me` on 404) to a single `GET /auth/me` call so the OpenAPI-validator 400 on the missing primary path no longer masks the working endpoint; `_try_user_path`, the `USER_ME_PATH=/api/v1/user/me` constant, the synthesized both-404 `HttpError`, and the `HttpError` import were removed; `TestCmdUserHttpErrors` was rewritten to pin the new contract (200 → exit 0 / JSON payload on stdout; non-2xx → exit 4 / structured `service=seerr op=/auth/me status=<code>` stderr line).
Commit: 89816a7d30a549dc341a35b3891c541201d51f1b

### Implementation notes (vs. bug-review's Implementation Plan)

- Change 1 (collapse `cmd_user`): done. `_try_user_path` removed from
  `arr_cli/seerr.py`; `cmd_user` now calls `_get(USER_ME_PATH, args,
  cfg, op="user")` directly and forwards the payload to `_emit`.
- Change 2 (`USER_ME_PATH`): renamed the constant to point at
  `/auth/me` and removed `AUTH_ME_FALLBACK_PATH` from the module and
  `__all__`. Tests/did not reference the old constant by name, so no
  other call sites needed updating.
- Change 3 (module docstring): rewritten to describe the single
  `/auth/me` probe and to mention the historical two-step probe and
  why it was removed.
- Change 4 (`cmd_user` docstring): rewritten to match the new
  contract and reference `USER_ME_PATH` only.
- Change 5 (tests): `TestCmdUserHttpErrors` rewritten. Deleted
  `test_cmd_user_400_propagates_with_exit_four` and
  `test_cmd_user_both_404_exits_four`. Added
  `test_cmd_user_success_returns_user_json_with_exit_zero` (200 → exit
  0 → JSON on stdout → empty stderr) and
  `test_cmd_user_non_2xx_exits_four_with_structured_stderr` (503 →
  exit 4 → structured `service=seerr op=/auth/me status=503 message=`
  stderr line). Tests remain hermetic via `responses`.
- Change 6 (`make ci`): executed; all three leaves pass (test,
  secret-scan, smoke-dry).

### Files changed
```
 arr_cli/seerr.py         | 195 +++++++++++------------------------------------
 tests/unit/test_seerr.py | 132 +++++++++++++++-----------------
 2 files changed, 109 insertions(+), 218 deletions(-)
```

### Verification commands
- `make lint` → `py_compile sweep over arr_cli/ and tests/` OK
- `make ci` → 647 passed, secret-scan clean, smoke-dry OK

### Out of scope (intentionally not touched)
- `.bugs/seerr-user-fallback-4xx/bug-review.md` — per instructions, do
  not modify.
- Other `seerr` commands (`requests`, `request-count`, `search`,
  `available`, `media`) — unaffected by this fix; their tests
  continue to pass.
- `arr_cli/facade/transport.py` — the bug-review explicitly states
  this fix belongs in the per-service probe helper, not in the
  facade.
