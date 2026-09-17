# Plan: seerr-discover-cmds

## Summary

Add two new Seerr commands, `discover-movies` and `discover-tv`, that wrap the live-verified `GET /api/v1/discover/movies` and `GET /api/v1/discover/tv` endpoints and expose a focused filter set (`--genre`, `--sort`, `--language`, plus universal `--page`/`--limit`). This ships the "explore with filters" counterpart to the recently-landed `seerr trending` / `upcoming-movies` / `upcoming-tv` commands, reusing their paginated envelope shape, summary renderer, and CLI grammar so the diff stays small and the regression surface stays covered.

## Requirements

### US-1: discover-movies command

**User Story:** As a Seerr operator, I want a `seerr discover-movies` command that returns a paginated list of movies from the live `GET /api/v1/discover/movies` endpoint, so that I can browse movies programmatically with a stable, pipe-clean JSON output.

#### US-1 Acceptance Criteria

1. WHEN the operator runs `seerr discover-movies` (no flags) THEN the system SHALL issue `GET /api/v1/discover/movies` with `sortBy=popularity.desc`, `language=en-US`, `page=1`, returning the verbatim paginated envelope `{page, results, totalPages, totalResults}`. The row-count cap (default 20) is applied client-side per US-3 AC5; no `limit` query parameter is sent on the wire.
2. WHEN the operator passes `--genre <id>` THEN the system SHALL include `genre=<id>` in the query string, percent-encoded through the facade, and SHALL reject a non-integer genre id at parse time with exit code `1`.
3. WHEN the operator passes `--sort <sortBy>` THEN the system SHALL include `sortBy=<sortBy>` in the query string, percent-encoded, and SHALL pass the value through unchanged (no client-side allow-list in this ship).
4. WHEN the operator passes `--language <code>` THEN the system SHALL include `language=<code>` in the query string, percent-encoded, defaulting to `en-US` when omitted.
5. WHEN the operator passes `--page <n>` or `--limit <n>` THEN the system SHALL apply the universal flag semantics already defined for sibling discover commands.
6. IF the upstream returns a non-2xx status THEN the system SHALL propagate the facade's typed error so `main_wrapper` emits the documented `service=seerr op=discover-movies` line on stderr with the correct stable exit code.
7. WHEN the operator passes `--human` THEN the system SHALL render the response via the registered `_summary_seerr_discover_movies` renderer and present a tabular view that mirrors the `upcoming-movies` columns.

### US-2: discover-tv command

**User Story:** As a Seerr operator, I want a `seerr discover-tv` command that returns a paginated list of TV series from the live `GET /api/v1/discover/tv` endpoint, so that I can browse TV programmatically with the same shape as `discover-movies`.

#### US-2 Acceptance Criteria

1. WHEN the operator runs `seerr discover-tv` (no flags) THEN the system SHALL issue `GET /api/v1/discover/tv` with `sortBy=popularity.desc`, `language=en-US`, `page=1`, returning the verbatim paginated envelope. The row-count cap (default 20) is applied client-side per US-3 AC5; no `limit` query parameter is sent on the wire.
2. WHEN the operator passes `--genre <id>`, `--sort <sortBy>`, `--language <code>`, `--page <n>`, or `--limit <n>` THEN the system SHALL apply the same semantics as `discover-movies`, with `genre=<id>` percent-encoded.
3. IF the upstream returns a non-2xx status THEN the system SHALL propagate the facade's typed error with `service=seerr op=discover-tv` on stderr.
4. WHEN the operator passes `--human` THEN the system SHALL render via the registered `_summary_seerr_discover_tv` renderer with a tabular view structurally identical to `_summary_seerr_discover_movies`.

### US-3: filter and sort support (genre, sortBy, language, page, limit)

**User Story:** As a Seerr operator, I want the new discover commands to accept the documented filter set, so that I can narrow results by genre, sort order, and language without learning per-endpoint flag names.

