# Plan: seerr-upcoming-cmds

## Summary

Add two new `seerr upcoming-movies` and `seerr upcoming-tv` subcommands to the `arr-cli` Python package that wrap the live `GET /api/v1/discover/movies/upcoming` and `GET /api/v1/discover/tv/upcoming` endpoints on the operator's Seer instance, exposing upcoming movie releases and upcoming TV premieres respectively. This is targeted at operators (humans and shell-pipeline consumers) who already use `seerr search`, `seerr available`, and `seerr trending` and now need the same curated-summary treatment for "what's coming soon on Seer". It deliberately mirrors the existing `cmd_trending` + `_summary_seerr_trending` pair because both endpoints share the same `{page, results, totalPages, totalResults}` envelope shape and the same per-item `{title, mediaType, releaseDate, mediaInfo.tmdbId}` projection.

## Requirements

### US-1: Operator runs `seerr upcoming-movies` to fetch upcoming movie releases

**User Story:** As an arr-cli operator, I want a `seerr upcoming-movies` subcommand that defaults to listing upcoming movie releases from Seer, so that I can ask the Seer instance "what movies are coming soon" from the same shell-pipeline contract as `seerr trending` / `seerr search` / `seerr available`.

#### US-1 Acceptance Criteria

1. WHEN the operator runs `seerr upcoming-movies` with no flags THEN the CLI SHALL issue `GET /api/v1/discover/movies/upcoming` with no query parameters and emit the curated default summary to stdout (envelope `results` iterated, non-Mapping items dropped).
2. WHEN the operator runs `seerr upcoming-movies --human` THEN the CLI SHALL render the curated summary as a tabular view with one row per upcoming movie and column headers matching the summary shape keys (`title`, `mediaType`, `releaseDate`, `mediaInfo.tmdbId`).
3. WHEN the operator runs `seerr upcoming-movies --verbose` THEN the CLI SHALL bypass the summary renderer and emit the verbatim service JSON envelope to stdout.
4. WHEN the operator runs `seerr upcoming-movies --limit N` THEN the CLI SHALL cap the `--human` rendering to `N` rows and append a footer line indicating truncation (post-fetch cap, same shape as sibling list commands).
5. IF a non-2xx response is returned by the service THEN the CLI SHALL exit with code `4` (`HttpError`) and emit a structured `service=seerr op=/api/v1/discover/movies/upcoming status=<code>` line on stderr, identical to sibling seerr commands.

### US-2: Operator runs `seerr upcoming-tv` to fetch upcoming TV premieres

**User Story:** As an arr-cli operator, I want a `seerr upcoming-tv` subcommand that defaults to listing upcoming TV premieres from Seer, so that I can ask the Seer instance "what TV shows are premiering soon" from the same shell-pipeline contract as `seerr trending` / `seerr search` / `seerr available`.

#### US-2 Acceptance Criteria

1. WHEN the operator runs `seerr upcoming-tv` with no flags THEN the CLI SHALL issue `GET /api/v1/discover/tv/upcoming` with no query parameters and emit the curated default summary to stdout (envelope `results` iterated, non-Mapping items dropped).
2. WHEN the operator runs `seerr upcoming-tv --human` THEN the CLI SHALL render the curated summary as a tabular view with one row per upcoming TV item and column headers matching the summary shape keys (`title`, `mediaType`, `releaseDate`, `mediaInfo.tmdbId`).
3. WHEN the operator runs `seerr upcoming-tv --verbose` THEN the CLI SHALL bypass the summary renderer and emit the verbatim service JSON envelope to stdout.
4. WHEN the operator runs `seerr upcoming-tv --limit N` THEN the CLI SHALL cap the `--human` rendering to `N` rows and append a footer line indicating truncation (post-fetch cap, same shape as sibling list commands).
5. IF a non-2xx response is returned by the service THEN the CLI SHALL exit with code `4` (`HttpError`) and emit a structured `service=seerr op=/api/v1/discover/tv/upcoming status=<code>` line on stderr, identical to sibling seerr commands.

