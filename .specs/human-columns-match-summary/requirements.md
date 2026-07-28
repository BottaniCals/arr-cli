# Requirements Document

## Introduction

PR #6 on branch `fix/human-tracks-default-shape` landed the correct
renderer-selection logic in `arr_cli.facade.output.emit`:
`summarize(service, command, payload)` runs before `human()` whenever
`human_mode=True, verbose_mode=False`, so the data shape feeding the
`--human` table now matches the no-flag JSON. QA against Renald's live
environment (2026-07-28) verified that routing is correct.

What PR #6 did **not** touch is the per-service `columns = [...]`
blocks in `arr_cli/{jellyfin,radarr,sonarr,maintainerr,seerr}.py`.
Those blocks were written against the **verbatim service payload**
shape (`NowPlayingItem.Name`, `DeviceName`, `UserName`,
`UserData.PlaybackPositionTicks`, …) and were never updated when the
summary renderers in `arr_cli.facade.output._SUMMARY_RENDERERS`
started producing a different curated shape (`user`, `device`, `client`,
`playing.type`, `playing.name`, `playing.series`, `progress.position_ticks`,
`mediaInfo.tmdbId`, …). Because `human()` projects each row onto the
named columns via `item.get(column)`, every cell in the table is the
literal string `"<null>"` for fields the summary renderer no longer
emits under that exact key.

The curated summary shape is split two ways across the 15 size-to-summary
commands:

- **9 commands with flat summary keys** — the renderer emits a flat
  dict at the top level (e.g. `_summary_jellyfin_favorites` →
  `{"Name", "Type", "ProductionYear", "SeriesName"}`). For these,
  `columns = [...]` simply needs to be the match-the-summary-keys
  literal. The current blocks already align for 8 of the 9 (after the
  fixes in this feature); the 9th — `maintainerr -h pending` — is
  **already aligned** with the summary shape (`["title", "mediaCount",
  "deleteAfterDays", "isOnHold"]` matches `_summary_maintainerr_pending`
  exactly) and is therefore covered by the regression net but does
  NOT require a `columns = [...]` rewrite.
- **6 commands with nested summary keys** — the renderer emits the
  value under a nested dict (e.g. `_summary_jellyfin_now` emits
  `playing: {type: ..., name: ...}`, `_summary_seerr_search` emits
  `mediaInfo: {tmdbId: ...}`). For these, `columns = [...]` uses
  dot-path tokens (`playing.type`, `mediaInfo.tmdbId`) that map to
  the nested summary fields. The `human()` renderer in
  `arr_cli/facade/output.py` currently calls `item.get(column)` —
  a flat `dict.get` — which would treat `"playing.type"` as a single
  flat key and return `None` for every cell, perpetuating the bug.
  A small targeted change to `human._row_from_mapping` adds
  dot-path traversal so dot-path column tokens resolve correctly
  against the nested summary shape. The non-dotted path remains a
  strict subset (no behavior change for the 9 flat commands).

The 6 nested-summary-shape commands:

1. `jellyfin -h now` — `playing.*`, `progress.*` (e.g. `playing.type`,
   `progress.position_ticks`)
2. `radarr -h recent` — `movie.title`, `movie.year`
3. `sonarr -h recent` — `series.title`, `episode.title`
4. `seerr -h requests` — `requestedBy.displayName`
5. `seerr -h search` — `mediaInfo.tmdbId`
6. `seerr -h available` — `mediaInfo.status`

The 15 size-to-summary commands, with the 14 that get a
`columns = [...]` rewrite:

- `jellyfin`: `now` (dot-path), `resume` (flat), `recent` (flat),
  `latest` (flat), `favorites` (flat) — 5 to fix
- `radarr`: `wanted` (flat, `movieFile` removed), `queue` (flat),
  `recent` (dot-path) — 3 to fix
- `sonarr`: `wanted` (flat), `queue` (flat), `recent` (dot-path) — 3 to fix
- `maintainerr`: `pending` (flat, **already aligned**, no rewrite
  required) — 0 to fix, covered by regression net
- `seerr`: `requests` (dot-path), `search` (dot-path),
  `available` (dot-path) — 3 to fix

Total: 14 commands rewritten, 1 verified to already align, 15 covered
by the regression net, 15 size-to-summary candidates total.

