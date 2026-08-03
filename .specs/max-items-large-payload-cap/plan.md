# Plan: max-items-large-payload-cap

## Summary

Wire up the documented large-payload cap contract on `arr_cli.facade.transport.get` by adding a keyword-only `max_items` parameter (default `MAX_ITEMS_DEFAULT = 10_000`) and truncating oversized list responses with a single stderr warning that names both the upstream item count and the cap. The change is confined to the facade (`config.py`, `transport.py`) plus the matching test edits (`tests/unit/test_perf_budgets.py`), so the four currently-frozen skip tests in `TestLargePayloadCap` stop skipping and pass green, closing the documented gap without touching the network, auth, or per-service CLIs.

## Requirements

### User Story 1

**User Story:** As an `arr-cli` operator invoking a service command against a large collection, I want `transport.get` to truncate oversized list payloads to a documented cap and warn me on stderr, so that a runaway upstream response (e.g. Jellyfin `/Items` with 15 000 entries) cannot blow up my downstream pipeline or render budget while I still get a deterministic, bounded result.

#### Acceptance Criteria

1. WHEN `transport.get` is called with the default `max_items` THEN the parameter default SHALL equal `MAX_ITEMS_DEFAULT` (`10_000`) as a sentinel int, not `None`.
2. WHEN `transport.get` is called with an explicit `max_items` int THEN that int SHALL override the default for that call.
3. WHEN the parsed JSON payload is a `list` whose length is greater than `max_items` THEN `transport.get` SHALL return the list truncated to `max_items` items and SHALL emit exactly one stderr warning containing both the upstream item count and the cap value as decimal strings.
4. WHEN the parsed JSON payload is a `list` whose length is less than or equal to `max_items` THEN `transport.get` SHALL return the list verbatim and SHALL emit no stderr output.
5. WHEN the parsed JSON payload is a `dict`, scalar, or `None` THEN `transport.get` SHALL return the value verbatim and SHALL emit no stderr output.
6. WHEN truncation logic runs THEN it SHALL execute after `json.loads(body_bytes)` succeeds and before the value returns to the caller.

### User Story 2

**User Story:** As a maintainer of the `arr-cli` facade, I want the `max_items` cap default to live as a single canonical constant in `arr_cli.facade.config` alongside the other transport defaults, so that any future change to the cap (or any consumer that needs to import it) has one auditable source of truth.

#### Acceptance Criteria

1. IF `MAX_ITEMS_DEFAULT` is added to `arr_cli.facade.config` THEN it SHALL be declared as `MAX_ITEMS_DEFAULT: int = 10_000` with the same module-level documentation style as `DEFAULT_CONNECT_TIMEOUT` / `DEFAULT_READ_TIMEOUT` / `DEFAULT_RETRY` / `DEFAULT_CONFIG_PATH`.
2. IF `MAX_ITEMS_DEFAULT` is added to `arr_cli.facade.config` THEN it SHALL be present in that module's `__all__` list.
3. IF `tests/unit/test_perf_budgets.py` references `MAX_ITEMS_DEFAULT` THEN the constant SHALL be imported from `arr_cli.facade.config` rather than re-declared locally.

### User Story 3

**User Story:** As an `arr-cli` contributor running the test suite, I want the four currently-frozen skip tests in `tests/unit/test_perf_budgets.py::TestLargePayloadCap` to stop skipping and pass green, so that the documented large-payload contract has an executable acceptance gate in CI instead of a permanent skip that documents a gap.

#### Acceptance Criteria

1. WHEN `transport.get` declares `max_items` in its signature THEN `_transport_get_supports_max_items()` SHALL return `True` and the `TestLargePayloadCap.setUp` skip gate SHALL fall through.
2. WHEN the four `TestLargePayloadCap` test methods run THEN each SHALL pass: `test_default_max_items_is_ten_thousand`, `test_over_cap_response_warns_and_truncates`, `test_under_cap_response_is_not_truncated`, `test_non_list_payload_is_untouched`.
3. IF the `setUp` skip gate stays in place THEN it SHALL remain untouched (it is the self-documenting contract marker) and only the underlying condition (`_transport_get_supports_max_items()` returning `True`) SHALL change.

## Design

### Approach

The change is a small, surgical extension of an existing facade entry point. `transport.get` already runs a clean request → status check → `json.loads(body_bytes)` pipeline; the truncation step slots in immediately after `json.loads` succeeds and before the value returns to the caller, so it never touches the network layer, auth injection, error mapping, or the retry wrapper. The cap default lives as a new module-level constant in `arr_cli.facade.config` next to the existing transport defaults, and `transport.py` imports it the same way it already imports `AuthConfig` / `ServiceConfig`. The truncation is implemented as a single conditional that checks `isinstance(payload, list) and len(payload) > effective_cap`, slices with `payload[:effective_cap]`, and emits one `_logger.warning(...)` call. `None` is explicitly accepted as a "use the default" sentinel inside the body so the function signature stays `int | None` while the signature default stays the integer constant (matching the test gate).