### US-3: Operator forwards `--page` and `--language` via the universal flag set

**User Story:** As an arr-cli operator, I want `seerr upcoming-movies --page 2 --language en` (and the same for `seerr upcoming-tv`) to forward `page` and an ISO 639-1 `language` code on the query string, so that pagination and localization work identically to the sibling detail commands (`seerr movie`, `seerr tv`, `seerr trending`).

#### US-3 Acceptance Criteria

1. WHEN the operator runs `seerr upcoming-movies --page 2` THEN the CLI SHALL forward `?page=2` on the query string alongside the implicit no-other-filter default.
2. WHEN the operator runs `seerr upcoming-tv --language en` THEN the CLI SHALL forward `?language=en` on the query string (no positional `MEDIA_TYPE` argument is accepted; the endpoint already encodes media type in the path).
3. WHEN the operator runs either command with no `--page` flag THEN the CLI SHALL omit `page` from the query string (no empty `?page=`).
4. WHEN the operator runs either command with no `--language` flag THEN the CLI SHALL omit `language` from the query string (no empty `?language=`).
5. WHEN `--page` or `--language` is set THEN the value SHALL be forwarded verbatim via the facade's `params` argument (the facade percent-encodes; the handler MUST NOT pre-encode).

### US-4: Renderer defends against envelope-wrapped and bare-list payloads

**User Story:** As an arr-cli maintainer, I want `_summary_seerr_upcoming_movies` and `_summary_seerr_upcoming_tv` to defend against both the documented envelope shape and the bare-list shape Seer may return across versions, so that future endpoint drift doesn't silently produce an empty summary, and the renderer priority chain (`--human` > `--verbose` > default summary) remains unchanged.

#### US-4 Acceptance Criteria

