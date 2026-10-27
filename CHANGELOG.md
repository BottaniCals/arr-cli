# Changelog

All notable changes to `arr-cli` are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
within the pre-1.0 contract documented in `README.md`.

## [Unreleased]

### Added

- `jellyfin --human`: every size-to-summary table now carries the
  audit-requested fields. `jellyfin now` adds `LastActivityDate`,
  `PlayState.IsPaused`, `NowPlayingQueue` length, and a transcoding
  indicator; `jellyfin resume` / `jellyfin recent` add
  `PercentPlayed` (the playback progress rounded to one decimal
  place, e.g. `"63.8%"`); `jellyfin nextup` adds `RunTimeTicks`
  and a truncated `Overview`; `jellyfin latest` adds `RunTimeTicks`
  → minutes, `PremiereDate`, and `Genres`; `jellyfin favorites`
  adds `RunTimeTicks` → minutes and `UserData.LastPlayedDate`;
  `jellyfin search` defaults to `Name`, `Type`, `ProductionYear`.
  Run times are normalised from Jellyfin's 100-ns ticks to minutes
  via a shared `_format_runtime_minutes` helper; `Genres` joins via
  a shared `_join_names` helper.
- `jellyfin item <id>`: registered a new `_summary_jellyfin_item`
  renderer in `_SUMMARY_RENDERERS`, so the command now returns a
  curated single-record table (`Name`, `Type`, `ProductionYear`,
  `PremiereDate`, `RunTimeTicks`, `Genres`, `Overview`, `Status`)
  instead of dumping the raw Jellyfin JSON.
- `radarr --human`: every size-to-summary table now carries the
  audit-requested fields. `radarr movie` adds `hasFile`, `genres`,
  `added`, `collection.title`, and `studio`; `radarr calendar`
  (dated and open-ended) adds `monitored`, `hasFile`, `genres`,
  `runtime`, and `collection.title`; `radarr wanted` adds `hasFile`,
  `genres`, `physicalRelease`, and `releaseDate`; `radarr queue`
  adds `title`, `year`, `quality`, `status`, and `timeLeft`;
  `radarr recent` adds `sourceTitle`, `quality`, `language`, and
  `client`; `radarr lookup` adds `hasFile`, `genres`, a truncated
  `overview`, and `status`. Long `overview` strings are truncated
  via a shared `_format_overview` helper.
- `radarr movie <id>`: registered a new `_summary_radarr_movie_id`
  renderer in `_SUMMARY_RENDERERS`, so the command now returns a
  curated single-record table (`title`, `year`, `status`,
  `monitored`, `hasFile`, `genres`, `overview`, `added`, `studio`)
  instead of dumping the raw Radarr JSON.
- `sonarr --human`: every size-to-summary table now carries the
  audit-requested fields. `sonarr calendar` adds `monitored`,
  `hasFile`, `runtime` formatted as `"<X>h <Y>m"`, and a truncated
  `overview`; `sonarr wanted` adds `hasFile`, `runtime`, `overview`,
  and `series.title`; `sonarr queue` adds `series.title`,
  `episode`, `quality`, and `timeLeft` (the pre-change `status`
  stays in its existing position and is not re-appended at the
  tail; no `progress` column is invented); `sonarr recent` adds
  `language` (the comma-joined names of the upstream `languages`
  array) and `client` (resolved from `data.downloadClient`);
  `sonarr lookup` adds `statistics`, `genres`, `overview`, and
  `status`; `sonarr series` (library list) adds `statistics`,
  `genres`, `firstAired`, and `lastAired`. The new
  `_format_episode_progress` helper formats
  `statistics.episodeFileCount / statistics.totalEpisodeCount` as
  `"<file>/<total>"`; existing helpers `_format_runtime_minutes_whole`,
  `_truncate_overview`, and `_join_names` are reused.