The four `TestLargePayloadCap` tests already encode the contract precisely — they patch `_ensure_session`, drive a 2xx synthetic JSON body through `get`, and assert on both the returned list and the captured stderr via `redirect_stderr`. Once the parameter exists and the truncation branch is wired, the only test-side change is replacing the locally-declared `MAX_ITEMS_DEFAULT` constant with an import from `arr_cli.facade.config`, so the constant has exactly one source of truth.

### Code Reuse

- **`arr_cli/facade/config.py` constant pattern**: `MAX_ITEMS_DEFAULT` follows the exact module-level declaration, type-annotated-with-docstring, and `__all__`-list inclusion pattern already used by `DEFAULT_CONNECT_TIMEOUT`, `DEFAULT_READ_TIMEOUT`, `DEFAULT_RETRY`, and `DEFAULT_CONFIG_PATH`.
- **`arr_cli/facade/transport.py` imports**: `transport.py` already imports `AuthConfig` and `ServiceConfig` from `arr_cli.facade.config`; the new constant joins that import block verbatim.
- **`_logger` warning pattern**: `transport.py` already has `_logger = logging.getLogger("arr_cli.facade.transport")` at the top and uses `_logger.debug(...)` in `_record_debug`; the truncation warning reuses `_logger.warning(...)` so the test's `redirect_stderr` capture works without any handler plumbing (Python's default config writes WARNING+ to stderr).
- **Keyword-only kwarg convention**: `cfg`, `connect_timeout`, `read_timeout`, and `debug` in `get` are already keyword-only (declared after `*` in the signature); `max_items` joins them so call-sites read `get("jellyfin", "/Items", cfg=cfg, max_items=5000)`.
- **`setUp` skip gate**: `TestLargePayloadCap.setUp` already calls `_transport_get_supports_max_items()`; the gate is kept verbatim because it is the self-documenting contract marker, and only the helper's return value flips from `False` to `True` once the parameter exists.

### Components and Interfaces

#### Component: `arr_cli.facade.config.MAX_ITEMS_DEFAULT`

- **Purpose:** Canonical constant for the documented large-payload cap (10 000 items).
- **Interfaces:** Module-level attribute `MAX_ITEMS_DEFAULT: int = 10_000`, exported via `__all__`.
- **Dependencies:** None.
- **Reuses:** Same declaration pattern as the existing `DEFAULT_*` transport constants in the same module.

#### Component: `arr_cli.facade.transport.get` `max_items` parameter

- **Purpose:** Per-call override of the large-payload cap; truncates oversized list responses with a single stderr warning.
- **Interfaces:** Adds keyword-only parameter `max_items: int | None = MAX_ITEMS_DEFAULT`. Internally resolves `effective_cap = max_items if max_items is not None else MAX_ITEMS_DEFAULT`. After the existing `json.loads(body_bytes)` block, applies:
  ```python
  if isinstance(payload, list) and len(payload) > effective_cap:
      upstream_count = len(payload)
      payload = payload[:effective_cap]
      _logger.warning(
          "arr_cli.facade.transport: truncated payload from %d items to %d (max_items cap)",
          upstream_count,
          effective_cap,
      )
  ```
  then returns `payload`.
- **Dependencies:** `MAX_ITEMS_DEFAULT` from `arr_cli.facade.config`, the module-level `_logger`.
- **Reuses:** Existing `json.loads(body_bytes)` block, existing `_logger` instance, existing keyword-only kwarg convention.

#### Component: `tests/unit/test_perf_budgets.py` import edit

- **Purpose:** Drop the locally-declared `MAX_ITEMS_DEFAULT` constant and import it from `arr_cli.facade.config` so the constant has one source of truth.
- **Interfaces:** Extends the existing import line `from arr_cli.facade.config import AuthConfig, ServiceConfig` to `from arr_cli.facade.config import AuthConfig, ServiceConfig, MAX_ITEMS_DEFAULT` and removes the inline `MAX_ITEMS_DEFAULT: int = 10_000` declaration.
- **Dependencies:** `arr_cli.facade.config.MAX_ITEMS_DEFAULT`.
- **Reuses:** The existing `_transport_get_supports_max_items()` helper, the existing `TestLargePayloadCap` test bodies, the existing `setUp` skip gate.

### Error Handling

1. **Scenario:** Caller passes `max_items=None` explicitly.
   - **Handling:** `effective_cap = max_items if max_items is not None else MAX_ITEMS_DEFAULT` resolves to the documented default, matching the test gate's expectation that `None` is a "use the default" sentinel.
   - **User Impact:** No behavioural change vs. omitting the kwarg; the documented cap is honoured.

2. **Scenario:** Caller passes `max_items=0` or a negative int.
   - **Handling:** Out of scope for this ticket; the contract is "cap of 10 000 items with a stderr warning on truncation", and `test_default_max_items_is_ten_thousand` only locks the *default*. An `effective_cap <= 0` would either truncate every list to zero or never truncate; both are operator-visible and not the documented contract. Future work may add a `ConfigError` validation pass; this change does not touch validation.
   - **User Impact:** None for the documented path; out-of-contract inputs are the operator's responsibility.

