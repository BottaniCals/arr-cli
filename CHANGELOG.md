# Changelog

All notable changes to `arr-cli` are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
within the pre-1.0 contract documented in `README.md`.

## [Unreleased]

### Fixed

- `seerr available` README §4.5 doc drift -- the row for
  `seerr available <query>` no longer promises a client-side
  title-substring post-filter; that filter was retired in PR #39
  (`seerr-available-no-title-filter`, commit `5bf3629`) because
  upstream `GET /api/v1/media` records on the operator's Seer
  instance do not carry a top-level `title` field, which made the
  filter a guaranteed no-op. The "Notes" cell now describes the
  actual post-PR #39 behaviour: positional `<query>` is accepted
  for backwards compatibility but is ignored; a non-empty query
  emits a stderr note; use `seerr search <query>` to match against
  titles. No code change -- the handler, the curated summary, and
  the stderr note were already correct in PR #39. Pin test in
  `TestCmdAvailable.test_cmd_available_title_substring_filter_removed`
  (upgraded to assert the captured upstream request is unchanged
  regardless of `args.query`: no `query` / `title` / extra `filter`
  on the wire).
- `radarr recent` README drift -- the §4.2 Radarr table now
  documents the actual endpoint
  (`GET /api/v3/history?includeMovie=true&pageSize=<N>`) and
  the `--page-size` flag added in PR #52, replacing the stale
  `GET /api/v3/history/movie` row that escaped the original
  fix (REQs §4.3 Sonarr row was updated in PR #53). No code
  changes.
- `jellyfin search ""` -- the handler now short-circuits an empty
  query to the canonical emit path with `[]`, exiting 0 without
  touching `/Items`. The previous implementation forwarded
  `searchTerm=` (empty) to `GET /Items?searchTerm=&Recursive=true`;
  Jellyfin treats an empty `searchTerm` as "no filter applied", so
  the `Recursive=true` walk that PR #54 (`jellyfin-search-recursive`,
  commit `f8b5103`) explicitly requested resolved to "return the
  entire library" (4 303 items on the operator's instance) instead
  of the empty array README §4.1 and AC2 of REQ-6 AC6 advertise.
  The short-circuit runs before any HTTP call: default JSON emits
  verbatim `[]`; `--human` emits the documented `"(empty list)"`
  rendering. The `cmd_search` docstring was rewritten so the stale
  "service returns an empty array for empty queries" claim no
  longer misdescribes the contract. Wire shape, params dict,
  endpoint, and auth header are unchanged for the non-empty branch
  (AC1 / AC3 stay pinned by the surviving
  `test_search_forwards_query`,
  `test_search_query_with_special_chars`, and
  `test_search_forwards_recursive_true` cases). See
  `.bugs/jellyfin-search-empty-query/bug-review.md` for the
  wire-layer confirmation and rejected alternatives. Pin tests in
  `TestCmdSearch.test_search_empty_query_short_circuits_to_empty_list`,
  `..._missing_query_defaults_to_empty`, and
  `..._empty_query_human_renders_empty_list`; the two prior
  wrong-contract tests
  (`test_search_empty_query_still_calls_endpoint` /
  `test_search_missing_query_defaults_to_empty`'s outgoing-params
  assertion) were retired.
