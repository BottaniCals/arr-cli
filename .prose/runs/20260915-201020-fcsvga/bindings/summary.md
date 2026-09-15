# summary

kind: output

source:
```prose
output summary = session "Generate the final completion report for the bug fix of 'seerr-user-fallback-4xx'"
```

---

# 🐛 Bug Fix: seerr-user-fallback-4xx

## Branch
- **Branch:** `fix/seerr-user-fallback-4xx`
- **Repository:** github.com/BottaniCals/media-cli
- **Base:** merge commit `d9e7338` (the prior `seerr-requests-paginated-envelope` PR #16 merge)
- **Commits this run:** 8 (8 new commits on top of base)
- **Files changed:** 11 (+1011 / −223)

## Bug
- **Summary:** `seerr user` exits with code `4` and a `service=seerr op=user` stderr line against the live Seer deployment, instead of exiting `0` with the authenticated user object on stdout. The CLI's two-step probe (`/api/v1/user/me` then fallback to `/auth/me`) was designed to fall back when the primary returns `404`. On Seer, the primary returns `400` (the OpenAPI validator's "path unknown"), so the strict `exc.status == 404` guard inside `_try_user_path` never fires; the `HttpError` re-raises and propagates to `main_wrapper` as exit code `4`.
- **Root cause:** Two-fold: (a) Seer's OpenAPI validator answers `400` (not `404`) for unknown paths, so the narrow `404`-only fallback trigger never activates; (b) Seer subsequently dropped `/api/v1/user/me` from its live OpenAPI spec in favour of `/auth/me` only, making the primary leg a dead round-trip on every successful `seerr user` call. The two-step probe predates the current Seer API surface.
- **Severity:** High — major functionality broken. `seerr user` is the only documented way to confirm a `X-Api-Key` works against Seer from the CLI without a manual `curl` probe (REQ-10 AC6); operator-side automation that asserts a non-zero exit means "auth is wrong" rather than "endpoint is wrong" is also affected.
- **Bug review:** `/workspace/projects/media-cli/.bugs/seerr-user-fallback-4xx/bug-review.md`

## Fix
- **Strategy:** Adopted **Option B** (the maintainer-recommended approach): collapse the two-step probe to a single `GET /auth/me` call. `USER_ME_PATH` now points at `/auth/me`; the `_try_user_path` helper, the `AUTH_ME_FALLBACK_PATH` constant, and the synthesised "both-404" error path are removed. The error model is preserved — any non-2xx response from `_get` raises `HttpError(exit_code=4)` which propagates to `main_wrapper` and surfaces as the documented `service=seerr op=... status=...` stderr line. **Option A** (broaden the fallback trigger to also match `400`) was rejected because it would silently mask future real client errors (auth failures, server errors, body-shape errors) by falling back to a different endpoint that may itself fail and produce a misleading "both paths failed" message.
- **Files touched:**
  - `arr_cli/seerr.py` (+29 / −177) — `_try_user_path` helper removed; `cmd_user` collapsed to a single `_get(USER_ME_PATH, args, cfg, op="user")` + `_emit(...)` call; `USER_ME_PATH = "/auth/me"`; `HttpError` import removed; module and `cmd_user` docstrings rewritten to describe the single-path probe and note that the historical two-step probe was removed when the upstream path was confirmed to be `/auth/me` only. `__all__` cleaned up.
  - `tests/unit/test_seerr.py` (+229 / −73) — `TestCmdUserHttpErrors` rewritten to pin the new contract hermetically via `responses`; companion `TestCmdSearch` class also added for the in-place `seerr-search-wrong-api-path` fix.
  - `README.md` (+2 / −2) — `seerr user` row collapsed to `GET /auth/me` with "Auth self-check." note (no two-step fallback wording); neighbouring `seerr search <query>` row reads `GET /api/v1/search?query=<query>`.
  - `CHANGELOG.md` (+20) — `[Unreleased] > ### Fixed` bullet describing the user-probe collapse (matches the neighbouring `seerr search` and `seerr requests` entries in style and detail).
  - `.gitignore` (+3) — `.tmp/` scratch-directory exclusion (plus a follow-up trim of its explanatory comments).
  - `.bugs/seerr-user-fallback-4xx/bug-review.md` (+387) — bug analysis and root-cause writeup (new).
  - `.bugs/seerr-search-wrong-api-path/bug-review.md` (+173) — bug analysis for the in-place search fix (new; included in this branch as a companion).
  - `.prose/runs/20260915-201020-fcsvga/bindings/{bug_analysis,fix_result,fix_review,fix_revision}.md` — workflow provenance artefacts (bug analysis, fix result, reviewer verdict, revision note).
- **Regression tests:** `tests/unit/test_seerr.py::TestCmdUserHttpErrors` rewritten to pin the new contract hermetically via `responses` (no live HTTP, per AGENTS.md §7.5):
  - `test_cmd_user_success_returns_user_json_with_exit_zero` — `GET /auth/me` → 200 → exit 0, user JSON on stdout, empty stderr, exactly one HTTP call.
  - `test_cmd_user_non_2xx_exits_four_with_structured_stderr` — `GET /auth/me` → 503 → exit 4, empty stdout, structured `service=seerr op=/auth/me status=503 ...` stderr line.