Out of scope: the renderer-selection fix in
`arr_cli.facade.output.emit` (PR #6 — already correct, do not change),
the curated summary shape itself (the 17 `_summary_*` renderer bodies
remain unchanged), the dispatch table `_SUMMARY_RENDERERS` (same 15
entries), any new commands or new tabular columns, and SKILL.md
cleanup in Sage / Lily workspaces (the grep for `known-issue` /
`workaround` / "does not currently match its intended design" returned
zero matches; nothing to drop).

In scope: the targeted change to `human._row_from_mapping` in
`arr_cli/facade/output.py` for dot-path traversal. This is NOT PR #6's
surface area — it is the `human()` renderer internals, not the
`emit()` routing logic.

Value to users: `jellyfin -h now`, `radarr -h wanted`,
`sonarr -h recent`, `seerr -h requests`, `maintainerr -h pending`, and
the other ten size-to-summary `--human` paths render a table whose
columns describe the same data the no-flag JSON describes, so the
table and the JSON are interchangeable reading aids rather than two
windows onto two different shapes.

## Requirements

### Requirement 1

**User Story:** As a self-hosted media stack operator, I want `jellyfin -h now` to render a table whose columns come from the curated `now` summary shape (not from the verbatim `/Sessions` payload), so that every cell carries a real value instead of the literal string `<null>`.

#### Requirement 1 Acceptance Criteria

1. WHEN the user runs `jellyfin -h now` against a non-empty `/Sessions` payload THEN `arr_cli.jellyfin.cmd_now`'s `columns = [...]` block SHALL contain exactly `["user", "device", "client", "playing.type", "playing.name", "playing.series", "playing.season", "playing.episode", "progress.position_ticks", "progress.is_paused"]` — dot-path tokens matching the nested summary fields the `_summary_jellyfin_now` renderer emits (top-level keys plus nested-dict keys joined by `.`).
2. WHEN the user runs `jellyfin -h now` THEN `arr_cli.jellyfin.cmd_now`'s `columns = [...]` block SHALL NOT contain any of the verbatim-payload keys `DeviceName`, `UserName`, `Client`, `NowPlayingItem`, `NowPlayingItem.Name`, `NowPlayingItem.SeriesName`, `NowPlayingItem.Type`, `NowPlayingItem.ParentIndexNumber`, `NowPlayingItem.IndexNumber`, `PlayState`, `PlayState.PositionTicks`, or `PlayState.IsPaused`.
3. **Given** the dot-path traversal in Req 16 is in place, WHEN `arr_cli.facade.output.summarize("jellyfin", "now", payload)` produces the summary dict THEN `human(summary_payload, columns=[...])` SHALL produce a row for every column in the block — every rendered cell SHALL be either a populated value or the literal `<null>` only when the summary itself populated that key with `None`, and SHALL NOT be `<null>` for every cell across every row.

### Requirement 2

**User Story:** As a self-hosted media stack operator, I want `jellyfin -h resume` to render a table whose columns come from the curated `resume` summary shape, so that the progress fields shown are the flattened `UserData.PlaybackPositionTicks` and `UserData.PlayCount` tokens rather than the nested `UserData` object the verbatim payload carries.

#### Requirement 2 Acceptance Criteria

1. WHEN the user runs `jellyfin -h resume` THEN `arr_cli.jellyfin.cmd_resume`'s `columns = [...]` block SHALL contain exactly `["Name", "Type", "ProductionYear", "SeriesName", "UserData.PlaybackPositionTicks", "UserData.PlayCount"]` (the top-level keys of `_summary_jellyfin_resume`'s output, including the flattened `UserData.*` tokens the renderer emits as top-level keys with `.`).
2. WHEN the user runs `jellyfin -h resume` THEN `arr_cli.jellyfin.cmd_resume`'s `columns = [...]` block SHALL NOT contain the verbatim-payload token `UserData` (the summary renderer flattens the nested object; the literal `UserData` key is no longer present at the top level).
3. WHEN `arr_cli.facade.output.summarize("jellyfin", "resume", payload)` produces the summary list THEN `human(summary_payload, columns=[...])` SHALL produce a non-null cell for at least the `Name` and `Type` columns on every row whose source item supplies those fields.

### Requirement 3

**User Story:** As a self-hosted media stack operator, I want `jellyfin -h recent` to render a table whose columns come from the curated `recent` summary shape, so that the date column shown is the flattened `UserData.LastPlayedDate` token rather than the nested `UserData` object the verbatim payload carries.

#### Requirement 3 Acceptance Criteria

1. WHEN the user runs `jellyfin -h recent` THEN `arr_cli.jellyfin.cmd_recent`'s `columns = [...]` block SHALL contain exactly `["Name", "Type", "ProductionYear", "SeriesName", "UserData.LastPlayedDate"]` (the top-level keys of `_summary_jellyfin_recent`'s output, including the flattened `UserData.LastPlayedDate` token).
2. WHEN the user runs `jellyfin -h recent` THEN `arr_cli.jellyfin.cmd_recent`'s `columns = [...]` block SHALL NOT contain the verbatim-payload token `UserData`.
3. WHEN `arr_cli.facade.output.summarize("jellyfin", "recent", payload)` produces the summary list THEN `human(summary_payload, columns=[...])` SHALL populate the `UserData.LastPlayedDate` column with the flattened ISO date string the renderer emitted (or `<null>` only when the source item lacked `UserData.LastPlayedDate`).

