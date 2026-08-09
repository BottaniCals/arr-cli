# Code Review: max-items-large-payload-cap
Date: 2026-08-03
Branch: feature/max-items-large-payload-cap
Reviewer: Automated Code Review

## Summary
The implementation delivers the documented large-payload cap contract. `MAX_ITEMS_DEFAULT = 10_000` is declared canonically in `arr_cli.facade.config` (and exported via `__all__`), `transport.get` accepts a keyword-only `max_items: int | None = MAX_ITEMS_DEFAULT` parameter, and oversized list payloads are truncated with a single `_logger.warning(...)` that names both the upstream count and the cap. All four previously-frozen `TestLargePayloadCap` tests now pass green with no skips, the full `make ci` chain (637 tests + secret-scan + smoke-dry) is green, and every user story acceptance criterion is met. One minor scope-creep nit: `_logger.propagate = False` was added without plan justification. Otherwise the implementation is clean, idiomatic, and consistent with project conventions.

**Overall assessment: APPROVED**

## User Story Coverage
| User Story | Status | Notes |
|------------|--------|-------|
| US-1 | ✅ | Truncation contract fully met: default == `MAX_ITEMS_DEFAULT`, explicit override supported, list truncation + warning + under-cap passthrough + non-list passthrough all verified. Truncation runs after `json.loads(body_bytes)`. |
| US-2 | ✅ | `MAX_ITEMS_DEFAULT: int = 10_000` declared in `arr_cli/facade/config.py` with matching docstring style; present in `__all__`; test file imports from `arr_cli.facade.config` (no local redeclaration). |
| US-3 | ✅ | `_transport_get_supports_max_items()` now returns True (via the new `max_items` parameter), `setUp` skip gate is untouched and falls through, all four test methods pass with no skips. |

## Design Adherence
| Component | Status | Notes |
|-----------|--------|-------|
| `MAX_ITEMS_DEFAULT` constant in `arr_cli/facade/config.py` | ✅ | Declared immediately after `DEFAULT_RETRY` with a 4-line docstring matching the surrounding `DEFAULT_*` style; added to `__all__` in the `DEFAULT_*` group. |
| `transport.get` keyword-only `max_items` kwarg | ✅ | `max_items: int | None = MAX_ITEMS_DEFAULT` placed after `read_timeout` and before `debug`, all keyword-only (post `*`). |
| Truncation placement after `json.loads(body_bytes)` | ✅ | Verified in `_do_request` at the post-parse stage; `return json.loads(body_bytes)` was replaced with `payload = json.loads(body_bytes)` and the truncation branch + `return payload` was added immediately after. |
| Single `_logger.warning(...)` naming upstream count AND cap | ✅ | Exact string `"arr_cli.facade.transport: truncated payload from %d items to %d (max_items cap)"` with `upstream_count, effective_cap` arguments — matches the plan template verbatim. |
| Single source of truth for `MAX_ITEMS_DEFAULT` | ✅ | Test file imports from `arr_cli.facade.config`; the locally-declared constant was removed (only the documented-budgets comment header remains). |
| `setUp` skip gate untouched | ✅ | `_transport_get_supports_max_items()` helper and the `setUp` skip logic are byte-identical to main; only the helper's return value flipped because the parameter now exists. |
| Test imports | ✅ | `from arr_cli.facade.config import AuthConfig, ServiceConfig, MAX_ITEMS_DEFAULT`. |

## Task Completeness
| Task | Status | Notes |
|------|--------|-------|
| 1.1 Declare `MAX_ITEMS_DEFAULT` after `DEFAULT_RETRY` | ✅ | Present at `arr_cli/facade/config.py:70-73`. |
| 1.2 Add to `__all__` in `DEFAULT_*` group | ✅ | Present at `arr_cli/facade/config.py:54`. |
| 2.1 Extend config import block | ✅ | `MAX_ITEMS_DEFAULT` added to the existing `from arr_cli.facade.config import (...)` block. |
| 2.2 Add keyword-only `max_items` between `read_timeout` and `debug` | ✅ | Signature reads `..., read_timeout: float | None = None, max_items: int | None = MAX_ITEMS_DEFAULT, debug: bool = False)`. |
| 2.3 Truncation branch + `_logger.warning` after `json.loads` | ✅ | `effective_cap = max_items if max_items is not None else MAX_ITEMS_DEFAULT` plus `isinstance(payload, list) and len(payload) > effective_cap` branch with single warning. |
| 3.1 Change test import line | ✅ | `from arr_cli.facade.config import AuthConfig, ServiceConfig, MAX_ITEMS_DEFAULT`. |
| 3.2 Remove local `MAX_ITEMS_DEFAULT` declaration | ✅ | Only the documented-budgets docstring remains; the local constant is gone. |
| 3.3 Leave `setUp` skip gate untouched | ✅ | Skip gate unchanged; helper now returns True automatically. |
| 4.1 Verify 4 `TestLargePayloadCap` tests pass | ✅ | All 4 pass with no skips (run output below). |
| 4.2 Verify full `make ci` green | ✅ | 637 unit tests pass, secret-scan OK, smoke dry-run OK. |