## Review
- **Final verdict:** APPROVED
- **Turns used:** 2 of 3 (Turn 1: REVISION_NEEDED on the README §4.5 row + the CHANGELOG `[Unreleased] > ### Fixed` entry; Turn 2: APPROVED once the README row was collapsed to `GET /auth/me` with "Auth self-check." and the matching CHANGELOG bullet was added).
- **Outstanding issues (if any):** None — reviewer approved. The reviewer flagged two non-blocking notes: (a) `arr_cli/seerr.py:102` — `USER_ME_PATH` now correctly points at `/auth/me`, which matches the bug review's recommended neutral-name Option B; no action needed. (b) `CHANGELOG.md` `[Unreleased] > ### Fixed` now carries three entries (search, user, requests); reads cleanly, convention respected.

## Notes
- **No live smoke was executed as part of `make ci`.** Per AGENTS.md §3 and §7.5, `make smoke-dry` only checks the help-text grammar (step 2/3 of `scripts/smoke.sh`); step 3 is opt-in via `RUN_LIVE=1 make smoke-live`. Operators with access to a live Seer instance should run `RUN_LIVE=1 make smoke-live` to confirm the rewritten `seerr user` returns the user JSON on stdout and exits `0` against their actual upstream. This is the only verification that proves the fix against the live divergent API; the unit-test suite remains hermetic by contract.
- **In-place companion fix.** This branch also carries the `seerr-search-wrong-api-path` fix (commit `b48688c`), a separate correction that the reviewer re-verified as still intact. It is independent of the user-fallback fix but shares the same root-cause family (Seer API drift away from `/api/v1/...` endpoints toward `/auth/me` and `/api/v1/search`).
- **`_try_user_path` is fully removed.** Any external code or downstream tooling that imported `_try_user_path` from `arr_cli.seerr` will break; this is intentional and matches the bug review's "Option B" recommendation. `__all__` is updated to match.
- **Historical breadcrumbs retained.** A grep audit shows remaining mentions of `/api/v1/user/me` in `arr_cli/seerr.py` are limited to historical-breadcrumb comments/docstrings (lines 16, 49, 51, 102, 177) — appropriate context for the next maintainer per the reviewer's assessment.
- **No source files outside the planned scope were modified.** The diff is contained to `arr_cli/seerr.py`, its tests, the documentation surfaces (README, CHANGELOG, bug-review), `.gitignore`, and the workflow provenance bindings — no facade, transport, or per-service boundary crossings.

## `make ci` verification output

`make ci` (== `make test && make secret-scan && make smoke-dry`) — **all checks passed**.

Raw tail captured from `make ci 2>&1`:

```
make: [test] running pytest tests/unit
........................................................................ [ 11%]
........................................................................ [ 22%]
........................................................................ [ 33%]
........................................................................ [ 44%]
........................................................................ [ 55%]
........................................................................ [ 66%]
........................................................................ [ 77%]
........................................................................ [ 89%]
.......................................................................  [100%]
647 passed in 3.86s
make: [secret-scan] running scripts/secret-scan
example-lint: OK: /workspace/projects/media-cli/arr.conf.example contains only documented placeholders
secret-scan: scanning /workspace/projects/media-cli for committed secrets...
secret-scan: no committed secrets detected
make: [smoke-dry] running scripts/smoke.sh --dry-run
smoke.sh: step 1/3: cold-start probe (time python -c "import arr_cli.jellyfin")
smoke.sh:   cold-start probe OK (0s; budget: 2.0s)
smoke.sh: step 2/3: per-service command grammar check (mode=dry-run)
smoke.sh:   checking: jellyfin now
smoke.sh:     OK: jellyfin --help lists 'now'
smoke.sh:   checking: radarr calendar
smoke.sh:     OK: radarr --help lists 'calendar'
smoke.sh:   checking: sonarr calendar
smoke.sh:     OK: sonarr --help lists 'calendar'
smoke.sh:   checking: maintainerr health
smoke.sh:     OK: maintainerr --help lists 'health'
smoke.sh:   checking: seerr user
smoke.sh:     OK: seerr --help lists 'user'
smoke.sh: step 3/3: skipped (dry-run; pass --live with RUN_LIVE=1 to run)
smoke.sh: all smoke steps passed
make: [ci] all checks passed
```

Pass / fail counts:
- `make test` → **647 passed in 3.86s** (`pytest tests/unit`).
- `make secret-scan` → **no committed secrets detected**; `arr.conf.example` placeholder check **OK**.
- `make smoke-dry` → **all smoke steps passed** (cold-start probe OK; per-service grammar checks OK for `jellyfin now`, `radarr calendar`, `sonarr calendar`, `maintainerr health`, `seerr user`; live step skipped per `--dry-run`).
- Final make line: `make: [ci] all checks passed`.

Focused seerr verification (per reviewer audit):
- `python3 -m pytest tests/unit/test_seerr.py -v` → **18/18 passed** (includes both rewritten `TestCmdUserHttpErrors` tests and the four `TestCmdSearch` regression tests).
- `seerr user --help` → shows `GET /auth/me` only — no fallback wording.
- Grep audit → `_try_user_path` and `AUTH_ME_FALLBACK_PATH` are gone from active code; remaining `/api/v1/user/me` mentions are limited to historical-breadcrumb comments/docstrings.