- `radarr recent` -- the curated summary is no longer silently
  empty. The historical implementation hit
  `GET /api/v3/history/movie` with no query parameters; that
  endpoint is a per-movie lookup and returns `[]` unless a
  `movieId` is supplied, so every invocation previously
  projected `[]` regardless of the underlying Radarr activity
  log. The endpoint switched to
  `GET /api/v3/history?includeMovie=true&pageSize=<N>` (the
  activity-log endpoint), which returns the full paginated
  history and populates the nested `movie: {title, year}`
  envelope when `includeMovie=true`. The upstream
  `{totalRecords, records}` envelope is unwrapped to the bare
  `records` list before rendering so the summary renderer and
  the `--human` table iterate the rows directly. The default
  page size is `10` (matching the project-wide "recent"
  semantics) and is overridable via a new `--page-size <N>`
  argparse flag, bounded to `[1, 1000]` so an operator cannot
  accidentally request a million-row history page. The
  renderer contract (nested `movie: {title, year}` shape) is
  preserved unchanged -- this is the distinguishing difference
  from the `sonarr recent` fix (commit `087c8bb`) which
  flattened the identity fields because Sonarr's activity-log
  rows never carry a nested envelope; Radarr's activity-log
  rows **do** carry the nested envelope when `includeMovie=true`
  and the project contract keeps that shape. `--verbose` is
  unchanged (raw upstream rows still on the wire); `--human`
  is unchanged (the columns literal `movie.title` /
  `movie.year` / `eventType` / `date` still resolves via
  dot-path traversal). Pin tests in
  `TestCmdRecent.test_recent_hits_history_path_with_include_movie_and_page_size`,
  `..._forwards_page_size_override`,
  `..._unwraps_paginated_envelope`,
  `TestMain.test_main_recent_hits_history_with_include_movie`,
  `..._with_page_size_forwards_value`,
  `..._with_invalid_page_size_exits_one`, and
  `TestSummaryRadarrRecent.test_recent_pins_actual_history_row_shape`.
