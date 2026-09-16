# Changelog

All notable changes to `arr-cli` are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
within the pre-1.0 contract documented in `README.md`.

## [Unreleased]

### Removed

- `seerr media <tmdbId>` -- the command has been removed because
  Seer does not expose a GET-by-tmdbId for media details. Seer's
  `/media/{id}` uses the internal numeric `mediaId` (not the
  external `tmdbId`) and only exposes `POST /media/{id}/{status}`,
  `DELETE /media/{id}`, `DELETE /media/{id}/file`, and
  `GET /media/{id}/watch_data`; the historical Overseerr path
  `/api/v1/media/{tmdbId}` was inherited from pre-fork
  documentation and was never reconciled against the live Seer
  `/api-docs/swagger-ui-init.js` OpenAPI spec (AGENTS.md §1 "Seer
  note" guard paragraph), so the operator was getting `HTTP 405`
  against a Seer instance. Callers that need media info can run
  `seerr search <query> --verbose` instead; the search endpoint
  returns the same tmdbId-keyed media shape. The command-table
  row in `README.md` §4.5, the per-command counts in
  `README.md` intro and `AGENTS.md` §1, the `cmd_media`
  unit-test registration in `tests/unit/test_seerr.py`, and
  the `seerr media` missing-positional case in
  `tests/unit/test_argparse_universal_flags.py` move with the
  removal so future re-introduction of this exact shape fails
  the unit suite immediately.

### Documentation

- Clarified that the `seerr` CLI targets [Seer](https://github.com/seerr-team/seerr),
  the unified Overseerr + Jellyseerr fork, across `README.md`, `AGENTS.md`,
  `CHANGELOG.md`, and `arr.conf.example`. Added a guard paragraph in
  `AGENTS.md` §1 and a `README.md` §4.5 upstream note instructing future
  contributors to verify `seerr` endpoint paths and methods against the live
  Seer instance's `/api-docs/swagger-ui-init.js` OpenAPI spec rather than
  against the historical Overseerr or Jellyseerr documentation; both
  historical sources diverged from Seer on multiple endpoints and were the
  root cause of the failing `seerr` commands investigated in 2026-09.

### Changed

- The default JSON output for the 15 size-to-summary candidate commands is
  now a curated per-command summary sized for chat-agent consumption:
  - `jellyfin`: `now`, `recent`, `favorites`, `resume`, `latest`
  - `radarr`: `wanted`, `queue`, `recent`
  - `sonarr`: `wanted`, `queue`, `recent`
  - `seerr`: `requests`, `search`, `available`
  - `maintainerr`: `pending`
- The remaining 13 commands (`jellyfin item`/`search`/`nextup`,
  `radarr calendar`/`lookup`/`movie`, `sonarr calendar`/`lookup`/`series`,
  `seerr request-count`/`user`, `maintainerr health`/`storage`)
  continue to emit verbatim service JSON unchanged.

### Added

- `--verbose` universal flag (registered on every per-service CLI) — emits
  the verbatim service JSON payload on stdout instead of the curated
  summary. The renderer priority chain is `--human` > `--verbose` >
  default summary > verbatim JSON.
- `arr_cli.facade.output.summarize(service, command, payload)` public
  function and the underlying `_SUMMARY_RENDERERS` dispatch table --
  single audit point for per-command summary rendering.
- `seerr tv <id>` -- per-show TV details via
  `GET /api/v1/tv/{tvId}?language=<LANG>`, with `--ratings` to also
  fetch Rotten Tomatoes critic + audience scores from
  `/api/v1/tv/{tvId}/ratings`. Default summary flattens `genres[]` /
  `networks[]` to comma-joined strings and surfaces `name`,
  `originalName`, `firstAirDate`, `numberOfSeasons`, `status`.
- `seerr movie <id>` -- per-movie details via
  `GET /api/v1/movie/{movieId}?language=<LANG>`, with `--ratings` to
  also fetch Rotten Tomatoes critic + audience scores from
  `/api/v1/movie/{movieId}/ratings`. Default summary flattens
  `genres[]` to a comma-joined string and surfaces `name`,
  `originalTitle`, `releaseDate`, `runtime` (reformatted from raw
  minutes to `"<X>h <Y>m"`), `tagline`. Structural twin of
  `seerr tv <id>` so future drift between the two commands fails
  the unit suite immediately.

### Fixed

- `seerr search <query>` now hits Seer's consolidated
  `/api/v1/search` endpoint instead of the legacy Overseerr
  `/api/v1/search/multi` path that Seer does not expose. Every
  invocation of `seerr search` was returning `HTTP 404` against a
  Seer instance; the historical Overseerr path was inherited from
  pre-fork documentation and was never reconciled against the live
  Seer `/api-docs/swagger-ui-init.js` OpenAPI spec (AGENTS.md §1
  "Seer note" guard paragraph). Help text, the per-service command
  table in `README.md` §4.5, and a `TestCmdSearch` regression test
  in `tests/unit/test_seerr.py` move with the handler fix so future
  drift of this exact path fails the unit suite immediately.
- `seerr user` now calls `/auth/me` directly instead of probing
  `/api/v1/user/me` first and falling back to `/auth/me` only on
  `404`. Seer does not expose `/api/v1/user/me` (the OpenAPI
  validator answers with `HTTP 400`), so the historical probe's
  `404`-only fallback trigger never fired and the auth self-check
  surfaced exit `4` against valid credentials. The `seerr user` row
  in `README.md` §4.5 and the `TestCmdUserHttpErrors` cases in
  `tests/unit/test_seerr.py` move with the handler fix so future
  drift of this exact path fails the unit suite immediately.
- `seerr requests` now returns the full household request queue
  instead of `[]`. The summary renderer unwraps Seer's documented
  paginated envelope (`{pageInfo, results, serviceErrors}`) and
  iterates `results`; previously any non-list payload short-circuited
  to an empty list. `cmd_requests` also asks for the documented
  `take=1000` cap so a single response covers the household queue
  rather than the default first page of ten.
- `seerr available <query>` now hits Seer's general
  `/api/v1/media` endpoint instead of the Overseerr-shaped
  `/api/v1/media/available` sub-resource that Seer does not
  expose. The historical sub-resource was inherited from
  pre-fork documentation and was never reconciled against the
  live Seer `/api-docs/swagger-ui-init.js` OpenAPI spec
  (AGENTS.md §1 "Seer note" guard paragraph); the operator's
  reverse-proxy has been answering `HTTP 405` on every method
  for the missing sub-resource since the upstream Seer fork
  landed. The handler now sends `take=1000&filter=available`
  to bound the response and uses the live-spec-supported
  `filter` param for the "in library" subset
  (`filter=available` is the leading hypothesis; verify the
  accepted token against the operator's live spec before
  merging per the AGENTS.md guard); title-substring matching
  is applied client-side after the fetch because Seer's
  `/api/v1/media` does not document a title-search query
  parameter. The `_summary_seerr_available` renderer unwraps
  the paginated envelope (`{pageInfo, results,
  serviceErrors}`) so the curated summary stays non-empty;
  help text, the per-service command table in `README.md`
  §4.5, and a new `TestCmdAvailable*` regression class in
  `tests/unit/test_seerr.py` move with the handler fix so
  future drift of this exact path fails the unit suite
  immediately.
- Jellyfin authentication: the facade now sends the full
  `Authorization: MediaBrowser ***` envelope (Client, Device, DeviceId,
  Version, Token) instead of the standalone `X-Emby-Token` header.
  Jellyfin 12.x deprecated the bare header, so all eight `jellyfin`
  subcommands had been returning 401 against current Jellyfin releases.
  `DeviceId` is anchored to the host hostname so the Jellyfin dashboard
  groups the CLI's activity under one stable device.
- `jellyfin recent` now forwards `includeItemTypes=Movie,Episode`
  alongside the existing `SortBy=DatePlayed&Filters=IsPlayed` query.
  Jellyfin 12's GetItems is asynchronous and applies the recursive
  rollup that 10.11 produced only when filters are paired with
  `includeItemTypes` (v12 release notes, "API Changes"). Without it,
  a live probe against a v12 instance returned a single Episode
  rather than the rolled-up set; `Movie,Episode` matches the
  operator's recent-played expectation. `TestCmdRecent::test_recent_hits_user_path_with_sort_and_filter`
  moves with the handler fix.
- `jellyfin favorites` now hits
  `GET /Users/{user_id}/Items?Filters=IsFavorite` instead of the
  pre-v12 `/Users/{user_id}/Items/Favorites` sub-resource. Jellyfin
  v12 removed the dedicated favorites sub-resource and consolidated
  the favorites list into the general user-scoped `Items` query,
  the same v12 shape `cmd_recent` uses with `Filters=IsPlayed`. The
  removed sub-resource answered `HTTP 400` with
  `itemId: The value 'Favorites' is not valid.` against v12
  instances, so every invocation of `jellyfin favorites` against a
  current Jellyfin release was exiting `4` with an empty stdout.
  The handler also forwards the universal `--limit` value to the
  service as `Limit=<n>` using the same defensive pattern
  `cmd_nextup` already uses, so the page-size cap stays consistent
  across commands. `TestCmdFavorites::test_favorites_hits_items_path_with_filter`
  and `TestCmdFavorites::test_favorites_forwards_limit` move with the
  handler fix so future drift of the removed sub-resource fails the
  unit suite immediately.

### Breaking

- Default JSON output for the 15 size-to-summary candidate commands is no
  longer verbatim; pass `--verbose` to restore the pre-change behaviour.

## [MVP] - 2026-07-22

The first shippable release of `arr-cli`. Five thin, read-only Python CLIs
backed by a single shared facade (`arr_cli.facade`) covering Jellyfin,
Radarr, Sonarr, Maintainerr, and Seer. Every command is an HTTP
`GET`; the package cannot mutate upstream state under any circumstance.

### Added

- **Five read-only CLI scripts** (29 commands total):
  - `jellyfin` — 8 commands: `now`, `resume`, `recent`, `nextup`, `latest`,
    `search <query>`, `item <id>`, `favorites`.
  - `radarr` — 6 commands: `calendar [start [end]]`, `wanted`, `queue`,
    `recent`, `lookup <term>`, `movie <id>`.
  - `sonarr` — 6 commands: `calendar [start [end]]`, `wanted`, `queue`,
    `recent`, `lookup <term>`, `series <id>`.
  - `maintainerr` — 3 commands: `pending`, `storage`, `health`.
  - `seerr` — 6 commands: `requests`, `request-count`, `search <query>`,
    `available <query>`, `media <tmdbId>`, `user`.
- **Shared facade package** (`arr_cli.facade`) providing:
  - `config.py` — YAML/TOML config loader with POSIX permission check,
    URL validation, and `ARR_*` environment-variable overrides.
  - `transport.py` — single HTTP entry point with per-service auth-header
    injection, percent-encoded params, and timeout enforcement.
  - `errors.py` — `ArrError` hierarchy with the five documented exit codes
    (`ConfigError`, `AuthError`, `NetworkError`, `HttpError`, `ParseError`).
  - `output.py` — JSON pass-through and tabular `--human` rendering with
    `COLUMNS` honouring and 120-column cap.
  - `cli_common.py` — shared `argparse` base with `--config`, `--debug`,
    `--quiet`, `--human`/`-h`, `--connect-timeout`, `--read-timeout`,
    `--retry`, `--deadline`, and `--limit` flags.
  - `retry.py` — optional exponential-backoff retry layer for `--retry N`.
- **Single canonical config** at `~/.config/arr/arr.conf` accepting YAML or
  TOML by extension and by leading-byte sniff; local copies are gitignored
  and `arr.conf.example` ships with placeholder-only values.
- **Uniform output contract:** verbatim service JSON on stdout by default;
  `--human` / `-h` for tabular readable mode; diagnostics always to stderr.
- **Stable exit-code map** across all five services: `1` config, `2` auth,
  `3` network, `4` HTTP-status, `5` parse.
- **Per-service auth matrix:** `jellyfin` → `X-Emby-Token`; `radarr`,
  `sonarr`, `seerr` → `X-Api-Key`; `maintainerr` → optional headers
  (no auth by default, with a one-time stderr warning when `auth.enabled`
  is false and `--quiet` is not set).
- **Stateless per-invocation execution:** no daemon, no persistent cache,
  per-call `requests.Session()`, percent-encoded URL params, and configurable
  connect/read timeouts (defaults `5s`/`30s`).
- **Placeholder-only committed example** (`arr.conf.example`) with no real
  URLs, API keys, or user identifiers.
- **`README.md`** documenting purpose, canonical config path, per-service
  command tables, the auth matrix, install/invoke instructions, the exit-code
  map, and the contributing rule.
- **CI hygiene scripts:**
  - `scripts/smoke.sh` — exercises one command per service with `--human`
    and default JSON; ships `--dry-run` for CI without network calls and
    `--live` gated behind `RUN_LIVE=1`.
  - `scripts/secret-scan` — greps the working tree for committed API-key
    shapes and fails the build on any match.
  - `scripts/example-lint.sh` — asserts `arr.conf.example` contains only
    documented placeholder values.
- **Performance budgets** enforced as unit tests: cold-start ≤ 2 s,
  peak RSS ≤ 80 MiB, `--human` over 1 000 items ≤ 1.5 s, large-payload
  cap of 10 000 items with a stderr warning on truncation.
- **Unit test suite** under `tests/unit/` covering config, transport,
  errors, output, CLI parsing, and per-service command routing; 516 tests
  passing on the feature/mvp branch.

### Out of scope (deferred to tier-2)

- **Maintainerr `veto`** — `POST /api/collections/media/handle`.
- **Seerr `create-request`** — `POST /api/v1/request` (would require a
  `--confirm` guard if added).
- **Any write/mutate endpoint** on any of the five services.
- **Webhook receivers** for any service.
- **Long-running daemon or persistent cache layer** — MVP is stateless per
  invocation by design.
- **Maintainerr `/api/rules`** — not present in the current Maintainerr
  OpenAPI; re-evaluate against the live `/api/swagger` at the next upstream
  API revision.
- **Integration tests** against a live operator instance — opt-in via
  `--run-integration` and gated behind `ARR_LIVE_URL` / `ARR_LIVE_API_KEY`;
  never runs in default CI.
