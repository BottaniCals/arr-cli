# arr-cli

Read-only Python CLI wrappers around a self-hosted media server stack —
Jellyfin, Radarr, Sonarr, Maintainerr, and Seer. The package ships
five thin executables (`jellyfin`, `radarr`, `sonarr`, `maintainerr`, `seerr`)
backed by a single shared facade (`arr_cli.facade`) that owns configuration,
HTTP transport, authentication, error mapping, and output formatting. Together
they expose **36 read-only commands** (every call is an HTTP `GET`).

The CLIs are intended for two uses:

- shell pipelines that consume the verbatim JSON on stdout (pass `--verbose` to see the full upstream payload),
- ad-hoc terminal inspection with `--human` / `-h` to render the
  response as a readable table.

This is the **MVP** release. It is deliberately read-only, stateless per
invocation (no daemon, no mandatory cache), and safe to leave lying around —
a single broken service cannot break the others.

---

## 1. Purpose

`arr-cli` exists so that agents can talk to the media server stack.
Every service URL, API key, and user id lives in a single
gitignored config file (`~/.config/arr/arr.conf`); every command emits a
per-command output on stdout (the default JSON output is a curated summary
for the 17 size-to-summary candidate commands; pass `--verbose` for the
verbatim service payload) and a tabular readable view with `--human` / `-h`.
There are no write endpoints in MVP: this package cannot mutate the media
server state under any circumstance.

---

## 2. Canonical config path

The CLIs read configuration from:

```
~/.config/arr/arr.conf
```

Per-invocation override:

```
jellyfin now --config /path/to/arr.local.yaml
```

Env-var overrides are also honored (see `arr.conf.example` for the full list
of `ARR_*` keys). The CLI will:

- Exit non-zero with a stderr message naming the canonical path if the config
  is missing.
- Exit non-zero with a permission-error message if the file is readable by
  group or world (POSIX mode must be `0600` or stricter).
- Exit non-zero with a `unknown format` error if neither the extension nor
  the leading-byte sniff can pick YAML vs TOML.

---

## 3. Placeholder-only example

A placeholder schema is committed as `arr.conf.example`. Copy it to your
local config path and fill in real values. **Do not commit a real `arr.conf`.**
Real keys go in a local file only; `arr.conf.example` ships with placeholders.

```yaml
# arr.conf.example - placeholder-only example.
#
# Copy this file to ~/.config/arr/arr.conf and fill in real values locally.
# NEVER commit a real arr.conf. The real file is gitignored (see .gitignore).
#
# Both YAML and TOML are accepted (selected by file extension .yaml/.yml/.toml).
# This file is YAML; a TOML-only equivalent appears at the bottom of this file.

# --------------------------------------------------------------------
# Top-level transport defaults. Override per invocation via CLI flags.
# --------------------------------------------------------------------
connect_timeout: 5.0 # seconds - connect timeout
read_timeout: 30.0 # seconds - read timeout
retry: 0 # 0 = no retries (default). Use --retry N on CLI to retry.
deadline: null # absolute wall-clock cap for retries, in seconds

# --------------------------------------------------------------------
# Jellyfin (media playback server)
# --------------------------------------------------------------------
jellyfin:
  url: https://example.com # replace with your instance URL
  api_key: YOUR_API_KEY_HERE # Jellyfin -> Administration -> API Keys
  user_id: <user-id> # Required for resume/recent/latest/favorites

# --------------------------------------------------------------------
# Radarr (movies)
# --------------------------------------------------------------------
radarr:
  url: https://example.com # replace with your instance URL
  api_key: YOUR_API_KEY_HERE # Radarr Settings -> General -> API Key

# --------------------------------------------------------------------
# Sonarr (TV)
# --------------------------------------------------------------------
sonarr:
  url: https://example.com # replace with your instance URL
  api_key: YOUR_API_KEY_HERE # Sonarr Settings -> General -> API Key

# --------------------------------------------------------------------
# Maintainerr (collection cleanup)
# Defaults to NO AUTH. The CLI emits a stderr warning on each run
# reminding the operator that this endpoint must be reachable only on
# a trusted/private network. Set auth_enabled: true and fill in extra
# headers below to enable auth (for example via a reverse proxy).
# --------------------------------------------------------------------
maintainerr:
  url: https://example.com # replace with your instance URL
  auth_enabled: false # set to true if behind an auth proxy
  extra: # only used when auth_enabled is true
    # Authorization: "Bearer YOUR_API_KEY_HERE"
    # X-Custom-Header: YOUR_API_KEY_HERE

# --------------------------------------------------------------------
# Seer (media requests)
# --------------------------------------------------------------------
seerr:
  url: https://example.com # replace with your instance URL
  api_key: YOUR_API_KEY_HERE # Seerr Settings -> General -> API Key
```