- `sonarr series <id>`: registered a new `_summary_sonarr_series_id`
  renderer in `_SUMMARY_RENDERERS`, so the command now returns a
  curated single-record table (`title`, `year`, `runtime`,
  `status`, `monitored`, `statistics`, `genres`, `firstAired`,
  `lastAired`) instead of dumping the raw Sonarr JSON. The
  with-id branch dispatches on `("sonarr", "series_id")` via the
  `command=` kwarg so the renderer routes correctly while sharing
  the same argparse subcommand name with the no-id branch.
- `maintainerr --human`: every size-to-summary table now carries
  the audit-requested fields. `maintainerr pending` adds
  `arrAction`, `addDate`, `mediaServerType`, `type`, a truncated
  `description`, `isActive`, and `visibleOnHome`; `maintainerr
  storage` projects one row per upstream `mounts[]` entry with
  `mount`, `used`, `free`, `total`, and `usedPercent`, plus a
  `(totals)` row; `maintainerr health` is now a size-to-summary
  candidate that emits a single-line `OK` / `DEGRADED` token
  instead of dumping the raw upstream object. Long `description`
  strings are truncated via the existing `_truncate_overview`
  helper.
- `maintainerr storage` and `maintainerr health`: registered two
  new renderers (`_summary_maintainerr_storage`,
  `_summary_maintainerr_health`) in `_SUMMARY_RENDERERS`, so both
  commands now return a curated summary in default mode. The
  `storage` renderer projects rows from the live dict envelope
  and still tolerates the legacy list envelope;
  the `health` renderer maps `status == "ok"` to `"OK"` and
  every other shape (degraded / down / unknown string, bare
  `False`, non-mapping) to `"DEGRADED"` as the defensive default.
  `cmd_storage`'s `columns = [...]` literal widened to
  `["mount", "used", "free", "total", "usedPercent"]`; `cmd_health`
  continues to pass `columns=None` so the scalar `"OK"` /
  `"DEGRADED"` flows through the output module's `_render_scalar`
  branch.
- `seerr --human` widening per the `audit-2026-10-16` UX audit:
  `seerr requests` adds `requestedByUsername`, `mediaAddedAt`,
  `seasonCount`; `seerr search` adds a truncated `overview`,
  `popularity`, and `genreIds`; `seerr trending` and the
  upcoming / discover buckets add `popularity` and a truncated
  `overview`, with `discover-movies` / `discover-tv` additionally
  surfacing `genreIds`; `seerr movie <id>` adds a truncated
  `overview`, `status`, and `mediaInfoStatus`; `seerr tv <id>`
  adds `lastAirDate`, `numberOfEpisodes`, a truncated `overview`,
  `status`, and `mediaInfoStatus`; `seerr user` is now a
  size-to-summary candidate that projects `username`, `email`,
  `userType`, `permissions` (flattened via the documented list /
  mapping guard), and `requestCount`. All eleven affected `cmd_*`
  `columns` literals in `arr_cli/seerr.py` mirror the renderer
  body's left-to-right key order so the `--human` table and the
  default-mode JSON share a single shape. Missing fields default
  to `None` (rendered as `<null>`) so the renderer stays
  well-formed even when upstream drops a field.

### Changed

- **Breaking:** the default (non-`--verbose`) JSON summary shape
  changes for `jellyfin resume` / `jellyfin recent`, `seerr
  requests`, and `seerr available`.
  - The playback-progress key of `jellyfin resume` / `jellyfin
    recent` is renamed from `UserData.PlayedPercentage` to
    `PercentPlayed`, and the value is now the rounded percentage
    string (e.g. `"63.8%"`) instead of the raw upstream float
    (e.g. `63.78841043647125`). A consumer that read
    `summary["UserData.PlayedPercentage"]` as a number must migrate
    to `summary["PercentPlayed"]` (a string carrying the `%` sign).
    The `--human` column header changes from `UserData.PlayedPercentage`
    to `PercentPlayed` alongside it.
  - The TVDB id of `seerr requests` and `seerr available` is renamed
    from `tvdbId` to `tvdbIdRef` to mark it as a reference-only cell:
    `seerr tv` / `seerr movie` are keyed by the TMDB id, so the TVDB
    id is not a valid argument for them. A consumer that read
    `summary["tvdbId"]` must migrate to `summary["tvdbIdRef"]`. The
    `--human` column header changes from `tvdbId` to `tvdbIdRef`
    alongside it, in both tables -- so the two seerr read commands
    publish the same identity field set under the same key names.
  - The `--verbose` verbatim pass-through is unchanged, and so are the
    exit-code contract and the remaining summary keys of both
    commands.