### Requirement 4

**User Story:** As a self-hosted media stack operator, I want `jellyfin -h latest` to render a table whose columns come from the curated `latest` summary shape, so that the published column matches the summary's flattened `DateCreated` token rather than a verbatim-payload key that happens to share the same name today.

#### Requirement 4 Acceptance Criteria

1. WHEN the user runs `jellyfin -h latest` THEN `arr_cli.jellyfin.cmd_latest`'s `columns = [...]` block SHALL contain exactly `["Name", "Type", "ProductionYear", "SeriesName", "DateCreated"]` (the top-level keys of `_summary_jellyfin_latest`'s output).
2. WHEN the user runs `jellyfin -h latest` THEN `arr_cli.jellyfin.cmd_latest`'s `columns = [...]` block SHALL NOT contain any verbatim-only keys the summary renderer does not emit (e.g. `Overview`, `Genres`, `RunTimeTicks`).
3. WHEN the columns block changes (or the summary renderer changes) THEN the regression net in `tests/unit/test_output.py` SHALL fail visibly if any column key in `cmd_latest`'s block is no longer a key the summary renderer emits (top-level or dot-joined nested).

### Requirement 5

**User Story:** As a self-hosted media stack operator, I want `jellyfin -h favorites` to render a table whose columns come from the curated `favorites` summary shape, so that the column list is explicitly pinned to the summary's top-level keys rather than implicitly inheriting whatever happened to match the verbatim shape today.

#### Requirement 5 Acceptance Criteria

1. WHEN the user runs `jellyfin -h favorites` THEN `arr_cli.jellyfin.cmd_favorites`'s `columns = [...]` block SHALL contain exactly `["Name", "Type", "ProductionYear", "SeriesName"]` (the top-level keys of `_summary_jellyfin_favorites`'s output).
2. WHEN the user runs `jellyfin -h favorites` THEN `arr_cli.jellyfin.cmd_favorites`'s `columns = [...]` block SHALL NOT contain any verbatim-only keys the summary renderer does not emit (e.g. `Overview`, `Genres`, `RunTimeTicks`, `Taglines`).
3. WHEN the columns block changes (or the summary renderer changes) THEN the regression net in `tests/unit/test_output.py` SHALL fail visibly if any column key in `cmd_favorites`'s block is no longer a key the summary renderer emits (top-level or dot-joined nested).

### Requirement 6

**User Story:** As a self-hosted media stack operator, I want `radarr -h wanted` to render a table whose columns come from the curated `wanted` summary shape, so that the rendered header lists `title`, `year`, `tmdbId`, `monitored` and no longer references the verbatim-payload `movieFile` key the summary drops.

#### Requirement 6 Acceptance Criteria

1. WHEN the user runs `radarr -h wanted` THEN `arr_cli.radarr.cmd_wanted`'s `columns = [...]` block SHALL contain exactly `["title", "year", "tmdbId", "monitored"]` (the top-level keys of `_summary_radarr_wanted`'s output).
2. WHEN the user runs `radarr -h wanted` THEN `arr_cli.radarr.cmd_wanted`'s `columns = [...]` block SHALL NOT contain the verbatim-payload token `movieFile` (the summary renderer does not emit that key).
3. WHEN `arr_cli.facade.output.summarize("radarr", "wanted", payload)` produces the summary list THEN `human(summary_payload, columns=[...])` SHALL populate the `tmdbId` column with the integer value the summary emitted (or `0` when the source item lacked a TMDB id).

### Requirement 7

**User Story:** As a self-hosted media stack operator, I want `radarr -h queue` to render a table whose columns come from the curated `queue` summary shape, so that the column list is explicitly pinned to the summary's top-level keys.

#### Requirement 7 Acceptance Criteria

1. WHEN the user runs `radarr -h queue` THEN `arr_cli.radarr.cmd_queue`'s `columns = [...]` block SHALL contain exactly `["title", "status", "trackedDownloadStatus", "size", "sizeleft"]` (the top-level keys of `_summary_radarr_queue`'s output).
2. WHEN the user runs `radarr -h queue` THEN `arr_cli.radarr.cmd_queue`'s `columns = [...]` block SHALL NOT contain any verbatim-only keys the summary renderer does not emit (e.g. `downloadClient`, `indexer`, `protocol`).
3. WHEN the columns block changes (or the summary renderer changes) THEN the regression net in `tests/unit/test_output.py` SHALL fail visibly if any column key in `cmd_queue`'s block is no longer a key the summary renderer emits (top-level or dot-joined nested).

### Requirement 8