The committed `arr.conf.example` contains a TOML-only equivalent at the
bottom of the file. Both formats are accepted at runtime.

> **Do not commit a real arr.conf. Real keys go in a local file only;
> `arr.conf.example` ships with placeholders.**

---

## 4. Per-service command tables

All commands are HTTP `GET`. Search terms, ids, dates, and tmdb ids are
percent-encoded by the facade; the tables below show the canonical path
shape (decoded).

### 4.1 Jellyfin (`jellyfin` — 8 commands)

| Command                   | HTTP | Path                                                                                       | Notes                                                                                                                                                                                                                                                                                                           |
| ------------------------- | :--: | ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `jellyfin now`            | GET  | `/Sessions`                                                                                | All active sessions across users. Default output is the curated 13-key session summary (`user`, `device`, `client`, `playing.type`, `playing.name`, `playing.series`, `playing.season`, `playing.episode`, `progress.position_ticks`, `progress.is_paused`, `LastActivityDate`, `NowPlayingQueue.length`, `transcoding`); pass `--verbose` for the verbatim payload.                                                                                                                                                                                                                                                                              |
| `jellyfin resume`         | GET  | `/Users/{user_id}/Items/Resume`                                                            | Requires `jellyfin.user_id`. Default output is the curated 8-key summary (`Name`, `Type`, `ProductionYear`, `SeriesName`, `UserData.PlaybackPositionTicks`, `UserData.PlayCount`, `UserData.PlayedPercentage`, `RunTimeTicks` formatted as `"<X>h <Y>m"`); pass `--verbose` for the verbatim payload. Type-filtered to `Movie` and `Episode` so Series / Season roll-up entries (which some Jellyfin versions return) do not inflate the table.                                                                                                                                                                    |
| `jellyfin recent`         | GET  | `/Users/{user_id}/Items?SortBy=DatePlayed&Filters=IsPlayed&includeItemTypes=Movie,Episode` | Requires `jellyfin.user_id`. `includeItemTypes=Movie,Episode` is required on Jellyfin v12 (without it the server returns a single episode instead of the rolled-up recent set). Default output is the curated 6-key summary (`Name`, `Type`, `ProductionYear`, `SeriesName`, `UserData.LastPlayedDate`, `UserData.PlayedPercentage`); pass `--verbose` for the verbatim payload.                                                                                                                                                                                                                                                                          |
| `jellyfin nextup`         | GET  | `/Shows/NextUp`                                                                            | `--limit` is client-side only (caps the renderer, never sent on the wire). `UserId` is always sent from `jellyfin.user_id` (required on v12+). Accepts `--start-index N` for wire-side pagination (forwarded as `StartIndex`). Default output is the curated 7-key summary (`Name`, `SeriesName`, `ParentIndexNumber`, `IndexNumber`, `PremiereDate`, `RunTimeTicks` formatted as `"<X>h <Y>m"`, `Overview`); pass `--verbose` for the verbatim payload.                                                                                                                                                                                                                  |
| `jellyfin latest`         | GET  | `/Users/{user_id}/Items/Latest`                                                            | Requires `jellyfin.user_id`. Default output is the curated 7-key summary (`Name`, `Type`, `ProductionYear`, `SeriesName`, `RunTimeTicks` formatted as `"<X>h <Y>m"`, `PremiereDate`, `Genres` comma-joined); pass `--verbose` for the verbatim payload. The slim DTO returned by this endpoint does not populate `DateCreated` on the operator's instance, so it is intentionally dropped from the summary; fetch the full item via `jellyfin item <id>` (`--verbose` flag).                                                                                                                                                                                                  |
| `jellyfin search <query>` | GET  | `/Items?searchTerm=<query>&Recursive=true`                                                 | `Recursive=true` is required on Jellyfin v10+/v12 — without it `/Items` returns the five library-root folders rather than matches. An empty query short-circuits client-side (returns `[]` without calling `/Items`), since `searchTerm=` combined with `Recursive=true` would otherwise walk the full library. Default `--human` table is `Name`, `Type`, `ProductionYear` (`SeriesName` dropped -- it is empty for most hits and wastes a column).                                                                                                                                                                                                       |
| `jellyfin item <id>`      | GET  | `/Items/{id}`                                                                              | 404 → exit code `4` with stderr naming the id. Size-to-summary command. Default output is the curated 8-key item summary (`Name`, `Type`, `ProductionYear`, `PremiereDate`, `RunTimeTicks` formatted as `"<X>h <Y>m"`, `Genres` comma-joined, `Overview`, `Status`); pass `--verbose` for the verbatim service payload. Empty `item_id` → exit code `1` (input-shape guard).                                                                                                                                                                                                                                                                                |
| `jellyfin favorites`      | GET  | `/Users/{user_id}/Items?Filters=IsFavorite`                                                | Requires `jellyfin.user_id`. `--limit` is client-side only (caps the renderer, never sent on the wire). Default output is the curated 7-key summary (`Id`, `Name`, `Type`, `ProductionYear`, `SeriesName`, `RunTimeTicks` formatted as `"<X>h <Y>m"`, `UserData.LastPlayedDate`); pass `--verbose` for the verbatim payload. `Id` is the leftmost column so a row can be piped straight into `jellyfin item <id>`.                                                                                                                                                                                                                                            |