#### US-3 Acceptance Criteria

1. WHEN the operator passes `--genre <id>` THEN the system SHALL percent-encode the value and forward it as `genre=<id>` on the underlying GET request.
2. WHEN the operator passes `--sort <sortBy>` THEN the system SHALL percent-encode the value and forward it as `sortBy=<sortBy>`; the default when `--sort` is omitted SHALL be `popularity.desc`.
3. WHEN the operator passes `--language <code>` THEN the system SHALL percent-encode the value and forward it as `language=<code>`; the default when `--language` is omitted SHALL be `en-US`.
4. WHEN the operator passes `--page <n>` THEN the system SHALL forward `page=<n>`; the default SHALL be `1`.
5. WHEN the operator passes `--limit <n>` THEN the system SHALL cap client-side row output to `n` rows; the default SHALL be `20`. The underlying endpoint is not asked for a `limit` query parameter in this ship (the discover endpoints page instead).
6. IF the operator omits every flag THEN the system SHALL issue the request with the defaults listed above.
7. IF the operator passes an unparseable value for `--genre`, `--sort`, `--language`, `--page`, or `--limit` THEN the system SHALL exit with code `1` and a single stderr line naming the offending flag.

## Design

### Approach

We extend the existing `seerr` CLI by adding two new subcommands that are structural twins of `cmd_upcoming_movies` and `cmd_upcoming_tv`, because the discover endpoints return the same paginated envelope shape `{page, results, totalPages, totalResults}` that those commands already consume. The CLI grammar mirrors `upcoming-movies`/`upcoming-tv` so operators already familiar with those commands get immediate muscle memory. HTTP goes through `arr_cli.facade.transport.get` (no new transport code), authentication comes from the existing `X-Api-Key` injection, and user-supplied query fragments are percent-encoded through the facade's helpers. Each new command registers a `_summary_seerr_discover_movies` / `_summary_seerr_discover_tv` renderer in `_SUMMARY_RENDERERS` so `--human` reuses the existing tabular output pipeline without adding new helpers.

The trade-off is that we intentionally do not expose every discover query parameter the upstream API supports (e.g. `primaryReleaseDateGte`, `keywords`, `withNetworks`); the documented CLI surface stays small and the filter set maps 1:1 to the spec's most-used knobs. Future PRs can layer on additional filters without changing the existing flags.

Endpoint paths and methods are verified against the live operator `/api-docs/swagger-ui-init.js` OpenAPI spec.

### Code Reuse

- **`cmd_upcoming_movies` / `cmd_upcoming_tv`** (`arr_cli/seerr.py`): structural twin for both new commands — same envelope handling, same renderer dispatch, same `--page` / `--language` flag pattern.
- **`_summary_seerr_upcoming_movies`** (`arr_cli/facade/output.py`): the new movie/TV discover summary renderers reuse its column shape; the new entries are byte-near-identical to keep the renderer table consistent.
- **`build_parser` / `main_wrapper` / `universal_parents`** (`arr_cli/facade/cli_common.py`): already provides `--config`, `--debug`, `--human`/`-h`, `--limit`, and the five-stable-exit-code translation. The new subcommands are added through the same `subparsers.add_parser(...)` flow that `upcoming-movies` uses.
- **`arr_cli.facade.transport.get`** (`arr_cli/facade/transport.py`): the single HTTP entry point. No new facade helpers; query parameters are passed via the existing `params=` kwarg, which the facade already percent-encodes.
- **`_SUMMARY_RENDERERS`** (`arr_cli/facade/output.py`): the single registration point for new summary renderers; both new renderers are registered here.

### Components and Interfaces

#### Component 1: `cmd_discover_movies` (in `arr_cli/seerr.py`)