- **Breaking:** the default (non-`--verbose`) JSON summary key
  `collection.name` is renamed to `collection.title` for `radarr
  movie` (library list) and `radarr calendar`. The upstream Radarr
  collection envelope is the live `CollectionResource` of the shape
  `{"title": str, "tmdbId": int}` -- it carries a `title`, never a
  `name` -- so the old key projected `<null>` for every row. A
  consumer that reads `collection.name` out of the default output
  must migrate to `collection.title`. The `--verbose` verbatim
  pass-through is unchanged, and the `--human` column header of the
  affected tables changes from `collection.name` to
  `collection.title` alongside it.
- **Breaking:** the default (non-`--verbose`) JSON summary shape
  changes for the four Radarr / Sonarr queue / recent rows and the
  two Seerr detail summaries. The `quality` value of `radarr queue`,
  `radarr recent`, `sonarr queue` and `sonarr recent` is now the
  release-quality display name string (e.g. `"WEBDL-1080p"`)
  instead of the upstream release-quality object -- a consumer that
  read the nested `quality` mapping must migrate to the scalar
  `quality` string. The nested `ratings` key of `seerr movie <id>`
  and `seerr tv <id>` is replaced by the three flat scalar keys
  `criticsScore`, `audienceRating` and `audienceScore` -- a
  consumer that read `summary["ratings"]["criticsScore"]` must
  migrate to `summary["criticsScore"]` (likewise for
  `audienceRating` and `audienceScore`). Both changes keep a nested
  object out of the `--human` cells (a mapping used to render as
  the `<N keys>` placeholder); a missing value renders as `<null>`.
  The `--verbose` verbatim pass-through is unchanged, and so are
  the `--human` column names, the column sets and the exit-code
  contract.
- `seerr --human` live-spec correction (the previous entry's
  hermetic-gate deferral is retired in favour of the verified
  direct projection): the audit-asked `title` column on
  `seerr available` is gone for good -- the live `/api/v1/media`
  envelope carries identity fields only and does not populate
  a `title` field on the operator's Seer instance (verified
  directly on 2026-10-17); the renderer follows the AGENTS.md §1
  identity-only contract and never projects a `title` cell, with
  a regression test (`tests/unit/test_output.py
  ::TestSummarySeerrAvailable::test_seerr_available_does_not_render_title_even_when_present`)
  pinning the contract and naming the 2026-09-18
  `seerr-available-no-title-filter` regression vector so any
  future contributor who tries to add a `title` projection back
  sees a hard test failure with the audit context preserved.
  The `seerr movie <id>` / `seerr tv <id>` `--ratings` projection
  ships the live-spec key names the operator verified directly:
  `criticsScore` (plural-with-s integer, RT's Tomatometer
  percentage 0-100), `audienceRating` (categorical string
  such as `"Spilled"` / `"Upright"`) and `audienceScore` (the
  numeric RT audience score); the three keys are projected
  verbatim when the upstream envelope populates them, and a
  missing field (e.g. when RT did not publish a categorical
  rating) renders as `<null>` next to its populated siblings --
  clearly distinguishable from "no ratings fetched". The previous
  key-name matrix (singular `criticScore` / plural `criticsScore`
  etc.) is dropped: the previous env-var gate deferral is retired
  in favour of the verified direct projection. The `seerr user` `permissions` cell becomes an
  integer-bitfield decoder -- the live `/api/v1/auth/me`
  envelope (verified directly on 2026-10-17) carries
  `"permissions": <int>`, a single integer bitfield with the
  documented map `1=request, 2=admin, 4=manage_users,
  8=manage_issues, 16=auto_approve, 32=view_users`, decoded
  into a sorted-by-bit comma-joined name list (e.g. `7 ->
  "request, admin, manage_users"`); `0` renders as `""` (the
  documented "no bits set" blank-cell contract; distinct from
  the defensive `<null>` fallback for non-integer shapes), and
  the historical Overseerr `permissions` list / mapping shape
  falls back to `None` per the AGENTS.md §4.1 defensive
  contract. The three hermetic-gate helpers and their associated
  deferral gates are removed so the renderer body reflects the
  live spec directly. CI stays hermetic: no live OpenAPI fetch from CI,
  the bitfield map / live key names are committed constants in
  the renderer body. The deferral-vs-direct-projection choice
  is preserved in this entry because the previous "deferred
  behind env-var gates" framing was correct in spirit (the
  audit was working from an incorrect field-name map) but the
  gate mechanics were wrong: the live Seer spec was verified
  directly by the operator on 2026-10-17, so there is no
  remaining decision to defer.