**User Story:** As a self-hosted media stack operator, I want `radarr -h recent` to render a table whose columns come from the curated `recent` summary shape, so that the rendered header references the summary's nested `movie.title` and `movie.year` tokens rather than the verbatim-payload keys.

#### Requirement 8 Acceptance Criteria

1. WHEN the user runs `radarr -h recent` THEN `arr_cli.radarr.cmd_recent`'s `columns = [...]` block SHALL contain exactly `["movie.title", "movie.year", "eventType", "date"]` — dot-path tokens matching the nested summary fields the `_summary_radarr_recent` renderer emits (top-level keys plus nested-dict keys joined by `.`).
2. WHEN the user runs `radarr -h recent` THEN `arr_cli.radarr.cmd_recent`'s `columns = [...]` block SHALL NOT contain any verbatim-only keys the summary renderer does not emit (e.g. `sourcePath`, `downloadClient`, `quality`).
3. **Given** the dot-path traversal in Req 16 is in place, WHEN `arr_cli.facade.output.summarize("radarr", "recent", payload)` produces the summary list THEN `human(summary_payload, columns=[...])` SHALL populate the `movie.title` column with the string the nested `movie.title` summary field carries (or `<null>` only when the source item lacked a `movie.title`).

### Requirement 9

**User Story:** As a self-hosted media stack operator, I want `sonarr -h wanted` to render a table whose columns come from the curated `wanted` summary shape, so that the rendered header lists `title`, `seasonNumber`, `episodeNumber`, `airDate`, `monitored` and no longer references the verbatim-payload `seriesId` key the summary drops.

#### Requirement 9 Acceptance Criteria

1. WHEN the user runs `sonarr -h wanted` THEN `arr_cli.sonarr.cmd_wanted`'s `columns = [...]` block SHALL contain exactly `["title", "seasonNumber", "episodeNumber", "airDate", "monitored"]` (the top-level keys of `_summary_sonarr_wanted`'s output).
2. WHEN the user runs `sonarr -h wanted` THEN `arr_cli.sonarr.cmd_wanted`'s `columns = [...]` block SHALL NOT contain the verbatim-payload token `seriesId` (the summary renderer does not emit that key).
3. WHEN `arr_cli.facade.output.summarize("sonarr", "wanted", payload)` produces the summary list THEN `human(summary_payload, columns=[...])` SHALL populate the `seasonNumber` and `episodeNumber` columns with the integer values the summary emitted (or `0` when the source item lacked those fields).

### Requirement 10

**User Story:** As a self-hosted media stack operator, I want `sonarr -h queue` to render a table whose columns come from the curated `queue` summary shape, so that the column list is explicitly pinned to the summary's top-level keys.

#### Requirement 10 Acceptance Criteria

1. WHEN the user runs `sonarr -h queue` THEN `arr_cli.sonarr.cmd_queue`'s `columns = [...]` block SHALL contain exactly `["title", "status", "trackedDownloadStatus", "size", "sizeleft"]` (the top-level keys of `_summary_sonarr_queue`'s output).
2. WHEN the user runs `sonarr -h queue` THEN `arr_cli.sonarr.cmd_queue`'s `columns = [...]` block SHALL NOT contain any verbatim-only keys the summary renderer does not emit (e.g. `downloadClient`, `indexer`, `protocol`).
3. WHEN the columns block changes (or the summary renderer changes) THEN the regression net in `tests/unit/test_output.py` SHALL fail visibly if any column key in `cmd_queue`'s block is no longer a key the summary renderer emits (top-level or dot-joined nested).

### Requirement 11

**User Story:** As a self-hosted media stack operator, I want `sonarr -h recent` to render a table whose columns come from the curated `recent` summary shape, so that the rendered header references the summary's nested `series.title` and `episode.title` tokens rather than the verbatim-payload keys.

#### Requirement 11 Acceptance Criteria

1. WHEN the user runs `sonarr -h recent` THEN `arr_cli.sonarr.cmd_recent`'s `columns = [...]` block SHALL contain exactly `["series.title", "episode.title", "eventType", "date"]` — dot-path tokens matching the nested summary fields the `_summary_sonarr_recent` renderer emits (top-level keys plus nested-dict keys joined by `.`).
2. WHEN the user runs `sonarr -h recent` THEN `arr_cli.sonarr.cmd_recent`'s `columns = [...]` block SHALL NOT contain any verbatim-only keys the summary renderer does not emit (e.g. `sourcePath`, `downloadClient`, `quality`).
3. **Given** the dot-path traversal in Req 16 is in place, WHEN `arr_cli.facade.output.summarize("sonarr", "recent", payload)` produces the summary list THEN `human(summary_payload, columns=[...])` SHALL populate the `series.title` and `episode.title` columns with the strings the nested summary fields carry (or `<null>` only when the source item lacked those nested fields).