- **Purpose:** Thin wrapper that builds the `GET /api/v1/discover/movies` query, calls the facade, and dispatches to the registered summary renderer or verbatim JSON output.
- **Interfaces:** `def cmd_discover_movies(args: argparse.Namespace, cfg: ServiceConfig) -> int`
- **Dependencies:** `argparse.Namespace` populated with `genre` (optional `int`), `sort` (optional `str`, default `popularity.desc`), `language` (optional `str`, default `en-US`), `page` (`int`, default `1`), `limit` (`int`, default `20`), plus `human` from the universal parents.
- **Reuses:** `cmd_upcoming_movies` (path/params construction, error propagation, return code); `_summary_seerr_discover_movies` (renderer dispatch via `arr_cli.facade.output.emit`).

#### Component 2: `cmd_discover_tv` (in `arr_cli/seerr.py`)

- **Purpose:** Structural twin of `cmd_discover_movies` for the `GET /api/v1/discover/tv` endpoint.
- **Interfaces:** `def cmd_discover_tv(args: argparse.Namespace, cfg: ServiceConfig) -> int`
- **Dependencies:** Same `argparse.Namespace` shape as Component 1.
- **Reuses:** `cmd_upcoming_tv` (envelope handling, renderer dispatch); `_summary_seerr_discover_tv`.

#### Component 3: `_summary_seerr_discover_movies` and `_summary_seerr_discover_tv` (in `arr_cli/facade/output.py`)

- **Purpose:** Map the verbatim discover envelope to the curated per-command summary shape used by `summarize` for `--human` rendering.
- **Interfaces:** Two new entries in `_SUMMARY_RENDERERS` (tuple-keyed `(service, command)`): `("seerr", "discover-movies")` → `_summary_seerr_discover_movies`, `("seerr", "discover-tv")` → `_summary_seerr_discover_tv`. Each renderer takes the verbatim JSON payload and returns the curated dict/list shape.
- **Dependencies:** The summary helpers already used by `_summary_seerr_upcoming_movies` / `_summary_seerr_upcoming_tv`.
- **Reuses:** Existing renderer pattern (`_summary_seerr_*` family in `arr_cli/facade/output.py`).

#### Component 4: Parser wiring in `build_parser()` (in `arr_cli/seerr.py`)

- **Purpose:** Register `discover-movies` and `discover-tv` subcommands, attach `--genre`, `--sort`, `--language`, plus the universal `--page` / `--limit` flags inherited from `universal_parents`.
- **Interfaces:** Two new `subparsers.add_parser(...)` calls inside `build_parser()`; two new entries in the `_DISPATCH` dispatch dict (`"discover-movies": cmd_discover_movies`, `"discover-tv": cmd_discover_tv`).
- **Dependencies:** `argparse`, `universal_parents`.
- **Reuses:** Existing `upcoming_movies` / `upcoming_tv` parser wiring as the template.

## Tasks

Each task touches 1-3 related files. Reference user stories via `_Requirements: US-X, US-Y_`.

- [x] 1. Add discover-movies and discover-tv subcommands in `arr_cli/seerr.py`
  - [x] 1.1 Implement `cmd_discover_movies(args, cfg) -> int`
    - Build the `/api/v1/discover/movies` path; assemble `params` from `genre`, `sortBy`, `language`, `page` with the documented defaults; call `arr_cli.facade.transport.get`; hand off to `arr_cli.facade.output.emit`; return `0`.
    - _Requirements: US-1.1, US-1.2, US-1.3, US-1.4, US-1.5, US-1.6, US-1.7_
  - [x] 1.2 Implement `cmd_discover_tv(args, cfg) -> int`
    - Structural twin of `cmd_discover_movies` against `/api/v1/discover/tv`.
    - _Requirements: US-2.1, US-2.2, US-2.3, US-2.4_
  - [x] 1.3 Wire the subparsers and dispatch dict in `build_parser()`
    - Add `--genre` (int), `--sort` (str), `--language` (str) plus the universal `--page`/`--limit`; register both subcommands in the `_DISPATCH` dict.
    - _Requirements: US-3.1, US-3.2, US-3.3, US-3.4, US-3.5, US-3.6, US-3.7_