## Code Quality Issues
- [MINOR] `arr_cli/facade/transport.py:73-77` — The `_logger.propagate = False` change is scope creep. The plan's design section explicitly states "Python's default config writes WARNING+ to stderr", which is correct and sufficient for `redirect_stderr` to capture the new warning without any handler plumbing. The added 5-line propagation tweak is not in the plan, not in any task, and changes existing logging behavior (a future host test runner or external handler that subscribed to `arr_cli.facade.transport` DEBUG records would silently stop receiving them). In current production the change is a no-op (no parent handlers exist), so this is non-blocking — but it is an unexplained deviation and should either be removed or raised as a separate plan item.

## Test Results
```
$ pytest tests/unit/test_perf_budgets.py::TestLargePayloadCap -v
============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-7.2.1, pluggy-1.0.0+repack
rootdir: /workspace/projects/media-cli, configfile: pyproject.toml
collected 4 items

tests/unit/test_perf_budgets.py ....                                     [100%]

============================== 4 passed in 0.08s ===============================
```

```
$ make ci
make: [test] running pytest tests/unit
........................................................................ [ 11%]
........................................................................ [ 22%]
........................................................................ [ 33%]
........................................................................ [ 45%]
........................................................................ [ 56%]
........................................................................ [ 67%]
........................................................................ [ 79%]
........................................................................ [ 90%]
.............................................................            [100%]
637 passed in 3.57s
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

```
$ pytest tests/unit/ -v --no-header
============================= test session starts ==============================
collected 637 items

tests/unit/test_argparse_universal_flags.py ........                     [  1%]
tests/unit/test_cli_common.py .......................................... [  7%]
...................                                                      [ 10%]
tests/unit/test_config.py .................................              [ 16%]
tests/unit/test_errors.py ...................................            [ 21%]
tests/unit/test_example_validator.py ......................              [ 24%]
tests/unit/test_jellyfin.py ............................................ [ 31%]
..............                                                           [ 34%]
tests/unit/test_maintainerr.py ......................................... [ 40%]
........                                                                 [ 41%]
tests/unit/test_output.py .............................................. [ 48%]
........................................................................ [ 60%]
.                                                                        [ 60%]
tests/unit/test_perf_budgets.py ........................                 [ 64%]
tests/unit/test_radarr.py .............................................. [ 71%]
............................                                             [ 75%]
tests/unit/test_retry.py .............................                   [ 80%]
tests/unit/test_seerr.py .........                                       [ 81%]
tests/unit/test_sonarr.py .............................................. [ 89%]
............................                                             [ 93%]
tests/unit/test_transport.py .......................................... [100%]

============================= 637 passed in 3.60s ==============================
```

## Recommendations
- [Optional] Revert `_logger.propagate = False` (lines 73-77 of `arr_cli/facade/transport.py`) since it is not justified by the plan and silently changes existing DEBUG propagation semantics. Python's default `lastResort` handler already routes WARNING+ to stderr, so `redirect_stderr` continues to work without the change. If the propagation flip is intentional, document it explicitly in a follow-up plan rather than bundling it into this PR.
- [Optional] Add a trailing newline to `.specs/max-items-large-payload-cap/plan.md` (currently ends without one). Cosmetic only.
- [Optional] Future work could add a `ConfigError` validation pass for `max_items <= 0`; out of scope per the plan but worth a follow-up ticket if the surface grows.

## Verdict
**APPROVED** — ready for merge

The feature is delivered end-to-end, the previously-frozen skip tests now pass green, every user story acceptance criterion is satisfied, every plan task is complete, and project conventions are followed. The single MINOR note about `_logger.propagate = False` is non-blocking scope creep — the implementation would still work correctly without it, and it does not violate the documented contract or break any test. A clean follow-up commit can address the nit if desired, but the feature itself is mergeable as-is.