### Requirement 12

**User Story:** As a self-hosted media stack operator, I want `seerr -h requests` to render a table whose columns come from the curated `requests` summary shape, so that the requester column references the summary's nested `requestedBy.displayName` token rather than the verbatim-payload keys.

#### Requirement 12 Acceptance Criteria

1. WHEN the user runs `seerr -h requests` THEN `arr_cli.seerr.cmd_requests`'s `columns = [...]` block SHALL contain exactly `["title", "type", "status", "createdAt", "requestedBy.displayName"]` — dot-path tokens matching the nested summary fields the `_summary_seerr_requests` renderer emits (top-level keys plus nested-dict keys joined by `.`).
2. WHEN the user runs `seerr -h requests` THEN `arr_cli.seerr.cmd_requests`'s `columns = [...]` block SHALL NOT contain any verbatim-only keys the summary renderer does not emit (e.g. `externalId`, `mediaId`, `seasonNumber`).
3. **Given** the dot-path traversal in Req 16 is in place, WHEN `arr_cli.facade.output.summarize("seerr", "requests", payload)` produces the summary list THEN `human(summary_payload, columns=[...])` SHALL populate the `requestedBy.displayName` column with the string the nested summary field carries (or `<null>` only when the source item lacked a `requestedBy.displayName`).

### Requirement 13

**User Story:** As a self-hosted media stack operator, I want `seerr -h search` to render a table whose columns come from the curated `search` summary shape, so that the TMDB id column references the summary's nested `mediaInfo.tmdbId` token rather than the verbatim-payload keys.

#### Requirement 13 Acceptance Criteria

1. WHEN the user runs `seerr -h search` THEN `arr_cli.seerr.cmd_search`'s `columns = [...]` block SHALL contain exactly `["title", "mediaType", "releaseDate", "mediaInfo.tmdbId"]` — dot-path tokens matching the nested summary fields the `_summary_seerr_search` renderer emits (top-level keys plus nested-dict keys joined by `.`).
2. WHEN the user runs `seerr -h search` THEN `arr_cli.seerr.cmd_search`'s `columns = [...]` block SHALL NOT contain any verbatim-only keys the summary renderer does not emit (e.g. `overview`, `posterPath`, `backdropPath`).
3. **Given** the dot-path traversal in Req 16 is in place, WHEN `arr_cli.facade.output.summarize("seerr", "search", payload)` produces the summary list THEN `human(summary_payload, columns=[...])` SHALL populate the `mediaInfo.tmdbId` column with the integer value the nested summary field carries (or `0` when the source item lacked a `mediaInfo.tmdbId`).

### Requirement 14

**User Story:** As a self-hosted media stack operator, I want `seerr -h available` to render a table whose columns come from the curated `available` summary shape, so that the status column references the summary's nested `mediaInfo.status` token rather than the verbatim-payload keys.

#### Requirement 14 Acceptance Criteria

1. WHEN the user runs `seerr -h available` THEN `arr_cli.seerr.cmd_available`'s `columns = [...]` block SHALL contain exactly `["title", "mediaType", "releaseDate", "mediaInfo.status"]` — dot-path tokens matching the nested summary fields the `_summary_seerr_available` renderer emits (top-level keys plus nested-dict keys joined by `.`).
2. WHEN the user runs `seerr -h available` THEN `arr_cli.seerr.cmd_available`'s `columns = [...]` block SHALL NOT contain any verbatim-only keys the summary renderer does not emit (e.g. `overview`, `posterPath`, `backdropPath`).
3. **Given** the dot-path traversal in Req 16 is in place, WHEN `arr_cli.facade.output.summarize("seerr", "available", payload)` produces the summary list THEN `human(summary_payload, columns=[...])` SHALL populate the `mediaInfo.status` column with the integer value the nested summary field carries (or `0` when the source item lacked a `mediaInfo.status`).

### Requirement 15

**User Story:** As a self-hosted media stack operator, I want `maintainerr -h pending` to remain covered by the regression net even though its columns block is already aligned, so that future drift in the `_summary_maintainerr_pending` renderer trips `make ci` rather than re-discovered in a live QA pass.

#### Requirement 15 Acceptance Criteria