1. WHEN the renderer receives an envelope payload of shape `{page, results, totalPages, totalResults}` THEN it SHALL iterate `results`, drop non-Mapping items, and project each to `{title, mediaType, releaseDate, mediaInfo: {tmdbId}}`.
2. WHEN the renderer receives a bare list payload THEN it SHALL iterate the list directly with the same per-item projection (same defensive coercion as `_summary_seerr_trending`).
3. WHEN the renderer receives an envelope without a `results` key THEN it SHALL return `[]` (mirrors `_summary_seerr_trending`'s graceful default).
4. WHEN the renderer receives a non-Mapping, non-list payload THEN it SHALL return `[]` (no crash).
5. The renderer priority chain (`--human` > `--verbose` > default summary) SHALL be unchanged: `_summary_seerr_upcoming_*` is registered via `_SUMMARY_RENDERERS[("seerr", "upcoming-movies")]` / `_SUMMARY_RENDERERS[("seerr", "upcoming-tv")]`, both keyed exactly on the `_DISPATCH` command name (no aliasing).

## Design

### Approach

The two new commands are structural siblings of `cmd_trending`: all three endpoints share the `{page, totalPages, totalResults, results}` envelope, all three render with the same column set (`title`, `mediaType`, `releaseDate`, `mediaInfo.tmdbId`), and all three forward their filters as query params via the same `_get(...)` facade helper. So we mirror the existing pattern exactly — `cmd_upcoming_movies` and `cmd_upcoming_tv` are `cmd_trending` with the media-type encoded in the path (no positional `MEDIA_TYPE` argument), and the optional filters restricted to `--page` and `--language` (no `mediaType` / `timeWindow` knobs — those would be additional API surface the documented CLI does not expose). The two renderers mirror `_summary_seerr_trending` byte-for-byte because the envelope and per-item shape match exactly.

Trade-offs: separating the two commands into their own subcommands (`upcoming-movies` and `upcoming-tv`) rather than a single `upcoming [MEDIA_TYPE]` command keeps the CLI grammar flat — no positional-with-choices, no default-supplied `timeWindow`, no `mediaType` knob — which is the documented "keep it simple" surface for these endpoints. The media type is encoded in the path, so a positional `MEDIA_TYPE` would be redundant. We accept the slight verbosity (two near-identical handlers) over the alternative of a single dispatcher with an injected `media_type` because the resulting code matches the existing `cmd_search` / `cmd_trending` one-handler-per-command convention exactly.

### Code Reuse

- **`cmd_trending`** (`arr_cli/seerr.py`): Pattern for both handler bodies — call `_get(path, args, cfg, params={...}, op=<command-name>)`, set `columns` to mirror the summary shape, return `_emit(...)`. `cmd_upcoming_movies` and `cmd_upcoming_tv` are structural copies with the path encoding the media type and `params` restricted to `{page, language}` only.
- **`_summary_seerr_trending`** (`arr_cli/facade/output.py`): Pattern for both renderers — unwrap envelope (`payload.get("results")`), iterate list, drop non-Mappings, project the same key set (`title`, `mediaType`, `releaseDate`, `mediaInfo.tmdbId`). `_summary_seerr_upcoming_movies` and `_summary_seerr_upcoming_tv` reuse this exact projection because the envelope and per-item shape match.
- **`_DISPATCH` table** (`arr_cli/seerr.py`): Reused unchanged — both new commands plug in via the documented one-line-registration contract alongside `cmd_trending`.
- **`__all__` exports** (`arr_cli/seerr.py`): Reused unchanged — both new handlers and both path constants are exported so tests can assert against the literal strings (same convention as `USER_ME_PATH` and `TRENDING_PATH`).
- **`_get` and `_emit` helpers** (`arr_cli/seerr.py`): Reused unchanged — auth / timeouts / percent-encoding / output dispatch all flow through the facade helpers.
- **`universal_parents()`** (`arr_cli.facade.cli_common`): Reused unchanged — both upcoming subparsers inherit `--human`, `--verbose`, `--limit`, `--page`, `--language`, `--config`, `--debug`, `--quiet`, `--connect-timeout`, `--read-timeout`, `--retry`, `--deadline` from the same universal flag set as every other seerr command.
- **`_SUMMARY_RENDERERS` dispatch table** (`arr_cli/facade/output.py`): Reused unchanged — both new renderers are registered alongside `("seerr", "trending")` keyed on the exact `_DISPATCH` command name.
- **`_row_from_mapping`** (`arr_cli/facade/output.py`): The `--human` renderer already supports dot-path traversal (`mediaInfo.tmdbId`); no renderer change is needed for tabular output.
- **`TRENDING_PATH` and `USER_ME_PATH` constants** (`arr_cli/seerr.py`): Pattern for the path constants — module-level literals expose the exact strings for tests to assert against, and centralise future renames. `UPCOMING_MOVIES_PATH` and `UPCOMING_TV_PATH` follow the same convention.

### Components and Interfaces

#### Component 1: `cmd_upcoming_movies` handler

- **Purpose:** Build the query params (`page`, `language` only), call the facade at `UPCOMING_MOVIES_PATH`, and render the response (default summary / `--human` table / `--verbose` verbatim).
- **Interfaces:** `cmd_upcoming_movies(args: argparse.Namespace, cfg: ServiceConfig) -> int`
- **Dependencies:** `_get`, `_emit`, `transport.get` (via `_get`).
- **Reuses:** `_get` (same facade wrapper used by `cmd_trending` / `cmd_search` / `cmd_tv` / `cmd_movie`), `_emit`, the `columns` list shape from `cmd_trending`.

#### Component 2: `cmd_upcoming_tv` handler

- **Purpose:** Build the query params (`page`, `language` only), call the facade at `UPCOMING_TV_PATH`, and render the response (default summary / `--human` table / `--verbose` verbatim).
- **Interfaces:** `cmd_upcoming_tv(args: argparse.Namespace, cfg: ServiceConfig) -> int`
- **Dependencies:** `_get`, `_emit`, `transport.get` (via `_get`).
- **Reuses:** `_get` (same facade wrapper used by `cmd_trending` / `cmd_search` / `cmd_tv` / `cmd_movie`), `_emit`, the `columns` list shape from `cmd_trending`.

#### Component 3: `_summary_seerr_upcoming_movies` renderer

- **Purpose:** Map the verbatim Seer payload (envelope or bare list) to the curated summary shape used by the default `--human` rendering for `seerr upcoming-movies`.
- **Interfaces:** `_summary_seerr_upcoming_movies(payload: Any) -> list[dict[str, Any]]` (registered via `_SUMMARY_RENDERERS[("seerr", "upcoming-movies")]`).
- **Dependencies:** `_safe_get` (already used by every Seerr summary renderer).
- **Reuses:** `_summary_seerr_trending`'s defensive envelope-unwrap + per-item projection pattern verbatim; the only difference is the registered key.

#### Component 4: `_summary_seerr_upcoming_tv` renderer

- **Purpose:** Map the verbatim Seer payload (envelope or bare list) to the curated summary shape used by the default `--human` rendering for `seerr upcoming-tv`.
- **Interfaces:** `_summary_seerr_upcoming_tv(payload: Any) -> list[dict[str, Any]]` (registered via `_SUMMARY_RENDERERS[("seerr", "upcoming-tv")]`).
- **Dependencies:** `_safe_get` (already used by every Seerr summary renderer).
- **Reuses:** `_summary_seerr_trending`'s defensive envelope-unwrap + per-item projection pattern verbatim; the only difference is the registered key.

#### Component 5: `UPCOMING_MOVIES_PATH` constant

- **Purpose:** Single source of truth for the exact endpoint path string; exposed via `__all__` so tests can assert against the literal.
- **Interfaces:** `UPCOMING_MOVIES_PATH: str = "/api/v1/discover/movies/upcoming"`
- **Dependencies:** None.
- **Reuses:** `TRENDING_PATH` / `USER_ME_PATH` convention (one module-level literal per endpoint, exported for test assertion).

#### Component 6: `UPCOMING_TV_PATH` constant

- **Purpose:** Single source of truth for the exact endpoint path string; exposed via `__all__` so tests can assert against the literal.
- **Interfaces:** `UPCOMING_TV_PATH: str = "/api/v1/discover/tv/upcoming"`
- **Dependencies:** None.
- **Reuses:** `TRENDING_PATH` / `USER_ME_PATH` convention (one module-level literal per endpoint, exported for test assertion).

#### Component 7: `upcoming-movies` and `upcoming-tv` subparsers

- **Purpose:** Wire the two new commands into the existing `seerr` argparse tree and the `_DISPATCH` table.
- **Interfaces:** Both subparsers added to `build_seerr_parser()` after the `trending` subparser; registered as `"upcoming-movies": cmd_upcoming_movies` and `"upcoming-tv": cmd_upcoming_tv` in `_DISPATCH`. Both subparsers use `parents=universal_parents()` and `add_help=False` (same convention as every sibling subparser).
- **Dependencies:** `universal_parents()` (already imported from `arr_cli.facade.cli_common`).
- **Reuses:** The subparser + dispatch pattern from `cmd_trending` / `cmd_search` / `cmd_available` / `cmd_tv` / `cmd_movie`.

### Data Models _(no new data shape — envelope shape is documented in the spec)_

Both endpoints return the standard Seer paginated envelope:

```
{
  page: int,
  results: list<UpcomingItem>,
  totalPages: int,
  totalResults: int
}
```

where each `UpcomingItem` carries at minimum:

- `id: int` — TMDB id (forwarded through `mediaInfo.tmdbId` in the summary)
- `title: str | null`
- `mediaType: "movie" | "tv" | null`
- `releaseDate: str | null`
- `mediaInfo: { tmdbId: int, ... }`

The summary renderer projects each item to `{title, mediaType, releaseDate, mediaInfo: {tmdbId}}` — the same projection as `_summary_seerr_trending`.

### Error Handling

The facade already maps non-2xx responses to `HttpError(exit_code=4)`, which `main_wrapper` surfaces as a structured `service=seerr op=/api/v1/discover/movies/upcoming status=<code>` (or `op=/api/v1/discover/tv/upcoming status=<code>`) stderr line. Neither handler needs to add any try/except — the existing facade contract applies unchanged.

The universal flag set (`--page`, `--language`) is inherited from `universal_parents()` and validated by argparse at parse time; invalid values raise `SystemExit(2)` via argparse's `error()` call before any HTTP request is issued. This is the documented graceful failure path for sibling optional-flag args (mirrors the malformed-date path on `radarr calendar`).

The summary renderers defend against three shape variants (envelope, bare list, neither) and never raise on an unexpected payload — the same graceful-default contract every other `_summary_seerr_*` function ships with.

## Tasks

Each task touches 1-3 related files. Reference user stories via `_Requirements: US-X, US-Y_`.

- [x] 1. Add `cmd_upcoming_movies` + `cmd_upcoming_tv` handlers, path constants, subparsers, and dispatch wiring in `arr_cli/seerr.py`
  - [x] 1.1 Define `UPCOMING_MOVIES_PATH = "/api/v1/discover/movies/upcoming"` and `UPCOMING_TV_PATH = "/api/v1/discover/tv/upcoming"` module-level constants
    - Add both to `__all__` (so tests can assert against the literals — same convention as `USER_ME_PATH` and `TRENDING_PATH`)
    - Place alongside `TRENDING_PATH` and update the module docstring's command count bullet to include the two new commands
    - _Requirements: US-1, US-2_
  - [x] 1.2 Implement `cmd_upcoming_movies(args, cfg)` mirroring `cmd_trending`'s handler body
    - Build `params: dict[str, Any]` with `page` only when `args.page is not None`, `language` only when `args.language is not None` (no `timeWindow`, no `mediaType` — those knobs are not part of the documented CLI surface for these endpoints)
    - Call `_get(UPCOMING_MOVIES_PATH, args, cfg, params=params, op="upcoming-movies")`
    - Set `columns = ["title", "mediaType", "releaseDate", "mediaInfo.tmdbId"]` to match the summary shape keys emitted by `_summary_seerr_upcoming_movies` (same literal as `cmd_trending` — sibling pattern)
    - Return `_emit(payload, args, columns=columns)`
    - Add `cmd_upcoming_movies` to `__all__`
    - _Requirements: US-1, US-3_
  - [x] 1.3 Implement `cmd_upcoming_tv(args, cfg)` mirroring `cmd_trending`'s handler body
    - Build `params: dict[str, Any]` with `page` only when `args.page is not None`, `language` only when `args.language is not None`
    - Call `_get(UPCOMING_TV_PATH, args, cfg, params=params, op="upcoming-tv")`
    - Set `columns = ["title", "mediaType", "releaseDate", "mediaInfo.tmdbId"]` to match the summary shape keys emitted by `_summary_seerr_upcoming_tv` (same literal as `cmd_trending` — sibling pattern)
    - Return `_emit(payload, args, columns=columns)`
    - Add `cmd_upcoming_tv` to `__all__`
    - _Requirements: US-2, US-3_
  - [x] 1.4 Register `upcoming-movies` and `upcoming-tv` subparsers in `build_seerr_parser()` immediately after the `trending` subparser
    - `parents=universal_parents()`, `add_help=False` (same convention as every sibling subparser)
    - No positional args (the media type is encoded in the path; no `MEDIA_TYPE` is accepted)
    - Both subparsers rely on `--page` and `--language` from `universal_parents()` for their optional filters
    - Update the `build_seerr_parser` description string to mention the two new commands and bump the command count from "eight" → "ten"
    - _Requirements: US-1, US-2, US-3_
  - [x] 1.5 Register `"upcoming-movies": cmd_upcoming_movies` and `"upcoming-tv": cmd_upcoming_tv` in `_DISPATCH` (after the `"trending"` entry)
    - _Requirements: US-1, US-2_

- [x] 2. Add `_summary_seerr_upcoming_movies` + `_summary_seerr_upcoming_tv` renderers and register in `_SUMMARY_RENDERERS` in `arr_cli/facade/output.py`
  - [x] 2.1 Define `_summary_seerr_upcoming_movies(payload)` mirroring `_summary_seerr_trending` verbatim
    - Defensive envelope-unwrap: `if isinstance(payload, Mapping): payload = payload.get("results")`
    - Guard `if not isinstance(payload, list): return []`
    - Iterate list, drop non-Mapping items, project each to `{title, mediaType, releaseDate, mediaInfo: {tmdbId}}` using `_safe_get`
    - `_logger` / `_safe_get` already imported at module scope — no new imports
    - _Requirements: US-1, US-4_
  - [x] 2.2 Define `_summary_seerr_upcoming_tv(payload)` mirroring `_summary_seerr_trending` verbatim
    - Defensive envelope-unwrap: `if isinstance(payload, Mapping): payload = payload.get("results")`
    - Guard `if not isinstance(payload, list): return []`
    - Iterate list, drop non-Mapping items, project each to `{title, mediaType, releaseDate, mediaInfo: {tmdbId}}` using `_safe_get`
    - `_logger` / `_safe_get` already imported at module scope — no new imports
    - _Requirements: US-2, US-4_
  - [x] 2.3 Register `("seerr", "upcoming-movies"): _summary_seerr_upcoming_movies` and `("seerr", "upcoming-tv"): _summary_seerr_upcoming_tv` in `_SUMMARY_RENDERERS` (after the `("seerr", "trending")` entry)
    - Both keys MUST match the exact `_DISPATCH` command name (no aliasing) so `output.emit`'s dispatch finds the renderer
    - _Requirements: US-1, US-2, US-4_

- [x] 3. Add `TestCmdUpcomingMovies` + `TestCmdUpcomingTv` regression classes to `tests/unit/test_seerr.py` (mirror `TestCmdTrending`)
  - [x] 3.1 Test dispatch-table registration for both commands
    - Assert `"upcoming-movies"` and `"upcoming-tv"` are keys in `_DISPATCH` and each maps to a callable handler
    - Assert `cmd_upcoming_movies`, `cmd_upcoming_tv`, `UPCOMING_MOVIES_PATH`, and `UPCOMING_TV_PATH` are exported via `__all__`
    - _Requirements: US-1, US-2_
  - [x] 3.2 Test `seerr upcoming-movies` hits `/api/v1/discover/movies/upcoming` with no default query parameters
    - Use `responses.RequestsMock` to mock `GET https://seerr.example/api/v1/discover/movies/upcoming`
    - Match via `match_querystring=False` (the default invocation has no query params — `match_querystring=True` would reject an empty query string) or via `query_param_matcher({})`
    - Invoke `seerr.main(["--config", cfg_path, "upcoming-movies"])`, assert exit code 0 and the mock fired
    - _Requirements: US-1_
  - [x] 3.3 Test `seerr upcoming-tv` hits `/api/v1/discover/tv/upcoming` with no default query parameters
    - Use `responses.RequestsMock` to mock `GET https://seerr.example/api/v1/discover/tv/upcoming`
    - Match via `query_param_matcher({})`
    - Invoke `seerr.main(["--config", cfg_path, "upcoming-tv"])`, assert exit code 0 and the mock fired
    - _Requirements: US-2_
  - [x] 3.4 Test `--page` query-param pass-through for both commands
    - `seerr upcoming-movies --page 2` → `page=2` on the wire
    - `seerr upcoming-tv --page 5` → `page=5` on the wire
    - `seerr upcoming-movies` (no `--page`) → no `page` key on the wire (no empty `?page=`)
    - `seerr upcoming-tv` (no `--page`) → no `page` key on the wire
    - _Requirements: US-3_
  - [x] 3.5 Test `--language` query-param pass-through for both commands
    - `seerr upcoming-movies --language en` → `language=en` on the wire
    - `seerr upcoming-tv --language de` → `language=de` on the wire
    - `seerr upcoming-movies` (no `--language`) → no `language` key on the wire (no empty `?language=`)
    - `seerr upcoming-tv` (no `--language`) → no `language` key on the wire
    - _Requirements: US-3_
  - [x] 3.6 Test `--human` tabular output for both commands
    - Build an `argparse.Namespace` with `human=True` and an envelope fixture, invoke `cmd_upcoming_movies` / `cmd_upcoming_tv`
    - Assert the rendered stdout contains the column header line and one row per `results` entry
    - Assert the renderer used the `_summary_seerr_upcoming_*` shape (i.e. the keys projected by the renderer are present as headers)
    - _Requirements: US-1, US-2_
  - [x] 3.7 Test `--verbose` verbatim passthrough for both commands
    - Build an `argparse.Namespace` with `verbose=True` and an envelope fixture, invoke `cmd_upcoming_movies` / `cmd_upcoming_tv`
    - Assert the rendered stdout is `json.loads`-equal to the fixture (the envelope is preserved verbatim)
    - _Requirements: US-1, US-2_
  - [x] 3.8 Test both renderers' defensive envelope-unwrap (shared pattern across both `_summary_seerr_upcoming_movies` and `_summary_seerr_upcoming_tv`)
    - Direct call with envelope fixture (`{page, results, totalPages, totalResults}`) → returns list of curated summaries, one per `results` entry
    - Direct call with bare list fixture → returns list of curated summaries
    - Direct call with envelope missing the `results` key → returns `[]`
    - Direct call with non-Mapping non-list payload → returns `[]`
    - Direct call with envelope containing a non-Mapping item inside `results` → that item is dropped
    - _Requirements: US-4_
  - [x] 3.9 Test `--limit` post-fetch cap for both commands
    - Build an envelope fixture with N results, invoke `cmd_upcoming_movies` / `cmd_upcoming_tv` with `--human --limit M` (M < N)
    - Assert the rendered table contains M rows and a footer line indicating truncation
    - _Requirements: US-1, US-2_

- [x] 4. Extend `tests/unit/test_output.py` per-service fixture map with the two new `(svc, cmd)` entries for upcoming-movies and upcoming-tv
  - [x] 4.1 Add `("seerr", "upcoming-movies")` and `("seerr", "upcoming-tv")` entries to `_synthetic_payload` (or equivalent fixture map) so the existing `TestColumnsBlockMatchesSummaryShape` regression iterates both new renderers without `KeyError`
    - The columns-block → summary-shape alignment regression iterates every `_SUMMARY_RENDERERS` key and calls `_synthetic_payload(svc, cmd)`; without these entries the helper raises `KeyError` and `make ci` fails (same deviation precedent documented in the `seerr-trending-cmd` plan)
    - _Requirements: US-4_

- [ ] 5. Update `README.md` §4.5 Seer command table and `CHANGELOG.md` `[Unreleased]` section
  - [ ] 5.1 Bump the `seerr — 8 commands` section header in `README.md` §4.5 → `seerr — 10 commands`
    - _Requirements: US-1, US-2_
  - [ ] 5.2 Append a `seerr upcoming-movies` row to the table in `README.md` §4.5 immediately after the `seerr trending` row
    - Command: `` `seerr upcoming-movies` ``
    - Path: `` `/api/v1/discover/movies/upcoming?page=<…>&language=<…>` ``
    - Notes: `Accepts optional --page and --language.`
    - _Requirements: US-1, US-3_
  - [ ] 5.3 Append a `seerr upcoming-tv` row to the table in `README.md` §4.5 immediately after the `seerr upcoming-movies` row
    - Command: `` `seerr upcoming-tv` ``
    - Path: `` `/api/v1/discover/tv/upcoming?page=<…>&language=<…>` ``
    - Notes: `Accepts optional --page and --language.`
    - _Requirements: US-2, US-3_
  - [ ] 5.4 Add two entries under `[Unreleased]` → `Added` in `CHANGELOG.md`
    - `` - `seerr upcoming-movies` — list upcoming movie releases via `GET /api/v1/discover/movies/upcoming`. ``
    - `` - `seerr upcoming-tv` — list upcoming TV premieres via `GET /api/v1/discover/tv/upcoming`. ``
    - _Requirements: US-1, US-2_

## Non-Functional Requirements

- **Hermetic tests:** All `TestCmdUpcomingMovies` and `TestCmdUpcomingTv` cases run under `tests/unit/` and use `responses` for HTTP mocking — no live network. AGENTS.md §7.5 explicitly forbids live HTTP in the unit suite.
- **Auth + secret hygiene:** The handlers MUST NOT inspect or echo the credential. Auth header injection stays inside `transport._inject_auth`; these handlers thread only the path + params via `_get`. No new `arr.conf.example` entries needed.
- **Code reuse over new code:** Both handlers and both renderers are near-verbatim copies of `cmd_trending` / `_summary_seerr_trending` because the envelopes and per-item shapes match — no new abstractions are warranted (a shared helper that takes `path` + `media_type` would obscure the one-handler-per-command convention).
- **Pipe-clean stdout:** `cmd_upcoming_movies` and `cmd_upcoming_tv` emit exactly one JSON document (default summary) or one table (`--human`) or one verbatim envelope (`--verbose`) to stdout; all diagnostics flow through stderr via the facade. No intermixing.
- **Renderer priority chain unchanged:** `_summary_seerr_upcoming_movies` and `_summary_seerr_upcoming_tv` plug into `_SUMMARY_RENDERERS` keyed on the exact `_DISPATCH` command name; the existing `output.emit` dispatch chain (`--human` > `--verbose` > default summary) is unchanged.

## Out of Scope

- **Sort/filter knobs beyond the documented CLI surface** (`--sort`, `--genre`, `--region`, etc.). Per the "keep it simple" rule, only `--page` and `--language` are exposed; any additional API query params the Seer endpoints accept are out of scope for this plan.
- **Pagination wrapper / cursor iteration** beyond single-page fetch via `--page`. The CLI does not loop pages or expose a `nextPage` cursor — the operator scripts that themselves if they need multi-page traversal.
- **New runtime dependencies.** `requests`, `PyYAML`, `pytest`, `responses` are already vendored; the two new commands add zero new imports beyond what `cmd_trending` / `cmd_search` / `cmd_available` already use (`argparse`, `argparse.Namespace`, the facade helpers).
- **New per-service HTTP/auth code outside the facade.** Both handlers delegate to `_get` (which delegates to `transport.get` + `transport._inject_auth`); no new HTTP plumbing is introduced.
- **A unified `seerr upcoming [MEDIA_TYPE]` command with a positional media-type argument.** The two endpoints have distinct paths (`/api/v1/discover/movies/upcoming` vs `/api/v1/discover/tv/upcoming`) and no shared filters beyond `--page` / `--language`; merging them into one subcommand with a positional `MEDIA_TYPE` would obscure the one-handler-per-command convention and add a `choices=` validation knob for no CLI surface benefit. Two flat subcommands (`upcoming-movies`, `upcoming-tv`) match the existing convention.
- **Adding `seerr create-request` or any other write endpoint.** AGENTS.md §1 / §4.2 / README §8 explicitly forbid write endpoints in MVP.
- **Live integration tests under `tests/integration/`.** Not requested for this change; the existing `make ci` chain does not invoke `pytest --run-integration`.
- **Touching unrelated per-service CLIs** (`jellyfin`, `radarr`, `sonarr`, `maintainerr`). The diff is scoped to `arr_cli/seerr.py`, `arr_cli/facade/output.py`, `tests/unit/test_seerr.py`, `tests/unit/test_output.py`, `README.md`, and `CHANGELOG.md`.
- **Changing the `_DISPATCH` registration mechanism or the `_SUMMARY_RENDERERS` dispatch shape.** The two new commands plug into existing tables via the documented one-line-registration contract.
- **Maintainingerr rules endpoint, Seer write endpoints, webhook receivers, daemon / persistent cache layer.** Out of MVP entirely per AGENTS.md / README §8.