3. **Scenario:** Truncation warning vs. existing `_logger.debug` debug records.
   - **Handling:** The warning is emitted unconditionally on overflow; the existing `debug=True` path remains unchanged and only adds DEBUG-level records. WARNING+ writes to stderr under Python's default logging config, so `redirect_stderr` in the test captures the warning without any new handler.
   - **User Impact:** Existing `--debug` users see the same redacted request record; a new WARNING line appears on stderr whenever truncation fires.

## Tasks

- [ ] 1. Declare `MAX_ITEMS_DEFAULT` in `arr_cli/facade/config.py`
  - [ ] 1.1 Add `MAX_ITEMS_DEFAULT: int = 10_000` as a module-level constant immediately after `DEFAULT_RETRY` (or in the same transport-defaults block), with a one-line docstring matching the existing `DEFAULT_*` constant style.
  - [ ] 1.2 Add `"MAX_ITEMS_DEFAULT"` to the module's `__all__` list, in the same `DEFAULT_*` group.
  - _Requirements: US-2_

- [ ] 2. Wire the `max_items` kwarg and truncation into `arr_cli/facade/transport.py`
  - [ ] 2.1 Extend the existing `from arr_cli.facade.config import (...)` block to also import `MAX_ITEMS_DEFAULT`.
  - [ ] 2.2 Add a keyword-only `max_items: int | None = MAX_ITEMS_DEFAULT` parameter to `get`, placed after `read_timeout` and before `debug` (all four existing kwargs are keyword-only and live after the `*`).
  - [ ] 2.3 Inside `get`, immediately after the existing `return json.loads(body_bytes)` block, resolve `effective_cap = max_items if max_items is not None else MAX_ITEMS_DEFAULT`, apply the `isinstance(payload, list) and len(payload) > effective_cap` truncation branch with one `_logger.warning("arr_cli.facade.transport: truncated payload from %d items to %d (max_items cap)", upstream_count, effective_cap)`, then return `payload`.
  - _Requirements: US-1_

- [ ] 3. Update `tests/unit/test_perf_budgets.py` to import the canonical constant
  - [ ] 3.1 Change the existing import line `from arr_cli.facade.config import AuthConfig, ServiceConfig` to `from arr_cli.facade.config import AuthConfig, ServiceConfig, MAX_ITEMS_DEFAULT`.
  - [ ] 3.2 Remove the inline `MAX_ITEMS_DEFAULT: int = 10_000` declaration (the comment block above it can stay as the documented-budgets header).
  - [ ] 3.3 Leave the `setUp` skip gate and `_transport_get_supports_max_items()` helper untouched — the helper now returns `True` automatically once the parameter exists.
  - _Requirements: US-2, US-3_

- [ ] 4. Verify the four `TestLargePayloadCap` tests pass green
  - [ ] 4.1 Run `make test PYTEST_OPTS='tests/unit/test_perf_budgets.py -k TestLargePayloadCap'` and confirm all four methods (`test_default_max_items_is_ten_thousand`, `test_over_cap_response_warns_and_truncates`, `test_under_cap_response_is_not_truncated`, `test_non_list_payload_is_untouched`) pass with no skips.
  - [ ] 4.2 Run the full `make ci` chain (`make test && make secret-scan && make smoke-dry`) and confirm no other test regressed, no secrets leaked, and the smoke dry-run still passes.
  - _Requirements: US-1, US-3_

## Non-Functional Requirements

- **Performance:** Truncation is a single `len()` check plus a slice (`payload[:effective_cap]`); the existing 2-second cold-start budget and 80 MiB memory cap from `tests/unit/test_perf_budgets.py` are unaffected. No new imports, no new I/O, no new dependencies.
- **Reliability:** The truncation branch runs only after `json.loads` succeeds, so a malformed body still surfaces as `ParseError` unchanged. The `effective_cap = max_items if max_items is not None else MAX_ITEMS_DEFAULT` resolution handles `None` explicitly so a caller passing `None` (or omitting the kwarg) gets the documented default — never an unguarded `None > int` comparison.
- **Observability:** A single `_logger.warning(...)` line on overflow carries both the upstream count and the cap value, so an operator grepping stderr can see *what was cut off* and *what the cap was* without needing to correlate with `--debug` output.

## Out of Scope

- Changing the cap default from `10_000` — locked by `test_default_max_items_is_ten_thousand`.
- Validating `max_items` input (rejecting zero, negative, or non-int values) — the documented contract does not specify input validation, and the test gate only locks the default.
- Paginating via offset / next-page links — the contract is a truncation warning, not a follow-up fetch.
- Caching, memoization, or retries on truncation — the change is strictly post-parse.
- Any change to the network layer, auth injection, error mapping, or retry wrapper in `transport.get`.
- Any change to the per-service CLIs (`arr_cli/{jellyfin,radarr,sonarr,maintainerr,seerr}.py`) — the facade is the only place that talks to `requests`, so adding a transport kwarg does not require per-service edits.
- README §6 or CHANGELOG edits — both are already accurate; the change closes a documented-but-unwired gap.
- Adding a new `ConfigError` / `AuthError` / typed error class — the truncation is a soft guard, not a failure mode.