1. WHEN the user runs `maintainerr -h pending` THEN `arr_cli.maintainerr.cmd_pending`'s `columns = [...]` block SHALL remain exactly `["title", "mediaCount", "deleteAfterDays", "isOnHold"]` (the top-level keys of `_summary_maintainerr_pending`'s output). The block is **already aligned** with the summary shape; the contributor SHALL NOT rewrite it because it is already correct.
2. WHEN the contributor lands this feature THEN `arr_cli.maintainerr.cmd_pending`'s `columns = [...]` block SHALL NOT be modified by the diff (the existing literal is the source of truth for the summary-aligned columns).
3. WHEN `arr_cli.facade.output.summarize("maintainerr", "pending", payload)` produces the summary list THEN `human(summary_payload, columns=[...])` SHALL populate the `mediaCount`, `deleteAfterDays`, and `isOnHold` columns with the typed values the summary emitted (integers for the counts, `true`/`false` for the bool) — and the regression net in Req 18 SHALL pin this behaviour so a future change to `_summary_maintainerr_pending` that drops a key trips the test.

### Requirement 16

**User Story:** As a self-hosted media stack operator, I want the `human()` renderer to resolve dot-path column tokens against nested summary fields, so that the 6 nested-shape size-to-summary commands (`jellyfin -h now`, `radarr -h recent`, `sonarr -h recent`, `seerr -h requests`, `seerr -h search`, `seerr -h available`) render populated cells from the curated summary instead of treating `"playing.type"` as a single flat key and returning `<null>` for every row.

#### Requirement 16 Acceptance Criteria

1. WHEN `human()` renders a list-of-mapping payload and a column token contains one or more `.` characters THEN `human._row_from_mapping` in `arr_cli/facade/output.py` SHALL walk the dot-separated path against the item: for a mapping at each step, take `item[segment]` (i.e. subscript the mapping with the string segment); for a sequence at each step, take `item[int(segment)]` (parse the segment as an integer and subscript the sequence); on any `KeyError`, `IndexError`, or `TypeError` encountered along the walk, return `None` (rendered as the literal `<null>` by `_stringify`).
2. WHEN `human()` renders a list-of-mapping payload and a column token does NOT contain a `.` THEN `human._row_from_mapping` SHALL behave identically to the pre-change flat-key lookup (`item.get(column)`) — the non-dotted path is a strict subset, no behavior change for the 9 flat-summary commands.
3. WHEN a unit test calls `human([{"a": {"b": 1}}], columns=["a.b"])` THEN the rendered table SHALL contain the string `1` in the `a.b` cell (asserting that the dot-path traversal reaches the nested integer value).
4. WHEN a unit test calls `human([{"a": {"b": 1}}, {"a": None}], columns=["a.b"])` THEN the second row's `a.b` cell SHALL be `<null>` (asserting that the `TypeError` path on a `None` intermediate returns `None` and renders as `<null>`).
5. WHEN a unit test calls `human([{"a": {"b": 1}}], columns=["a"])` THEN the rendered table SHALL contain the literal string `1 keys` (or equivalent mapping summary) in the `a` cell — confirming that a non-dotted column token still returns the value at the top level (no regression for flat columns).
6. WHEN a unit test calls `human([{"playing": {"type": "Episode"}}], columns=["playing.type"])` THEN the rendered table SHALL contain the string `Episode` in the `playing.type` cell — a positive end-to-end check that mirrors the `jellyfin -h now` summary shape.
7. WHEN the contributor lands this feature THEN `human._row_from_mapping` SHALL be the only function in `arr_cli/facade/output.py` whose body changes for dot-path traversal; the surrounding `human()` function body, the `emit()` function body, the `_SUMMARY_RENDERERS` dispatch table, and the 17 `_summary_*` renderer bodies SHALL NOT change as part of this fix.

### Requirement 17

**User Story:** As a self-hosted media stack operator, I want the column alignment to keep working for the verbatim-only and escape-hatch paths (no regression), so that `radarr -h calendar` still renders the documented columns and `jellyfin --verbose -h now` still renders the verbatim `/Sessions` columns.

#### Requirement 17 Acceptance Criteria

1. WHEN the user runs `radarr -h calendar` THEN `arr_cli.radarr.cmd_calendar`'s `columns = [...]` block SHALL remain exactly `["title", "year", "inCinemas", "physicalRelease", "digitalRelease"]` — i.e. the contributor SHALL NOT touch the calendar columns block because `cmd_calendar` is not a size-to-summary candidate and its columns describe the verbatim payload by design.
2. WHEN the user runs `jellyfin --verbose -h now` THEN `arr_cli.facade.output.emit` SHALL continue to render `human(verbatim_payload, columns=...)` (the `verbose_mode=True` branch bypasses `summarize()` and forwards the verbatim payload straight into `human()`), and the resulting table SHALL include the verbatim `/Sessions` columns (`DeviceName`, `UserName`, `NowPlayingItem.Name`, `NowPlayingItem.SeriesName`, `PlayState`) as the existing `TestEmitHumanVerboseEscapeHatch` test already pins.
3. WHEN the contributor edits any of the per-service CLI files THEN the contributor SHALL NOT touch `cmd_calendar`, `cmd_lookup`, `cmd_movie`, `cmd_nextup`, `cmd_search`, `cmd_item`, `cmd_request_count`, `cmd_media`, `cmd_user`, `cmd_storage`, or `cmd_health` because those commands are either non-summary or summary-irrelevant and their existing columns blocks (or `columns=None`) describe the verbatim payload by design.
4. WHEN the contributor edits `arr_cli/facade/output.py` THEN the contributor SHALL NOT change: the renderer-selection fix in `arr_cli.facade.output.emit` from PR #6; the dispatch table `_SUMMARY_RENDERERS` (same 15 entries); any of the 17 `_summary_*` renderer bodies' output shape. The targeted change to `human._row_from_mapping` for dot-path traversal (Req 16) IS permitted.