### 4.2 Radarr (`radarr` — 6 commands)

| Command                         | HTTP | Path                                             | Notes                                                                                                                                                                                                                                                |
| ------------------------------- | :--: | ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `radarr calendar`               | GET  | `/api/v3/calendar`                               | No date range. Size-to-summary command. Default output is the curated 10-key row summary (`title`, `year`, `monitored`, `hasFile`, `genres`, `runtime` formatted as `"<X>h <Y>m"`, `collection.title`, `inCinemas`, `physicalRelease`, `digitalRelease`); pass `--verbose` for the verbatim payload. |
| `radarr calendar <start> [end]` | GET  | `/api/v3/calendar?start=<start>&end=<end>`       | Accepts ISO-8601 dates or datetimes; malformed input → exit `1` + stderr usage hint. Size-to-summary command. Default output is the curated 10-key row summary (`title`, `year`, `monitored`, `hasFile`, `genres`, `runtime` formatted as `"<X>h <Y>m"`, `collection.title`, `inCinemas`, `physicalRelease`, `digitalRelease`); pass `--verbose` for the verbatim payload. |
| `radarr wanted`                 | GET  | `/api/v3/wanted/missing`                         | Missing movies.                                                                                                                                                                                                                                      |
| `radarr queue`                  | GET  | `/api/v3/queue`                                  | Current download / import queue.                                                                                                                                                                                                                     |
| `radarr recent`                 | GET  | `/api/v3/history?includeMovie=true&pageSize=<N>` | Movie history via generic `/history`; include flag populates nested `movie.title` / `movie.year`. Uses `--page-size` (default `10`, bounds `[1, 1000]`). Size-to-summary command. Default output is the curated 8-key row summary (`title`, `year`, `sourceTitle`, `quality`, `language`, `client`, `eventType`, `date`); `quality` carries the release-quality display name string (e.g. `WEBDL-1080p`) resolved from the upstream release-quality envelope; `language` is the comma-joined names of the upstream `languages` array and `client` is resolved from `data.downloadClient`; pass `--verbose` for the verbatim payload. |
| `radarr lookup <term>`          | GET  | `/api/v3/movie/lookup?term=<term>`               | Percent-encoded by the facade. An empty `<term>` short-circuits client-side to `[]` (exit 0); forwarding `term=` to RadarrAPI returns `HTTP 503 Value cannot be null.` (exit 4) -- mirroring the `jellyfin search ""` / `seerr search ""` contracts. |
| `radarr movie`                  | GET  | `/api/v3/movie`                                  | List every movie in the library. Size-to-summary command. Default output is the curated 7-key row summary (`title`, `year`, `hasFile`, `genres`, `added`, `collection.title`, `studio`); `collection.title` carries the upstream collection title; pass `--verbose` for the verbatim payload. |
| `radarr movie <id>`             | GET  | `/api/v3/movie/{id}`                             | 404 → exit code `4` with stderr naming the id.                                                                                                                                                                                                       |