### Fixed

- The `--run-integration` pytest flag was advertised by the README, by
  `AGENTS.md` and by the integration-suite docstring, but it was never
  registered with pytest's own argument parser. Every documented
  invocation (`pytest tests/integration/ --run-integration`,
  `pytest tests/ --run-integration`, `make integration-test`) aborted
  with `unrecognized arguments: --run-integration` (pytest exit code
  `4`) while parsing the command line, so the opt-in integration suite
  was unreachable through its documented channel. The flag is now
  registered by the project-level root `conftest.py` and handed to the
  skip helper through the `ARR_RUN_INTEGRATION` environment variable,
  so `make integration-test` runs and skips cleanly with an explicit
  reason when no live endpoint is configured. The default unit-test
  path, the `ARR_RUN_INTEGRATION=1` unittest path and the
  "integration tests are opt-in" default are unchanged.
- The `jellyfin --human resume` / `jellyfin --human recent` tables
  rendered the playback progress as the raw upstream float, truncated
  mid-number by the table width budget (`63.78841043647…` for the
  upstream value `63.78841043647125`), under a header that truncated
  as well (`UserData.Playe…` on the 8-column `resume` table,
  `UserData.PlayedPerc…` on the 6-column `recent` table). The cell
  now carries the rounded percentage (`63.8%`) under a `PercentPlayed`
  header that fits the same budget -- see the `Changed` entry above
  for the summary-key rename.
- `seerr tv <id>` / `seerr movie <id>` are keyed by the **TMDB** id,
  but the `seerr tv` help text advertised "TMDB/TVDB TV show id" and
  the `seerr requests` and `seerr available` tables surfaced the
  upstream TVDB id under a bare `tvdbId` column -- the very value the
  operator would then feed back into `seerr tv`, where the upstream
  rejects it with an opaque HTTP 500 `Unable to retrieve series.`.
  Both commands' `--help` now names the TMDB id explicitly (and
  states that TVDB ids are not accepted), both table columns are
  labelled `tvdbIdRef` to mark them as reference-only, and a wrong-id
  lookup re-raises the same `HttpError` (exit code `4`, pipe-clean
  stdout) with a hint naming the TMDB-id requirement instead of the
  bare upstream message.
- The Sonarr `statistics` cell (`sonarr lookup`, `sonarr series`,
  `sonarr series <id>`) rendered a misleading `0/0` for every library
  series: the denominator read `statistics.episodeCount`, which
  upstream counts as the monitored-and-aired (or already-downloaded)
  subset -- `0` for an unmonitored series -- instead of the series
  episode total carried in `statistics.totalEpisodeCount`. The cell now
  reads `statistics.episodeFileCount / statistics.totalEpisodeCount`
  (e.g. `0/85` for a library series with 85 known episodes and no
  files yet). A zero or absent denominator now renders `<null>`
  instead of `0/0`; that is the shape upstream
  `/api/v3/series/lookup` returns for a candidate that is not in the
  library yet. The default-mode JSON summary keeps `"statistics": null`
  for such a row, and the `--verbose` verbatim pass-through and the
  exit-code contract are unchanged.