### Requirement 18

**User Story:** As a self-hosted media stack operator, I want `tests/unit/test_output.py` to gain a regression net that pins the alignment between the 15 per-service `columns = [...]` blocks (across the 14 rewritten + 1 already-aligned) and the `_SUMMARY_RENDERERS` summary shapes, so that future drift trips `make ci` rather than re-emerging as a live-only bug.

#### Requirement 18 Acceptance Criteria

1. WHEN the contributor lands the columns-block fixes THEN `tests/unit/test_output.py` SHALL add a class (e.g. `TestColumnsBlockMatchesSummaryShape`) that, for every `(service, command)` key registered in `arr_cli.facade.output._SUMMARY_RENDERERS`, asserts that every column in the corresponding handler's `columns = [...]` block is either equal to or a substring of a key the summary renderer emits for that key, where the emitted keys are the union of: (a) top-level keys of the summary shape, and (b) nested-dict keys, dot-joined with the parent key (e.g. summary key `playing.type` corresponds to flat `playing` + `.` + `type`). The contributor SHALL source the per-handler `columns` list from the imported per-service module (e.g. by importing `arr_cli.jellyfin` and reaching into the handler's local `columns = [...]` literal via inspection or by extracting the literal via `ast`).
2. WHEN the contributor lands the columns-block fixes THEN `tests/unit/test_output.py` SHALL add a class (e.g. `TestHumanRendersNonNullRowsForSizeToSummary`) that, for every `(service, command)` key registered in `arr_cli.facade.output._SUMMARY_RENDERERS`, constructs a synthetic payload that populates the *summary* shape (run the actual `_summary_<svc>_<cmd>` renderer on a minimal realistic input), then runs `emit(synthetic_payload_input, human_mode=True, verbose_mode=False, service=svc, command=cmd, stream=...)`, parses the rendered table, and asserts that for each column in the handler's `columns = [...]` block, at least one data row's cell for that column is NOT `<null>` (i.e. the summary-shape data reaches the cell). The synthetic payload SHALL include at least one primitive (string / int / float / bool) value per summary key so the assertion has a non-vacuous expectation.
3. WHEN the contributor lands the columns-block fixes THEN `tests/unit/test_output.py` SHALL add a class (e.g. `TestDotPathTraversal`) that pins the dot-path traversal behaviour of `human._row_from_mapping` (per Req 16): at minimum the four assertions from Req 16 AC3-AC6 — `human([{"a": {"b": 1}}], columns=["a.b"])` produces `1`; `human([{"a": {"b": 1}}, {"a": None}], columns=["a.b"])` produces `<null>` for the second row; `human([{"a": {"b": 1}}], columns=["a"])` produces a mapping-summary cell; `human([{"playing": {"type": "Episode"}}], columns=["playing.type"])` produces `Episode`.
4. WHEN the contributor lands the columns-block fixes THEN `tests/unit/test_output.py` SHALL add a docstring note (or class-level comment) on the new regression classes documenting the rationale ("for each `_SUMMARY_RENDERERS` key, every column key in the corresponding handler's `columns = [...]` block is a substring of (or equal to) a top-level key OR a dot-joined nested-dict key in the summary shape — so future drift trips the test") so future maintainers understand the seam.
5. WHEN the new tests run THEN `make ci` SHALL pass (== `make test && make secret-scan && make smoke-dry`) and SHALL remain hermetic — only `responses` and in-process mocks, no live HTTP, no live service credentials.
6. WHEN the new tests run THEN `make lint` SHALL pass (two-space indent, Python ≥3.11 syntax, type hints on public functions per `AGENTS.md §4.1`).
7. WHEN a future change adds a key to `_summary_<service>_<command>` without updating the corresponding `columns = [...]` block THEN at least one of the new unit tests SHALL fail visibly (substring mismatch), pinpointing the drift.
8. WHEN a future change adds a key to a `columns = [...]` block that the corresponding summary renderer does not emit (top-level or dot-joined nested) THEN at least one of the new unit tests SHALL fail visibly, pinpointing the drift.

## Non-Functional Requirements

### Performance