- `jellyfin latest` -- the curated summary no longer projects
  `DateCreated`. The `GET /Users/{user_id}/Items/Latest` endpoint
  returns a slimmer DTO than `GET /Items/{id}`, and on the
  operator's Jellyfin v12 instance the slim DTO does not populate
  `DateCreated` on any row -- every row previously projected
  `DateCreated: null` (a "spurious null" on a field the operator
  reasonably expected populated). The summary now projects
  `{Name, Type, ProductionYear, SeriesName}`; the `--human`
  column list mirrors the trimmed shape. `DateCreated` remains
  available via `jellyfin item <id>` (chainable from `--verbose`
  or the curated summary's `Id` column on `favorites`).
  `--verbose` is unchanged (raw Latest rows still on the wire,
  `DateCreated` included or omitted by upstream). Pin tests in
  `TestSummaryJellyfinLatest.test_latest_drops_date_created` and
  `test_latest_shape`.
- `jellyfin favorites` -- pinned the type-based `SeriesName`
  contract that was implicit before this fix: the curated summary
  passes the upstream `SeriesName` through unchanged, so
  `SeriesName: null` is legitimate only for `Type=Movie`
  (Movies have no parent series in Jellyfin's model) and for
  `Type=Series` / `Type=BoxSet` (top-level entities with no
  parent series of their own), while `Type=Season` and
  `Type=Episode` populate the parent series name from the
  upstream payload. No code change to the renderer -- the
  contract was already correct -- but the previous tests only
  exercised the Movie branch, leaving the Season / Episode /
  Series branches implicit. Pin tests in
  `TestSummaryJellyfinFavorites.test_favorites_series_name_null_for_movie`,
  `..._populated_for_season`, `..._populated_for_episode`, and
  `..._null_for_series_top_level`.

- `sonarr recent` -- the curated summary now projects the nested
  `series: {title}` / `episode: {title}` envelopes that Sonarr
  populates when the operator opts in via the documented
  `includeSeries=true&includeEpisode=true` query parameters. The
  earlier fix (commit `087c8bb`) flattened the summary to
  identity fields because `/api/v3/history` returns flat rows by
  default; that fix missed that Sonarr DOES populate the nested
  objects when the include flags are passed. The endpoint now
  hits `GET /api/v3/history?includeSeries=true&includeEpisode=true`
  (the include flags are passed as a plain dict to `transport.get`
  so `requests` percent-encodes each value exactly once on the
  wire, per the PR #49 contract). The renderer projects both the
  nested objects (preserving the originally documented
  `{series: {title}, episode: {title}, eventType, date}`
  contract) and the flat identity fields the upstream payload
  also carries -- the fix is additive so `--json` consumers do
  not break on the field set. The `--human` column list uses
  dot-path tokens (`series.title` / `episode.title`) resolved by
  `_row_from_mapping`. A missing envelope renders as
  `{title: null}` instead of crashing. Sonarr's default page size
  is the documented `10` so no explicit `pageSize` is passed
  (the `radarr recent` fix added `pageSize` because Radarr's
  default is much larger). `--verbose` is unchanged (raw history
  rows still on the wire). Pin tests in
  `TestCmdRecent.test_recent_hits_history_path_with_include_series_and_episode`
  and `TestSummarySonarrRecent.test_sonarr_recent_projects_nested_series_and_episode_titles`.
- `seerr requests` -- the curated summary now surfaces the
  identity fields from the per-row `media` sub-dict (`id`,
  `mediaType`, `tmdbId`, `tvdbId`, `externalServiceSlug`,
  `status`) instead of a fabricated `title` populated from
  `media.title` (movie) or `media.name` (TV). The live
  `GET /api/v1/request` payload on the operator's Seer instance
  does not populate either of those fields -- every row
  projected `title: null` regardless of media type. The
  historical projection was based on the assumption that the
  `media` envelope carried a title, but the live payload only
  carries identity fields. The curated summary is now
  `{media: {id, mediaType, tmdbId, tvdbId, externalServiceSlug,
  status}, type, status, createdAt, requestedBy: {displayName}}`;
  the `--human` table column list mirrors the new shape. The
  requester (`requestedBy.displayName`) is intentionally NOT in
  the `--human` column list because the 120-char width budget
  divided across eight columns truncates the 23-char token to
  `requestedBy.di...`; the requester remains available in the
  default summary shape and the verbatim `--verbose` output.
  `--verbose` is unchanged (raw request envelope still on the
  wire). Mirrors the field set `seerr available` projects for
  a consistent mental model across the two read endpoints.
- `seerr requests` -- the curated summary now projects the
  identity fields (`id`, `mediaType`, `tmdbId`, `tvdbId`,
  `externalServiceSlug`) at the **top level** instead of nesting
  them behind `media.*` keys. The historical projection
  disagreed with the sibling `seerr available` summary, which
  returned flat identity fields at the top level; AGENTS.md §1
  says both should "project the identity fields instead" of
  `media.title`, so the two seerr read commands now share one
  mental model. The curated summary is now `{id, mediaType,
  tmdbId, tvdbId, externalServiceSlug, type, status, createdAt}`;
  the `--human` table column list mirrors the new flat shape
  (`id | mediaType | tmdbId | tvdbId | externalServiceSlug |
  type | status | createdAt`). The historical
  `requestedBy.displayName` projection was dropped (the
  `--human` column list does not include it; the requester
  remains available in the verbatim envelope via `--verbose`).
  `--verbose` is unchanged (raw request envelope still on the
  wire).
- `seerr search <query>` -- the curated summary now projects the
  top-level `id` field the upstream `GET /api/v1/search` payload
  actually carries, instead of fabricating a nested
  `mediaInfo.tmdbId` placeholder. Every row in the operator's
  Seer `search` payload exposes the TMDB/TVDB id at the top
  level (e.g. `id: 603` for *The Matrix*); the historical
  placeholder projected `{tmdbId: 0}` for every row regardless of
  the real id, which made the join key unusable downstream. The
  curated summary is now `{id, title, mediaType, releaseDate}`;
  the `--human` table column list mirrors the new shape. TV rows
  continue to source `title` from the top-level `name` field
  (unchanged). `--verbose` is unchanged (raw `search` envelope
  still on the wire). See
  `seerr-search-id-field` in the bug review for the full
  rationale and live-API evidence.
- `seerr trending`, `seerr upcoming-movies`, `seerr upcoming-tv`,
  `seerr discover-movies`, `seerr discover-tv` -- the curated
  summary now projects the top-level `id` field the upstream
  `GET /api/v1/discover/{trending,movies/upcoming,tv/upcoming,movies,tv}`
  payloads actually carry, instead of fabricating a nested
  `mediaInfo: {"tmdbId": 0}` placeholder for every row whose
  upstream payload lacks the nested `mediaInfo` envelope. The
  same defensive-else branch existed in five sibling renderers
  after PR #41 fixed it for `seerr search`; live QA on
  2026-09-18 confirmed 64 of 100 curated rows across the five
  endpoints rendered the fabricated `tmdbId: 0` (18/20 on
  `upcoming-tv`, 11/20 on `upcoming-movies`, 9/20 on
  `discover-movies`, 17/20 on `discover-tv`, 9/20 on
  `trending`) because upstream never exposed a nested
  `mediaInfo` envelope for those rows. The historical projection
  made every curated `tmdbId` field a guaranteed zero, which made
  the join key unusable downstream. The curated summary is now
  `{id, title, mediaType, releaseDate}` (movie keys) or
  `{id, title, mediaType, releaseDate}` sourced from `name` /
  `firstAirDate` (TV keys); the `--human` table column list
  mirrors the new shape (`id | title | mediaType | releaseDate`).
  `--verbose` is unchanged (raw discover envelope still on the
  wire). Mirrors the field set `seerr search` (PR #41) and
  `seerr available` (PR #39) project for a consistent mental
  model across all five `seerr` read endpoints that share the
  same upstream discover envelope shape.

### Changed

- `seerr available <query>` -- the client-side title-substring
  post-filter was removed because upstream `GET /api/v1/media`
  records on the operator's Seer instance do not carry a top-level
  `title` field, which made the filter a guaranteed no-op (every
  row was dropped, so `seerr available ''` and `seerr available
  'the'` both returned `[]`). The default summary now surfaces the
  upstream-provided identifiers instead: `id`, `mediaType`,
  `tmdbId`, `tvdbId`, `externalServiceSlug`, `status`,
  `mediaAddedAt`. The positional `<query>` is still accepted for
  backwards compatibility but is ignored; a stderr note is emitted
  when a non-empty `query` is passed so the operator is not
  surprised by what looks like an empty result. `--verbose` is
  unchanged (full `media` records still on the wire). Operators who
  need a friendly title can resolve it via
  `seerr search <query>` or look up `tmdbId` / `tvdbId` externally.

### Added

- `seerr genres [MEDIA_TYPE]` -- TMDB genre list as `[{id, name}, ...]`,
  fetched from `GET /api/v1/genres/<movie|tv>` so the operator can map
  a friendly genre name (e.g. `Sci-Fi`) to its TMDB integer id (e.g.
  `878`) before passing it to `seerr discover-movies --genre` /
  `seerr discover-tv --genre`. Optional positional `MEDIA_TYPE`
  (`movie` / `tv`; default `movie`) mirrors `seerr trending`'s
  positional-with-default pattern; argparse rejects any other value
  with exit code 2 at parse time. Default summary projects each row
  to `{id, name}`; `--verbose` emits the verbatim JSON list;
  `--human` renders the documented `Id | Name` tabular view. Pairs
  with the new `seerr discover-movies` / `seerr discover-tv` filters.
- `seerr discover-movies` -- filterable movie discover against
  `GET /api/v1/discover/movies`. Defaults `sortBy=popularity.desc`,
  `language=en-US`, `page=1`. Optional filters `--genre <id>` (int),
  `--sort <sortBy>`, `--language <code>`, `--page <n>`. Universal
  `--limit` is client-side only (caps the renderer, never sent on
  the wire). Pair with `seerr genres movie` to look up TMDB genre ids.
- `seerr discover-tv` -- structural twin of `discover-movies` for TV
  against `GET /api/v1/discover/tv`. Same flag surface, same defaults,
  same client-side `--limit` contract. Pair with `seerr genres tv` to
  look up TMDB genre ids.
- `seerr genres [MEDIA_TYPE]` -- new `--language <LANG>` flag
  (`ISO 639-1`) forwarded as `?language=<LANG>` on
  `GET /api/v1/genres/<movie|tv>`. Fixes the broken
  `seerr genres movie --language en → discover-movies --genre <id>
  --language en` chain: the lookup step previously rejected the
  flag at parse time (exit code 2) because the historical handler
  docstring claimed the endpoint was parameter-free -- a misread
  against the live Seer API. Both `/api/v1/genres/movie` and
  `/api/v1/genres/tv` accept and honour the `language` query
  parameter on the operator's live Seer instance. Without the
  flag the request stays parameter-free (server default
  behaviour preserved). `--help` lists the new option in the
  same shape as `seerr tv --help` / `seerr movie --help`. Pin
  tests in `TestCmdGenres.test_seerr_genres_language_en_hits_movie_endpoint_with_query_param`,
  `..._hits_tv_endpoint_with_query_param`,
  `..._no_language_omits_language_query_param`, and
  `..._help_lists_language_option`.

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
- `seerr trending [MEDIA_TYPE] [TIME_WINDOW]` -- Seer's
  `GET /api/v1/discover/trending?page=&language=&mediaType=&timeWindow=`
  endpoint. Both optional positional args are constrained by
  argparse `choices=` (`MEDIA_TYPE` ∈ {`movie`, `tv`};
  `TIME_WINDOW` ∈ {`day`, `week`}, defaulting to `week`); an
  optional `--language` matches the per-service localization
  surface used by `seerr tv <id>` / `seerr movie <id>`. Default
  summary projects `title`, `mediaType`, `releaseDate`,
  `mediaInfo.tmdbId` per result and unwraps the documented
  `{page, results, totalPages, totalResults}` envelope shared
  with `seerr search`. `--limit`, `--human`, and `--verbose`
  route through the standard renderer priority chain
  (`--human` > `--verbose` > default summary > verbatim JSON).
- `seerr upcoming-movies` -- upcoming movie releases via `GET /api/v1/discover/movies/upcoming?page=<…>&language=<…>`. Default summary mirrors `seerr trending` (envelope `results` iterated; per-item projection `title`, `mediaType`, `releaseDate`, `mediaInfo.tmdbId`). Universal flags only; no positional media type (fixed at command level).
- `seerr upcoming-tv` -- upcoming TV premieres via `GET /api/v1/discover/tv/upcoming?page=<…>&language=<…>`. Default summary mirrors `seerr trending` (envelope `results` iterated; per-item projection `title`, `mediaType`, `releaseDate`, `mediaInfo.tmdbId`). Universal flags only; no positional media type (fixed at command level).

### Fixed

- `jellyfin item <id>` now sends the required `UserId` query
  parameter on Jellyfin v12+. Without it the server returns
  HTTP 400 ``Error processing request.`` and the CLI surfaced
  the failure as exit 4 with no actionable hint. ``cmd_item``
  reads ``UserId`` from ``cfg.jellyfin.user_id`` via
  ``_require_user_id`` (mirrors ``cmd_nextup``), so a missing
  config value now surfaces as ``ConfigError(exit 1)`` with the
  documented "user_id missing" message instead of the upstream
  400. The 404 path is unchanged: ``HttpError(exit 4)`` still
  names the id on stderr. The ``TestCmdItem`` suite gains
  ``test_item_forwards_user_id_param`` (asserts
  ``params={"UserId": "jf-user-1"}``) plus
  ``test_item_missing_user_id_raises_config_error`` and
  ``test_item_missing_service_section_raises_config_error``
  to lock the new contract.
- `jellyfin favorites` now emits `Id` in both the default curated
  summary and the `--human` tabular view, restoring
  pipe-chainability into `jellyfin item <id>`. The
  `_summary_jellyfin_favorites` projection dict literal and the
  `cmd_favorites` `columns` literal both prepended `Id`; the two
  test fixtures (`_SUMMARY_RENDERERS_FAVORITES`,
  `_HUMAN_RENDERER_TEST_PAYLOADS["jellyfin"]["favorites"]`) and the
  two `TestSummaryJellyfinFavorites` assertions
  (`test_favorites_shape`, `test_envelope_with_items_is_unwrapped`)
  move with the renderer fix so future drift of this exact
  projection fails the unit suite immediately.
  See `.bugs/jellyfin-favorites-summary-id-field/bug-review.md`.
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
- `seerr discover-movies` and `seerr discover-tv` no longer hardcode
  `sortBy=popularity.desc` + `language=en-US` on the wire. The
  unconditional forward was returning `totalResults: 0` against the
  operator's live Seer build (a silent locale+sort join failure on
  upstream), so both handlers now drop the defaults and only forward
  `?sortBy=<…>` / `?language=<…>` when the operator passed the matching
  flag. The argparse `--sort` and `--language` defaults change from
  `"popularity.desc"` / `"en-US"` to `None`; `--help` reflects the new
  "(omit = use upstream default)" semantics. The default wire-format
  (`discover-movies` / `discover-tv` with no flags) and the explicit-flag
  wire-format (only the named key rides the wire) are pinned by new
  `TestCmdDiscoverMovies::test_seerr_discover_movies_default_omits_sort_and_language`
  / `TestCmdDiscoverTv::test_seerr_discover_tv_default_omits_sort_and_language`
  regressions so future re-introduction of the unconditional forward
  fails the unit suite immediately. Trending / upcoming / search /
  `genres` are unaffected -- they already followed the omit-when-default
  rule.
- `jellyfin favorites` summary now unwraps the v12
  `{Items, TotalRecordCount, StartIndex}` envelope instead of
  iterating the envelope as if it were a list. The renderer's
  previous shape matched the pre-v12 flat list and dropped every
  well-formed v12 response to `[]`; the handler fix above proved
  correct against the wire (verified via `--verbose` and direct
  probe), but the user-visible default summary was still empty
  for every invocation. The renderer now mirrors the
  `_summary_seerr_search` envelope-unwrap pattern
  (`payload = payload.get("Items")`) so a bare list and an
  envelope are both rendered as the curated `{Name, Type,
  ProductionYear, SeriesName}` projection. A new
  `TestSummaryJellyfinFavorites::test_envelope_with_items_is_unwrapped`
  in `tests/unit/test_output.py` feeds the renderer the documented
  envelope shape and asserts the items surface, so a regression to
  the pre-v12 flat-list assumption fails the unit suite immediately.

- `seerr discover-tv`, `seerr upcoming-tv`, and the TV half of
  `seerr trending` now surface TV-shaped fields under the curated
  `title` / `releaseDate` keys instead of rendering every row as
  `title=None, releaseDate=None`. The three size-to-summary renderers
  (`_summary_seerr_discover_tv`, `_summary_seerr_upcoming_tv`,
  `_summary_seerr_trending`) were projecting the movie-shaped
  `title` / `releaseDate` keys against an envelope where TV items
  use `name` / `firstAirDate`. `_summary_seerr_trending` branches per
  item on `mediaType` because the unfiltered `seerr trending`
  envelope can mix `movie` and `tv` items; the two TV-only renderers
  read `name` / `firstAirDate` unconditionally. The three
  renderers' docstrings are updated to drop the stale
  "byte-identical to <movie sibling>" claims that were the root of
  the copy-paste bug. New `TestSummarySeerrTrending`,
  `TestSummarySeerrUpcomingTv`, and `TestSummarySeerrDiscoverTv`
  classes in `tests/unit/test_output.py` feed each renderer the
  documented TV envelope (and, for `trending`, a mixed envelope)
  and assert `title` / `releaseDate` surface from the TV keys, so a
  regression to the movie-shape projection fails the unit suite
  immediately. Movie-flavored variants (`seerr discover-movies`,
  `seerr upcoming-movies`, `seerr trending movie …`) are unaffected.

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