- Several `--human` summary tables rendered `<null>` in columns
  whose values do exist upstream; the projector read a key the
  service never sends.
  - The Radarr collection column now projects the real collection
    title (`collection` -> `title`) instead of a `name` key the
    upstream `CollectionResource` does not carry -- see the
    `Changed` entry above for the key rename.
  - The Radarr and Sonarr `recent` tables now resolve `language`
    from the upstream `languages` array (comma-joined names) and
    `client` from the nested `data.downloadClient` entry; there is
    no top-level `language` / `downloadClient` key on a history
    row.
  - The Seerr `requests` table now projects `seasonCount` from the
    request item -- the number of requested seasons -- instead of
    the nested `media` envelope, which does not populate it.
  - The Sonarr `wanted`, `calendar`, and `queue` commands now send
    the upstream include flags (`includeSeries=true`, plus
    `includeEpisode=true` on `queue`) so the nested `series` /
    `episode` envelopes are populated and the `series.title` /
    `episode` columns carry the upstream titles. The flat row
    fields are unaffected upstream and are projected either way.
- Every corrected lookup tolerates an absent envelope and degrades
  to `<null>` instead of raising, so a payload that predates the
  include flags still renders a well-formed table. The exit-code
  contract and the `--verbose` verbatim-payload behaviour are
  unchanged.
- `maintainerr --human storage` rendered `(empty list)` instead of the
  storage table. The summary renderer only accepted the legacy list
  envelope, while the live `GET /api/storage-metrics` endpoint
  answers with a dict envelope (`{totals, mounts[], instances[], ...}`)
  — so the renderer's type guard turned a payload full of volumes
  into an empty list. The renderer now understands the dict envelope:
  one row per `mounts[]` entry (`mount` = the mount `path`, `free` =
  `freeSpace`, `total` = `totalSpace`, `used` = `totalSpace -
  freeSpace`, `usedPercent` derived from those two) plus a
  `(totals)` row carrying the upstream `totals` aggregate verbatim
  (upstream deduplicates shared mounts, so the aggregate is not the
  sum of the rows). The legacy list envelope stays tolerated, missing
  fields still render as `<null>`, and a mount whose capacity upstream
  reports as inaccurate no longer renders a misleading `0` used.
  **Behaviour change for consumers:** the default (non-`--verbose`)
  JSON summary of `maintainerr storage` changes from `[]` to the
  curated rows; the row keys (`mount`, `used`, `free`, `total`,
  `usedPercent`) are unchanged. The `--verbose` verbatim pass-through
  and the exit-code contract are unchanged.

## [0.1.0]

### Added

- First public release of `arr-cli`: read-only Python CLI wrappers
  around a self-hosted media stack (Jellyfin, Radarr, Sonarr,
  Maintainerr, Seer).
- Five executables — `jellyfin`, `radarr`, `sonarr`, `maintainerr`,
  `seerr` — backed by a single shared `arr_cli.facade`.
- Configuration via `~/.config/arr/arr.conf` (YAML or TOML); per-
  invocation override via `--config`. `POSIX 0600` enforced.
- Five stable exit codes: 1 ConfigError / 2 AuthError / 3
  NetworkError / 4 HttpError / 5 ParseError.
- Universal flags: `--human`/`-h`, `--verbose`, `--debug`,
  `--quiet`, `--config`, `--connect-timeout`, `--read-timeout`,
  `--retry`, `--deadline`.
- `arr.conf.example` placeholder-only schema with `scripts/secret-scan`
  enforcing the placeholder-only guarantee in CI.
- Integration test harness under `tests/integration/`, opt-in via
  `--run-integration` (or `ARR_RUN_INTEGRATION=1` for unittest).

### Known limitations (MVP, deliberate)

- All commands are HTTP `GET`.
- No daemon, no cache layer; every invocation is stateless.