- The columns-block fix SHALL NOT add any new pass over the payload: it is a static literal edit in each of the 5 per-service CLI modules. No new runtime cost beyond the existing `summarize()` invocation (already pure and O(payload size)).
- The dot-path traversal added to `human._row_from_mapping` (Req 16) SHALL be O(d) per column lookup where d is the dot-path depth; in this feature d ≤ 2 for every column token, so the asymptotic cost is identical to the pre-change flat `item.get(column)` lookup. No new runtime cost beyond a single `str.split(".")` per column.
- The new unit tests SHALL each complete in well under a second (no live HTTP, no network calls); the regression net SHALL add at most a few hundred milliseconds to `make test`.
- No new runtime dependencies SHALL be added to `pyproject.toml` per `AGENTS.md §4.1` (stdlib + `requests` + `PyYAML` + `pytest` + `responses` only).

### Security

- The columns-block fix SHALL NOT introduce any new code path that logs, echoes, or persists the verbatim service payload or any summary field. The redacted `--debug` trace plumbing in `arr_cli.facade.transport` (`header values become ***<length>`) remains untouched.
- No new credential handling, no new config parsing, no new HTTP. The fix is local to `columns = [...]` literals in `arr_cli/{jellyfin,radarr,sonarr,maintainerr,seerr}.py`, to the dot-path traversal in `human._row_from_mapping` in `arr_cli/facade/output.py` (Req 16), and to `tests/unit/test_output.py`.
- The new unit tests SHALL NOT depend on or expose any real credential. Where a synthetic payload exercises the summary renderer, only public-domain fixture values (e.g. `"alice"`, `"Foo"`, `2024`, `True`) SHALL be used.

### Reliability

- The columns-block fix SHALL preserve the documented exit-code contract (`AGENTS.md §6`): the five stable exit codes (`ConfigError`=1, `AuthError`=2, `NetworkError`=3, `HttpError`=4, `ParseError`=5) and the stderr-only diagnostics guarantee SHALL NOT change.
- The columns-block fix SHALL preserve the renderer priority chain (`--human` > `--verbose` > default summary > verbatim) documented in `arr_cli.facade.output.emit`'s docstring. `emit` itself is untouched in this fix; the change is strictly in the per-service handler `columns = [...]` literals and in the `human._row_from_mapping` dot-path traversal.
- The columns-block fix SHALL preserve `stdout` pipe-cleanliness: JSON-only paths emit JSON, the `--human` path emits tabular text, and stderr remains separate.
- After the columns-block fix lands, `make ci` SHALL continue to pass (== `make test && make secret-scan && make smoke-dry`) and `make lint` SHALL continue to pass. The existing 595 unit tests SHALL continue to pass; the new tests SHALL be additive.
- The dot-path traversal in `human._row_from_mapping` (Req 16) SHALL NOT introduce a new failure mode: any `KeyError`, `IndexError`, or `TypeError` along the walk returns `None` (rendered as `<null>`), which is the same observable behaviour as the pre-change flat lookup on a missing key. A malformed payload (e.g. nested field unexpectedly a list) SHALL NOT crash; it SHALL render `<null>` for that cell, matching the existing graceful-default contract.
- When the regression net asserts substring coverage, the substring rule SHALL handle both flat and dot-joined nested keys: a column key like `"playing.type"` SHALL match a dot-joined summary key like `"playing.type"` exactly, AND SHALL also match a top-level summary key like `"playing"` if the renderer ever flattens (it does not today, but the rule is forward-compatible). The implementation SHALL document this dual-shape rule in a comment so future readers do not regress to the flat-only substring rule.

### Usability

- The fix SHALL be invisible to operators using the documented flag triplet on size-to-summary commands: `<exe>` (default JSON), `<exe> --verbose` (verbatim JSON), `<exe> -h <cmd>` (summary table), `<exe> --verbose -h <cmd>` (verbatim table). All four outputs SHALL continue to look the same as before for commands that were never broken (the 14 verbatim-only commands and `radarr -h calendar`) and SHALL be corrected for the 15 size-to-summary commands.
- The new regression classes in `tests/unit/test_output.py` SHALL include a one-line class docstring (or comment) explaining what they pin ("for each `_SUMMARY_RENDERERS` key, every column key in the corresponding handler's `columns = [...]` block is a substring of (or equal to) a top-level key OR a dot-joined nested-dict key in the summary shape — so future drift trips the test") so future contributors understand the seam without having to re-derive it from the assertion body.
- The contributor SHALL run `make ci` and `make lint` locally before considering the change done, and both SHALL pass.
- The fix SHALL land as one or more commits on the existing branch `fix/human-tracks-default-shape` (the PR #6 branch). The contributor SHALL NOT open a new PR and SHALL NOT push a new branch.
