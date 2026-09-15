# fix_review

kind: let

source:
```prose
let fix_review = session: reviewer-tester
  prompt: "Review the bug fix ..."
```

---

VERDICT: APPROVED

Summary: The user-fallback fix is correct, minimal, and verified end-to-end on this revision. The prior review's REVISION_NEEDED blocker — the README §4.5 row still documenting the removed two-step probe (`/api/v1/user/me` with `/auth/me` fallback) — has been fully addressed: `README.md:207` now reads `| seerr user | GET | /auth/me | Auth self-check. |` (single path, no fallback wording), and `CHANGELOG.md` `[Unreleased] > Fixed` carries a matching bullet describing the collapse of the user probe. `make ci` is fully green (647 unit tests pass; lint, secret-scan, smoke-dry all pass), and `pytest tests/unit/test_seerr.py -v` shows 18/18 seerr tests passing including the rewritten `TestCmdUserHttpErrors` (success-path returns user JSON + exit 0; non-2xx surfaces exit 4 + structured `service=seerr op=/auth/me status=<code>` stderr line).

Review of the cumulative diff against `main` (7 files, +872/-223):

- `arr_cli/seerr.py` (core fix): `_try_user_path` helper removed, `AUTH_ME_FALLBACK_PATH` removed, `cmd_user` collapsed to a single `_get(USER_ME_PATH, args, cfg, op="user")` + `_emit(...)` call. `USER_ME_PATH = "/auth/me"` (re-exported in `__all__` for tests). The `HttpError` import is removed because the handler no longer catches it — `transport.get` raises `HttpError(exit_code=4)` for any non-2xx, which propagates to `main_wrapper` unchanged. Module-level docstring and `cmd_user` docstring are rewritten to describe the single `/auth/me` probe and to note the historical two-step probe was removed because Seer's OpenAPI validator returns `400` (not `404`) for unknown paths, which masked the divergence. The accompanying `seerr search` path fix (`/api/v1/search/multi` → `/api/v1/search`) and the matching help-text + `cmd_search` docstring update are also still in place.
- `tests/unit/test_seerr.py` (regression coverage): the `TestCmdUserHttpErrors` class is rewritten to pin the new contract — `test_cmd_user_success_returns_user_json_with_exit_zero` (200 → exit 0, user JSON on stdout, empty stderr, exactly one HTTP call) and `test_cmd_user_non_2xx_exits_four_with_structured_stderr` (503 → exit 4, empty stdout, structured stderr starting `service=seerr op=/auth/me status=503 ...`). The new `TestCmdSearch` class adds four regression tests pinning `/api/v1/search` + `query=` params and a defensive guard against any future copy-paste back to `/api/v1/search/multi`. All tests are hermetic via `responses` (AGENTS.md §7.5).
- `README.md`: the `seerr user` row is collapsed to `/auth/me` with "Auth self-check." note (no two-step language). The neighbouring `seerr search <query>` row reads `GET /api/v1/search?query=<query>`. README is now consistent with the code.
- `CHANGELOG.md`: a `### Fixed` bullet under `[Unreleased]` describes the collapse with the same level of detail used by the neighbouring `seerr search` and `seerr requests` entries — accurate, one entry per fixed bug, names the path divergence and points at the live OpenAPI spec source of truth.
- `.gitignore` + `.bugs/` documents: housekeeping; `bug-review.md` documents and `.tmp/` exclusion are expected.

Root-cause fix (not symptom-only): the bug was the two-step probe's `404`-only fallback trigger combined with Seer's OpenAPI-validator answering `400` for unknown paths. The fix collapses the probe to the single path Seer actually exposes (`/auth/me`), which removes the broken trigger entirely and also removes the dead-leg round-trip the bug review called out. Option A (broaden trigger) was correctly rejected per the bug-review analysis (would mask real client errors). The error model (exit 4 + structured stderr) is preserved per AGENTS.md §6.

Minimal and targeted: no drive-by refactoring; only the `cmd_user` flow, its constant, its docstrings, the `seerr search` help text + handler path (turn-1 fix still intact), the seerr CLI module docstring, the matching tests, the README row, and the CHANGELOG entry. The facade and per-service module boundaries (AGENTS.md §4.3) are respected — the change stays in `arr_cli/seerr.py` and its tests.

Conventions followed: `__all__` cleaned up, single-import for `ConfigError` only (no `HttpError`), Python ≥3.11 syntax with PEP 604 unions, no new runtime deps, two-space indent, hermetic `responses`-backed unit tests, `make ci` green.

Test results:

- `make ci` (== `make test && make secret-scan && make smoke-dry`): PASS. `make test` → 647 passed in 3.63s; `make secret-scan` → no committed secrets, `arr.conf.example` placeholder check OK; `make smoke-dry` → all smoke steps passed including `seerr --help lists 'user'`.
- `python3 -m pytest tests/unit/test_seerr.py -v`: 18/18 passed (includes both rewritten `TestCmdUserHttpErrors` tests and the four new `TestCmdSearch` tests).
- `seerr user --help`: shows `GET /auth/me` only — no fallback wording.
- `grep` audit for stale active-code references: `_try_user_path`, `AUTH_ME_FALLBACK_PATH` are gone; remaining mentions of `/api/v1/user/me` are limited to historical-breadcrumb comments/docstrings (lines 16, 49, 51, 102, 177 of `arr_cli/seerr.py`), which is appropriate context for the next maintainer.

Non-blocking notes:

- [MINOR] `arr_cli/seerr.py:102` — the `USER_ME_PATH` constant now points at `/auth/me`, which is what the bug review's Implementation Plan Change 2 explicitly recommended ("neutral name (e.g. `USER_ME_PATH = "/auth/me"`"). No action needed.
- [MINOR] `CHANGELOG.md` Unreleased `### Fixed` section now has three entries (search, user, requests). Reads cleanly; convention respected.