- [ ] 2. Add summary renderers in `arr_cli/facade/output.py` and register in `_SUMMARY_RENDERERS`
  - [ ] 2.1 Implement `_summary_seerr_discover_movies(payload) -> curated`
    - Reuse the column shape from `_summary_seerr_upcoming_movies`; emit `{page, results, totalPages, totalResults}` plus the curated per-row fields.
    - _Requirements: US-1.7_
  - [ ] 2.2 Implement `_summary_seerr_discover_tv(payload) -> curated`
    - Structural twin of `_summary_seerr_discover_movies` for the TV envelope.
    - _Requirements: US-2.4_
  - [ ] 2.3 Register both renderers in `_SUMMARY_RENDERERS`
    - Add keys `("seerr", "discover-movies")` and `("seerr", "discover-tv")` pointing to the new callables (matching the existing tuple-keyed shape used by `("seerr", "upcoming-movies")` and `("seerr", "upcoming-tv")`).
    - _Requirements: US-1.7, US-2.4_

- [ ] 3. Add regression tests in `tests/unit/test_seerr.py`
  - [ ] 3.1 Add `TestCmdDiscoverMovies` covering default request, each flag, percent-encoding, and non-2xx propagation
    - Mock `GET /api/v1/discover/movies` with `responses`; assert default params, that `--genre 28` issues `genre=28`, that `--sort popularity.asc` issues `sortBy=popularity.asc`, that `--language fr-FR` issues `language=fr-FR`, and that a 500 propagates as `HttpError`.
    - _Requirements: US-1.1, US-1.2, US-1.3, US-1.4, US-1.5, US-1.6, US-3.1, US-3.2, US-3.3, US-3.4, US-3.5, US-3.6, US-3.7_
  - [ ] 3.2 Add `TestCmdDiscoverTv` mirroring the movie coverage
    - _Requirements: US-2.1, US-2.2, US-2.3, US-3.1, US-3.2, US-3.3, US-3.4, US-3.5, US-3.6, US-3.7_
  - [ ] 3.3 Add `--human` renderer tests for both new renderers
    - Drive `arr_cli.facade.output.summarize` with a synthetic discover payload and assert the curated shape for `seerr:discover-movies` / `seerr:discover-tv`.
    - _Requirements: US-1.7, US-2.4_

- [ ] 4. Update `README.md` seerr command table and `CHANGELOG.md` `[Unreleased] → Added`
  - [ ] 4.1 Add rows to the `seerr` command table in `README.md`
    - Two new rows documenting `discover-movies` and `discover-tv`, including their flags.
    - _Requirements: US-1, US-2, US-3_
  - [ ] 4.2 Add a `CHANGELOG.md` entry under `[Unreleased] → Added`
    - Short bullet naming both new commands and the filter flags.
    - _Requirements: US-1, US-2, US-3_

## Non-Functional Requirements _(include only if material)_

- **Usability:** New commands must reuse the existing renderer/parser machinery so the operator's `--human` / `--verbose` / default behavior is identical to sibling discover commands. No new flag names beyond `--genre`, `--sort`, `--language`, `--page`, `--limit`.

## Out of Scope

- `--studio` / `--network` flags — no list endpoint exists for studios/networks in the live OpenAPI spec, and adding these would require hardcoding values; deferred.
- `primaryReleaseDateGte` / `firstAirDateGte` parameters — the discover endpoints accept these, but they are not part of the documented CLI surface in this ship; deferred.
- `keywords` / `excludeKeywords` parameters — adds significant parser surface and validation work; deferred.
- New runtime dependencies — `requests` and `PyYAML` are already the only runtime deps; no additions.
- New exit codes — the existing five stable exit codes cover every error path introduced here.