### 4.3 Sonarr (`sonarr` — 6 commands)

| Command                         | HTTP | Path                                                     | Notes                                                                                                                                                                                                                                                                              |
| ------------------------------- | :--: | -------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sonarr calendar`               | GET  | `/api/v3/calendar?includeSeries=true`                    | No date range. Size-to-summary command. Default output is the curated 9-key row summary (`title`, `seasonNumber`, `episodeNumber`, `airDate`, `series.title`, `monitored`, `hasFile`, `runtime` formatted as `"<X>h <Y>m"`, `overview` truncated to 80 chars); pass `--verbose` for the verbatim payload. The `includeSeries=true` flag populates the nested `series` envelope, so `series.title` carries the upstream series title. Paginated envelope is unwrapped defensively (mirrors `radarr calendar`). |
| `sonarr calendar <start> [end]` | GET  | `/api/v3/calendar?start=<start>&end=<end>&includeSeries=true` | Accepts ISO-8601 dates or datetimes; malformed input → exit `1` + stderr usage hint. Same summary shape as the open-ended form; the `includeSeries=true` flag populates the nested `series` envelope, so `series.title` carries the upstream series title. |
| `sonarr wanted`                 | GET  | `/api/v3/wanted/missing?includeSeries=true`              | Missing episodes. Size-to-summary command. Default output is the curated 9-key row summary (`title`, `seasonNumber`, `episodeNumber`, `airDate`, `monitored`, `hasFile`, `runtime` formatted as `"<X>h <Y>m"`, `overview` truncated to 80 chars, `series.title`); pass `--verbose` for the verbatim payload. The `includeSeries=true` flag populates the nested `series` envelope, so `series.title` carries the upstream series title. |
| `sonarr queue`                  | GET  | `/api/v3/queue?includeSeries=true&includeEpisode=true`   | Current download / import queue. Size-to-summary command. Default output is the curated 9-key row summary (`title`, `status`, `trackedDownloadStatus`, `size`, `sizeleft`, `series.title`, `episode`, `quality`, `timeLeft`); `quality` carries the release-quality display name string (e.g. `WEBDL-1080p`) resolved from the upstream release-quality envelope; pass `--verbose` for the verbatim payload. The `includeSeries=true` / `includeEpisode=true` flags populate the nested `series` / `episode` envelopes, so `series.title` and `episode` carry the upstream titles. `status` stays pre-change and is not re-appended at the tail; no `progress` column is invented. |
| `sonarr recent`                 | GET  | `/api/v3/history?includeSeries=true&includeEpisode=true` | TV history (NOT `/history/movie` like Radarr); include flags populate nested `series`/`episode` titles. Size-to-summary command. Default output is the curated row summary (`series.title`, `episode.title`, `eventType`, `date`, `sourceTitle`, `quality`, `language`, `client`); `quality` carries the release-quality display name string (e.g. `WEBDL-1080p`) resolved from the upstream release-quality envelope; `language` is the comma-joined names of the upstream `languages` array and `client` is resolved from `data.downloadClient`; pass `--verbose` for the verbatim payload. |
| `sonarr lookup <term>`          | GET  | `/api/v3/series/lookup?term=<term>`                      | Percent-encoded by the facade. An empty `<term>` short-circuits client-side to `[]` (exit 0); forwarding `term=` to SkyHook returns `HTTP 503 Object reference not set to an instance of an object.` (exit 4) -- mirroring the `jellyfin search ""` / `seerr search ""` contracts. Size-to-summary command. Default output is the curated 10-key candidate row (`title`, `year`, `tvdbId`, `tvMazeId`, `id`, `monitored`, `statistics` formatted as `"<file>/<total>"`, `genres` comma-joined, `overview` truncated to 80 chars, `status`); pass `--verbose` for the verbatim payload. |
| `sonarr series`                 | GET  | `/api/v3/series`                                         | List every series in the library. Size-to-summary command. Default output is the curated 10-key library row (`title`, `year`, `monitored`, `status`, `tvdbId`, `seasons`, `statistics` formatted as `"<file>/<total>"`, `genres` comma-joined, `firstAired`, `lastAired`); pass `--verbose` for the verbatim payload. |
| `sonarr series <id>`            | GET  | `/api/v3/series/{id}`                                    | 404 → exit code `4` with stderr naming the id. Size-to-summary command. Default output is the curated 9-key single-record summary (`title`, `year`, `runtime` formatted as `"<X>h <Y>m"`, `status`, `monitored`, `statistics` formatted as `"<file>/<total>"`, `genres` comma-joined, `firstAired`, `lastAired`); pass `--verbose` for the verbatim payload. The with-id branch dispatches on `("sonarr", "series_id")` via the `command=` kwarg so the renderer routes to `_summary_sonarr_series_id` instead of `_summary_sonarr_series` (mirrors the `radarr movie <id>` dispatch). |

### 4.4 Maintainerr (`maintainerr` — 3 commands)

Maintainerr ships with **no auth** by default. The facade logs a one-line
stderr warning on each invocation reminding the operator that the endpoint
must be reachable only on a trusted / private network, unless `--quiet` is
passed.

| Command               | HTTP | Path                            | Notes                                                                                                                                                                                                                                                                                                                                                                                       |
| --------------------- | :--: | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `maintainerr pending` | GET  | `/api/collections/overlay-data` | Pending collection overlays. Size-to-summary command. Default output is the curated 11-key single-row-per-collection summary (`title`, `mediaCount`, `deleteAfterDays`, `isOnHold`, `arrAction`, `addDate`, `mediaServerType`, `type`, `description` (truncated to 80 chars via `_truncate_overview`), `isActive`, `visibleOnHome`); pass `--verbose` for the verbatim payload. Missing fields default to `<null>` so the table stays well-formed on sparse upstream payloads. |
| `maintainerr storage` | GET  | `/api/storage-metrics`          | Storage metrics. Size-to-summary command. Default output is the curated 5-key per-volume summary (`mount`, `used`, `free`, `total`, `usedPercent`). The live upstream envelope is a dict: one row is emitted per `mounts[]` entry (`mount` = the mount `path`, `free` = `freeSpace`, `total` = `totalSpace`, `used` = `totalSpace - freeSpace`, `usedPercent` derived from those two), preceded by a `(totals)` row carrying the upstream `totals` aggregate verbatim (upstream deduplicates shared mounts, so the aggregate is not the sum of the rows). The legacy list envelope (an `instances[]` array, or the parent item's own keys) is still tolerated. Missing fields render as `<null>`. Pass `--verbose` for the verbatim payload. |
| `maintainerr health`  | GET  | `/api/health/ready`             | Readiness probe. Size-to-summary command. Default output is the curated single-line `OK` / `DEGRADED` summary (`status == "ok"` → `OK`; any other shape — `degraded` / `down` / unknown string, bare `False`, non-mapping — → `DEGRADED`). The legacy bare-boolean shape (`True` / `False`) is also tolerated and maps to `OK` / `DEGRADED`. Pass `--verbose` for the verbatim upstream payload (the documented `{status, uptimeSeconds, database, timestamp}` shape on newer Maintainerr releases). Exit `0` regardless of `OK` / `DEGRADED` (the operator reads the value). |

### 4.5 Seer (`seerr` — 13 commands)

| Command                                     | HTTP | Path                                                                          | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------------------------------------------- | :--: | ----------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `seerr requests`                            | GET  | `/api/v1/request`                                                             | All requests. Size-to-summary command. Default output is the curated 10-key row summary (`id`, `mediaType`, `tmdbId`, `tvdbId`, `type`, `status`, `createdAt`, `requestedByUsername`, `mediaAddedAt`, `seasonCount`); `seasonCount` is the item-level season count of the request (the nested `media` envelope does not carry it); pass `--verbose` for the verbatim payload. Missing fields default to `<null>` so the table stays well-formed on sparse upstream payloads. |
| `seerr request-count`                       | GET  | `/api/v1/request/count`                                                       | Aggregate request counts.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `seerr search <query>`                      | GET  | `/api/v1/search?query=<query>`                                                | Percent-encoded by the facade. Size-to-summary command. Default output is the curated 7-key candidate row (`id`, `title`, `mediaType`, `releaseDate`, `overview` truncated to 80 chars via `_truncate_overview`, `popularity`, `genreIds`); pass `--verbose` for the verbatim payload. Missing fields default to `<null>`.                                                                                                                                                                                                                                                                                                |
| `seerr available <query>`                   | GET  | `/api/v1/media?filter=available&take=1000`                                    | Positional `<query>` is accepted for backwards compatibility but is ignored -- upstream `/api/v1/media` records do not carry a title field. A non-empty query emits a stderr note; use `seerr search <query>` to match against titles. Size-to-summary command. Default output is the curated 7-key identity row (`id`, `mediaType`, `tmdbId`, `tvdbId`, `externalServiceSlug`, `status`, `mediaAddedAt`); the `title` cell is intentionally **never** projected -- the live `/api/v1/media` envelope carries identity fields only and does not populate a `title` field (verified directly on 2026-10-17 against the operator's Seer instance; this is the AGENTS.md §1 identity-only contract, not a regression). A regression test in `tests/unit/test_output.py` (named after the 2026-09-18 `seerr-available-no-title-filter` ticket) pins the contract so any future contributor who tries to add a `title` projection back sees a hard test failure. Pass `--verbose` for the verbatim payload. Missing fields default to `<null>`. |
| `seerr user`                                | GET  | `/api/v1/auth/me`                                                             | Auth self-check (the bare `/auth/me` resolves to the Next.js frontend SPA and returns HTML, not the API). Size-to-summary command. Default output is the curated 5-key projection (`username`, `email`, `userType`, `permissions` (decoded from the live `/api/v1/auth/me` integer bitfield into a sorted-by-bit comma-joined name list via `_decode_permissions_bitfield`; the documented map is `1=request, 2=admin, 4=manage_users, 8=manage_issues, 16=auto_approve, 32=view_users`, so `permissions=2 -> "admin"`, `permissions=3 -> "request, admin"`, `permissions=0 -> ""`), `requestCount`); pass `--verbose` for the verbatim payload. Non-integer `permissions` shapes (the historical Overseerr list / mapping / string, or future envelope drift) fall back to `<null>` per `AGENTS.md §4.1`. Missing fields default to `<null>`.                                                                                                            |
| `seerr tv <id>`                             | GET  | `/api/v1/tv/<id>?language=<LANG>`                                             | Size-to-summary command. Default output is the curated 14-key summary (`name`, `originalName`, `firstAirDate`, `lastAirDate`, `numberOfSeasons`, `numberOfEpisodes`, `genres` (comma-joined names), `networks` (comma-joined names), `overview` truncated to 80 chars via `_truncate_overview`, `status`, `mediaInfoStatus`, `criticsScore`, `audienceRating`, `audienceScore`); pass `--verbose` for the verbatim service payload. The three score keys are always present (JSON `null` without `--ratings`; populated from the upstream `/ratings` sub-resource when `--ratings` is set, after merging it). The `criticsScore` key is plural-with-s (the live RT Tomatometer percentage 0-100), `audienceRating` is a categorical string (e.g. `"Spilled"` / `"Upright"`) and `audienceScore` is the numeric RT audience score (0-100); the projection uses the live envelope shape verified on 2026-10-17.                                                                                                              |
| `seerr movie <id>`                          | GET  | `/api/v1/movie/<id>?language=<LANG>`                                          | Size-to-summary command. Default output is the curated 12-key summary (`title`, `originalTitle`, `releaseDate`, `runtime` (formatted as `"<X>h <Y>m"`), `genres` (comma-joined names), `tagline`, `overview` truncated to 80 chars via `_truncate_overview`, `status`, `mediaInfoStatus`, `criticsScore`, `audienceRating`, `audienceScore`); pass `--verbose` for the verbatim service payload. The three score keys are always present (JSON `null` without `--ratings`; populated from the upstream `/ratings` sub-resource when `--ratings` is set, after merging it); see the `seerr tv <id>` row for the RT score key names. Structural twin of `seerr tv <id>` (same flag surface, same ratings merge contract, same live-spec key names). |
| `seerr trending [MEDIA_TYPE] [TIME_WINDOW]` | GET  | `/api/v1/discover/trending?mediaType=<…>&timeWindow=<…>&language=<…>`         | `MEDIA_TYPE ∈ {movie, tv} (omit = all); TIME_WINDOW ∈ {day, week}, default week. Accepts optional `--language`. Size-to-summary command. Default output is the curated 6-key row (`id`, `title`, `mediaType`, `releaseDate`, `popularity`, `overview` truncated to 80 chars); pass `--verbose` for the verbatim payload. Missing fields default to `<null>`.                                                                                                                                                                                                                                                                                                                          |
| `seerr upcoming-movies`                     | GET  | `/api/v1/discover/movies/upcoming?page=<…>&language=<…>`                      | Paginated upcoming movie releases. Size-to-summary command. Default output is the curated 6-key row (`id`, `title`, `mediaType`, `releaseDate`, `popularity`, `overview` truncated to 80 chars); pass `--verbose` for the verbatim payload. Universal flags only; no positional media type (fixed at command level). Missing fields default to `<null>`.                                                                                                                                                                                                                                                                  |
| `seerr upcoming-tv`                         | GET  | `/api/v1/discover/tv/upcoming?page=<…>&language=<…>`                          | Paginated upcoming TV premieres. Size-to-summary command. Default output is the curated 6-key row (`id`, `title`, `mediaType`, `releaseDate`, `popularity`, `overview` truncated to 80 chars); pass `--verbose` for the verbatim payload. Universal flags only; no positional media type (fixed at command level). Missing fields default to `<null>`.                                                                                                                                                                                                                                                                        |
| `seerr discover-movies`                     | GET  | `/api/v1/discover/movies?genre=<id>&sortBy=<sortBy>&language=<LANG>&page=<N>` | Filterable movie discover; no CLI defaults ride the wire (the `sortBy=popularity.desc` / `language=en-US` / `page=1` triple shown above is the upstream's default behaviour, not what the CLI sends). Optional `--genre <id>` (int), `--sort <sortBy>`, `--language <code>`, `--page <n>` are forwarded only when set. `--limit` is client-side only (caps the renderer, never sent on the wire). Size-to-summary command. Default output is the curated 7-key candidate row (`id`, `title`, `mediaType`, `releaseDate`, `popularity`, `overview` truncated to 80 chars, `genreIds`); pass `--verbose` for the verbatim payload. Missing fields default to `<null>`.                                  |
| `seerr discover-tv`                         | GET  | `/api/v1/discover/tv?genre=<id>&sortBy=<sortBy>&language=<LANG>&page=<N>`     | Filterable TV discover; same flag surface as `discover-movies`. Structural twin; same "no CLI defaults ride the wire" contract. `--limit` is client-side only. Size-to-summary command. Default output is the curated 7-key candidate row (`id`, `title`, `mediaType`, `releaseDate`, `popularity`, `overview` truncated to 80 chars, `genreIds`); pass `--verbose` for the verbatim payload. Missing fields default to `<null>`.                                                                                                                                                                                                                  |
| `seerr genres [MEDIA_TYPE]`                 | GET  | `/api/v1/genres/<movie\|tv>`                                                  | TMDB genre list as `[{id, name}, ...]`. `MEDIA_TYPE ∈ {movie, tv}`, default `movie` (matches `seerr trending`'s positional-with-default pattern); any other value is rejected by argparse and surfaces as a `ConfigError` with exit code `1` (the CLI's documented `ConfigError` exit; argparse's internal exit `2` is mentioned in the stderr message but is not the CLI exit). Optional `--language <LANG>` forwards as `?language=<LANG>` when set. Pair with `seerr discover-movies --genre` / `seerr discover-tv --genre` to map a friendly genre name (e.g. `Sci-Fi`) to its TMDB integer id (e.g. `878`). |

---

## 5. Auth matrix

The facade injects exactly one auth header per request — no per-command
threading of credentials.

| Service       | Header                                  | Value source                                                                                                                                                    |
| ------------- | --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `jellyfin`    | `Authorization` (MediaBrowser envelope) | `jellyfin.api_key` in `arr.conf`, sent as `Token="…"` inside the `MediaBrowser Client="arr-cli", Device="arr-cli", DeviceId="<host>", Version="0.1.0"` envelope |
| `radarr`      | `X-Api-Key`                             | `radarr.api_key` in `arr.conf`                                                                                                                                  |
| `sonarr`      | `X-Api-Key`                             | `sonarr.api_key` in `arr.conf`                                                                                                                                  |
| `seerr`       | `X-Api-Key`                             | `seerr.api_key` in `arr.conf`                                                                                                                                   |
| `maintainerr` | (none by default)                       | When `maintainerr.auth_enabled = true`, every `maintainerr.extra` key/value pair is added as a header. Otherwise no `Authorization`-style header is sent.       |

Missing credentials for any service raise `AuthError` (exit code `2`) with a
stderr message naming the missing key. HTTP 401 / 403 from any service also
maps to `AuthError` (exit code `2`); `--debug` redacts header values to
`***<length>` so secrets never reach stderr even with full tracing on.

---

## 6. Install / invoke

Install in editable mode for development:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Each console script is invokable from any shell with a working `PATH` once
the package is installed:

```bash
jellyfin now --human
radarr wanted
sonarr calendar 2026-01-01 2026-01-31
maintainerr health
seerr user
```

Run `scripts/smoke.sh --dry-run` for a no-network grammar check across all
five CLIs; `scripts/smoke.sh --live` (gated behind `RUN_LIVE=1`) hits the
operator's instance with one command per service.

Integration tests under `tests/integration/` exercise the CLIs against a
live service instance. They are **skipped by default** and only run when
the test runner is invoked with `--run-integration` (pytest) or when
`ARR_RUN_INTEGRATION=1` is exported (unittest). To run them against your
own instance, export `ARR_LIVE_URL` and the per-service credentials
first:

```bash
export ARR_LIVE_URL=https://jellyfin.example.com
export ARR_LIVE_API_KEY=...
export ARR_LIVE_USER_ID=...   # jellyfin endpoints that need user_id
pytest tests/integration/ --run-integration
# or, with the Makefile:
make integration-test
```

Universal flags (every CLI):

| Flag                     | Description                                                                                                   |
| ------------------------ | ------------------------------------------------------------------------------------------------------------- |
| `--config <path>`        | Override the canonical config path for this invocation only.                                                  |
| `--debug` / `--no-debug` | Enable / disable the full traceback + redacted request/response log.                                          |
| `--quiet` / `--no-quiet` | Suppress informational stderr lines (e.g. the Maintainerr auth-disabled warning). Errors still surface.       |
| `--human` / `-h`         | Render tabular readable text instead of raw JSON on stdout.                                                   |
| `--verbose`              | Emit the verbatim service JSON payload instead of the curated summary (default for size-to-summary commands). |
| `--limit <int>`          | Page size for `--human` lists (default `20`).                                                                 |
| `--connect-timeout <s>`  | Override the config's `connect_timeout` (default `5.0`).                                                      |
| `--read-timeout <s>`     | Override the config's `read_timeout` (default `30.0`).                                                        |
| `--retry <int>`          | Number of retry attempts on `NetworkError` (default `0`).                                                     |
| `--deadline <s>`         | Absolute wall-clock cap for `--retry`, in seconds.                                                            |

Run any subcommand with `--help` for the per-service synopsis and flags.

---

## 7. Exit codes

Every CLI returns one of five stable exit codes.

| Code | Class          | Trigger                                                              | Example stderr                                                             |
| ---: | -------------- | -------------------------------------------------------------------- | -------------------------------------------------------------------------- |
|  `1` | `ConfigError`  | Missing config, bad perms, unknown format, malformed CLI date input. | `radarr: calendar — invalid date 'next-tuesday'; expected ISO-8601`        |
|  `2` | `AuthError`    | HTTP 401 / 403 from the service, or missing required credential.     | `jellyfin: op=now — 401 Unauthorized; check jellyfin.api_key in arr.conf`  |
|  `3` | `NetworkError` | DNS failure, connection refused, TLS error, or timeout.              | `radarr: op=calendar url=https://radarr.example/api/v3/calendar — Timeout` |
|  `4` | `HttpError`    | HTTP 4xx (non-auth) or 5xx from the service.                         | `sonarr: op=series id=42 status=404 message=Series not found`              |
|  `5` | `ParseError`   | Response body is not valid JSON.                                     | `seerr: op=user — invalid JSON at byte offset 17`                          |

Diagnostics always flow through stderr; a consumer redirecting only stdout
receives a clean JSON document with no interspersed log lines. Python
tracebacks are printed only when `--debug` is set.
