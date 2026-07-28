# Requirements Document

## Introduction

`arr-cli` currently emits the **verbatim** service JSON payload on stdout by
default for every command, with a tabular `--human` view available as an
opt-in. That default is the right contract for a shell pipeline that already
knows what it's parsing, but it is the wrong shape for the chat-agent
consumer that is emerging as the project's primary operator: a model that
wants to summarize "what is my media server doing right now" in a one-shot
turn, not to swallow a 250-line `/Sessions` array and pick the bits it
needs.

The `--verbose` flag flips that default in an **incompatible** but
deliberate way (`option (a)` per workboard ticket
`6a4dfded-9944-47e7-ad86-59a712e93fb0`): the default JSON output becomes a
**curated per-command summary** sized for chat-agent consumption, while
the previous default — full verbatim service JSON — remains available via
`--verbose`. `--human` / `-h` stays exactly where it is (tabular rendering,
unchanged behaviour). The intent is to make the chat-agent ergonomics the
default without removing the raw payload for anyone who depends on it.

This change is implemented with a per-command **summary renderer** that
lives in `arr_cli/facade/output.py` parallel to the existing `human()`
rendering path, plus a new `--verbose` flag registered at the top-level
argparse parser (the universal-flag pattern in
`arr_cli/facade/cli_common.py`) that populates `args.verbose`. The facade
chooses which renderer to use based on `args.verbose` vs `args.human`
with a documented priority: `--human` > `--verbose` > default summary. A
small set of commands is flagged as **size-to-summary candidates** and
gets a curated bullet list of fields; the rest stay on the verbatim JSON
pass-through (they are either already small or out of scope for this
ticket).

The audiences for this change are:

- **Chat-agent operators** — the default output is now small enough to fit
  in a single response and answer "what is playing?" / "what wanted?" /
  "what pending?" in one shot.
- **Shell-pipeline / script consumers** — pass `--verbose` to get today's
  pipe-clean JSON and keep building on top of it; no change to the
  pipe-clean stdout contract (single line, valid JSON, UTF-8 preserved).
- **Human operators** — `--human` / `-h` continues to render the
  full-payload table; nothing changes for them.

Six user-facing requirements below cover the flag, the renderer selection
priority, the per-command summary spec, the stable-exit-code / pipe-clean
guarantees, documentation, and the test surface. Non-functional
requirements lock down performance, security, reliability, and usability
constraints (no new runtime dependencies, no new exit codes, no log
churn, no diagnostics on stdout).

> **Note on context7 / code-context grounding.** The `context7__*` and
> `code-context__*` tools are not present in the agent tool list for this
> turn, so doc- and code-grounding lookups were not performed. The
> reference stack is plain Python 3.11+ stdlib + `argparse` + `requests` +
> `PyYAML` per `AGENTS.md §1` and `§4.1`; no external doc lookup is
> required for a CLI-only behavioural change of this scope.

> **Note on workboard ticket.** This requirements document encodes
> `option (a)` — the incompatible flip — for workboard ticket
> `6a4dfded-9944-47e7-ad86-59a712e93fb0`. The decision is recorded here
> as part of the acceptance gate ("design decision recorded as (a)
> incompatible flip").

## Requirements

### Requirement 1

**User Story:** As a chat-agent operator running `arr-cli` from a prompt
loop, I want the default JSON output of the size-to-summary commands to
be a curated per-command summary sized for a single-shot response, so
that I can answer "what is my media server doing right now?" without
having to ask the model to digest a 250-line `/Sessions` array first.

#### Requirement 1 Acceptance Criteria

1. WHEN the operator runs any command flagged as a size-to-summary
   candidate in the table under §"Per-command summary spec" (e.g.
   `jellyfin now`, `radarr wanted`, `seerr requests`) without
   `--verbose` and without `--human` THEN the CLI SHALL emit the
   curated per-command summary as the top-level JSON shape documented
   for that command, not the verbatim service payload.
2. WHEN the operator runs a command that is **not** flagged as a
   size-to-summary candidate (e.g. `jellyfin item`, `radarr lookup`,
   `seerr request-count`, `maintainerr health`) without `--verbose`
   and without `--human` THEN the CLI SHALL continue to emit the
   verbatim service JSON unchanged from today's behaviour.
3. WHEN the curated summary is emitted THEN the JSON SHALL be a
   single line of compact JSON (no indent), UTF-8 preserved via
   `ensure_ascii=False`, and parseable by `json.loads` — the same
   pipe-clean stdout contract that holds today.
4. IF a command's summary renderer receives a payload that is not
   the shape the renderer expects (e.g. an empty list, a `null`,
   a missing nested object) THEN the renderer SHALL return a
   well-formed JSON value (e.g. `[]`, `null`, `{}`, or a documented
   placeholder object) instead of raising an exception, so the
   five stable exit codes are preserved.
5. WHEN the same command is run twice with different service
   payloads (e.g. one session vs. three sessions) THEN the summary
   keys SHALL be the same and only the count-shaped values SHALL
   differ, so a chat-agent can pattern-match on the structure.

### Requirement 2

**User Story:** As a shell-pipeline / script consumer who already
scripts against today's verbatim JSON output, I want a `--verbose` flag
that restores the exact pipe-clean JSON behaviour on demand, so that
my existing pipelines continue to work unchanged with a single
flag addition.

#### Requirement 2 Acceptance Criteria

1. WHEN the operator runs any command with `--verbose` (and without
   `--human`) THEN the CLI SHALL emit the verbatim service JSON payload
   on stdout — byte-equivalent to the pre-change default — and the exit
   code SHALL be `0` on success.
2. WHEN the operator runs any command with `--verbose` THEN the CLI
   SHALL NOT call the summary renderer in the hot path; the verbatim
   payload that came back from `transport.get` SHALL be written
   directly via `json.dumps(payload, ensure_ascii=False)`.
3. WHEN `--verbose` is combined with a command that is not a
   size-to-summary candidate (e.g. `jellyfin item --verbose`) THEN the
   emitted payload SHALL be identical to the no-flag output for that
   command — `--verbose` is a no-op on commands that already emit
   verbatim JSON by default.
4. IF `--verbose` and `--human` are both passed THEN `--human` SHALL
   win (priority order in Requirement 3); `--verbose` SHALL have no
   effect on the rendered human table.
5. WHEN `--verbose` is registered as a CLI argument THEN it SHALL
   default to `False` and SHALL be a `store_true` action (no value
   required); argparse SHALL accept `--verbose` as a bare flag in any
   position relative to the subcommand.

### Requirement 3

**User Story:** As a CLI author reading the parser output, I want the
renderer selection logic to be a single, auditable priority chain
(`--human` > `--verbose` > default summary), so that the user-facing
behaviour is predictable and the dispatch is easy to reason about
across all five service CLIs.

#### Requirement 3 Acceptance Criteria

1. WHEN the operator passes `--human` / `-h` (with or without
   `--verbose`) THEN the CLI SHALL emit the tabular human-readable
   view via the existing `output.human(...)` path and SHALL NOT
   invoke the summary renderer.
2. WHEN the operator passes `--verbose` (without `--human`) and the
   command is a size-to-summary candidate THEN the CLI SHALL emit the
   verbatim service JSON, NOT the summary.
3. WHEN the operator passes neither `--human` nor `--verbose` and the
   command is a size-to-summary candidate THEN the CLI SHALL emit the
   curated per-command summary.
4. WHEN the operator passes neither `--human` nor `--verbose` and the
   command is NOT a size-to-summary candidate THEN the CLI SHALL emit
   the verbatim service JSON (today's behaviour, unchanged).
5. IF the renderer selection logic is implemented as a single
   `if args.human: ... elif args.verbose: ... elif is_summary_candidate(args.command): ... else: ...`
   chain in `output.emit` (or an equivalent one-call dispatch in the
   per-service `_emit` helpers) THEN the acceptance check SHALL pass;
   the logic SHALL NOT be scattered across per-service CLI files.
6. WHEN the dispatch table is added to `output.emit` (or a sibling
   module) THEN it SHALL be a plain `dict[str, Callable[[Any], Any]]`
   keyed by per-service command name (e.g. `("jellyfin", "now")`),
   with a single registration point so adding a new summary is a
   one-line change.

### Requirement 4

**User Story:** As a future CLI author adding a new command, I want
the per-command summary spec to be documented in this requirements
file with explicit field lists per command, so that the contract for
"what does the summary look like?" is reviewable up-front rather than
discovered by reading the renderer.

#### Requirement 4 Acceptance Criteria

1. WHEN this requirements document is read end-to-end THEN the §
   "Per-command summary spec" SHALL exist and SHALL contain one
   bullet list per size-to-summary candidate command, with one
   field per bullet (key: source-field description).
2. WHEN the spec is reviewed THEN the commands listed in the spec
   SHALL be exactly the size-to-summary candidates enumerated in the
   feature description (`jellyfin: now, recent, favorites, resume,
   latest`; `radarr: wanted, queue, recent`; `sonarr: wanted, queue,
   recent`; `seerr: requests, search, available`; `maintainerr:
   pending`); no other command SHALL be speculatively added to the
   spec.
3. WHEN the spec is reviewed THEN the "safe to leave alone" commands
   SHALL be explicitly enumerated in a separate list (`jellyfin:
   item, search, nextup`; `radarr: calendar, lookup, movie`;
   `sonarr: calendar, lookup, series`; `seerr: request-count, media,
   user`; `maintainerr: health, storage`) with a one-line statement
   that they remain on verbatim JSON — the explicit absent list is
   part of the contract.
4. WHEN the summary for `jellyfin now` is specified THEN its
   top-level shape SHALL be
   `{ user, device, client, playing: {type, name, series, season, episode}, progress: {position_ticks, is_paused} }`
   — the suggested shape from the feature description, with one
   session per top-level object and the inner `playing` object
   collapsed to `null` when no item is currently playing.
5. WHEN the summary for any other command is specified THEN the
   field list SHALL be the minimum field set needed by a chat-agent
   to answer the natural-language question implied by the command
   name (e.g. `radarr wanted` → "what is missing and monitored?";
   `seerr requests` → "what has the household requested?"), and
   SHALL NOT carry any field that is not used to answer that
   question.

### Requirement 5

**User Story:** As any downstream consumer of `arr-cli` (chat-agent,
shell pipeline, dashboard, monitoring tool), I want the five stable
exit codes, the pipe-clean stdout contract, and the stderr-only
diagnostics contract to be preserved across this change, so that the
flag flip is observable only in the contents of stdout and never in
the metadata around it.

#### Requirement 5 Acceptance Criteria

1. WHEN a command succeeds with the new default summary output THEN
   the exit code SHALL be `0`; the five-class error model
   (`ConfigError` → `1`, `AuthError` → `2`, `NetworkError` → `3`,
   `HttpError` → `4`, `ParseError` → `5`) SHALL be unchanged, and
   their exit-code values SHALL be byte-identical to the pre-change
   constants in `arr_cli/facade/errors.py`.
2. WHEN the summary renderer is in the hot path THEN it SHALL NOT
   emit anything to stderr; the structured `service=... op=...
   status=... message=...` line on error SHALL still flow through
   `main_wrapper` unchanged, and any `--debug` traceback SHALL still
   flow through `handle_arr_error` / `handle_unexpected_error`
   unchanged.
3. WHEN `--verbose` is used to bypass the renderer THEN the output
   SHALL be a single line of JSON with no trailing newline strip
   and no envelope wrapper (matches today's `REQ-3 AC1` contract:
   "the top-level structure is the verbatim service payload, so we
   do NOT wrap it in an envelope").
4. WHEN a size-to-summary command is invoked against a typo'd or
   unknown subcommand THEN argparse SHALL still raise the standard
   `argparse` usage error and exit with code `2` (the documented
   argparse behaviour) — the new `--verbose` flag SHALL NOT alter
   the parse-failure path.
5. WHEN the summary renderer is added to `output.py` THEN it SHALL
   be a pure function (no I/O, no logging, no `print`); the only
   site that writes to `sys.stdout` SHALL remain `output.emit`
   itself, so the diagnostics-on-stderr guarantee is preserved by
   construction.

### Requirement 6

**User Story:** As the maintainer shipping this change, I want the
docs, the test suite, and the `--help` text updated to reflect the new
default and the new flag, so that the chat-agent-onboarding story is
discoverable and the existing shell-pipeline story is grep-able for
anyone scripting against the CLI.

#### Requirement 6 Acceptance Criteria

1. WHEN the README, AGENTS.md, and `.specs/arr-cli-mvp/requirements.md`
   are inspected THEN the description of the default JSON output
   SHALL be updated to "curated per-command summary; pass `--verbose`
   for the verbatim service payload" — the obsolete "default JSON
   output is verbatim" phrasing SHALL be removed.
2. WHEN `arr_cli/facade/cli_common.py::build_parser` is inspected THEN
   the universal-flag set SHALL include a `--verbose` entry with the
   same shape as the existing `--human` / `-h` entry (a `store_true`
   flag, default `False`, no short alias) and the docstring SHALL
   enumerate the renderer priority chain (`--human` > `--verbose` >
   default summary).
3. WHEN the per-service CLI `--help` text is inspected (e.g.
   `jellyfin --help`) THEN the description SHALL mention the new
   default and SHALL explicitly call out `--verbose` for users
   who want the verbatim service payload.
4. WHEN the `tests/unit/` test suite is run THEN new tests SHALL
   cover at least: (a) `jellyfin now` without `--verbose` produces
   the curated summary shape, (b) `jellyfin now --verbose` produces
   the verbatim payload, (c) `jellyfin now --human --verbose`
   produces the human table (`--human` wins), (d) `jellyfin now`
   with an empty `/Sessions` response produces a well-formed
   summary (no exception), and (e) the dispatcher priority chain
   is a single audit point.
5. WHEN `make ci` (test + secret-scan + smoke-dry) is run THEN it
   SHALL pass; `make lint` SHALL also pass — the umbrella test
   gates enumerated in `AGENTS.md §3` are not weakened.

## Per-command summary spec

The fields below are the contract for the curated summary renderer.
Each command's summary is a JSON object (or array of objects) whose
keys are listed in the bullet list. Inner objects (e.g. `playing`,
`progress`) are documented inline with their sub-bullets.

### Size-to-summary candidates

These commands get a curated summary as the default; pass `--verbose`
for the verbatim payload.

#### `jellyfin now` (suggested shape, per feature description)

- `user` — `UserName` of the active session (string).
- `device` — `DeviceName` of the active session (string).
- `client` — `Client` of the active session (string).
- `playing` — nested object or `null`:
  - `type` — `NowPlayingItem.Type` (string, e.g. `"Episode"`).
  - `name` — `NowPlayingItem.Name` (string).
  - `series` — `NowPlayingItem.SeriesName` (string).
  - `season` — `NowPlayingItem.ParentIndexNumber` (int).
  - `episode` — `NowPlayingItem.IndexNumber` (int).
- `progress` — nested object:
  - `position_ticks` — `PlayState.PositionTicks` (int).
  - `is_paused` — `PlayState.IsPaused` (bool).

**Top-level shape:** one object per active session; if no sessions are
active, emit `[]`.

#### `jellyfin recent`

- `Name` — item name (string).
- `Type` — item type (string).
- `ProductionYear` — production year (int).
- `SeriesName` — series name or `null` (string).
- `UserData.LastPlayedDate` — last played date (string or `null`).

**Top-level shape:** array of these objects (one per recently played
item).

#### `jellyfin favorites`

- `Name` — item name (string).
- `Type` — item type (string).
- `ProductionYear` — production year (int).
- `SeriesName` — series name or `null` (string).

**Top-level shape:** array of these objects.

#### `jellyfin resume`

- `Name` — item name (string).
- `Type` — item type (string).
- `ProductionYear` — production year (int).
- `SeriesName` — series name or `null` (string).
- `UserData.PlaybackPositionTicks` — resume position (int).
- `UserData.PlayCount` — play count (int).

**Top-level shape:** array of these objects.

#### `jellyfin latest`

- `Name` — item name (string).
- `Type` — item type (string).
- `ProductionYear` — production year (int).
- `SeriesName` — series name or `null` (string).
- `DateCreated` — date added to library (string).

**Top-level shape:** array of these objects.

#### `radarr wanted`

- `title` — movie title (string).
- `year` — release year (int).
- `tmdbId` — TMDB id (int).
- `monitored` — monitored flag (bool).

**Top-level shape:** array of these objects.

#### `radarr queue`

- `title` — queue item title (string).
- `status` — `status` (string).
- `trackedDownloadStatus` — `trackedDownloadStatus` (string).
- `size` — total size in bytes (int).
- `sizeleft` — remaining size in bytes (int).

**Top-level shape:** array of these objects.

#### `radarr recent`

- `movie.title` — movie title (string).
- `movie.year` — release year (int).
- `eventType` — `eventType` (string).
- `date` — `date` (string).

**Top-level shape:** array of these objects.

#### `sonarr wanted`

- `title` — episode title (string).
- `seasonNumber` — season number (int).
- `episodeNumber` — episode number (int).
- `airDate` — air date (string).
- `monitored` — monitored flag (bool).

**Top-level shape:** array of these objects.

#### `sonarr queue`

- `title` — queue item title (string).
- `status` — `status` (string).
- `trackedDownloadStatus` — `trackedDownloadStatus` (string).
- `size` — total size in bytes (int).
- `sizeleft` — remaining size in bytes (int).

**Top-level shape:** array of these objects.

#### `sonarr recent`

- `series.title` — series title (string).
- `episode.title` — episode title (string).
- `eventType` — `eventType` (string).
- `date` — `date` (string).

**Top-level shape:** array of these objects.

#### `seerr requests`

- `title` — request title (string).
- `type` — media type (string, e.g. `"movie"` / `"tv"`).
- `status` — request status (string, e.g. `"pending"`, `"approved"`).
- `createdAt` — request creation date (string).
- `requestedBy.displayName` — requester display name (string).

**Top-level shape:** array of these objects.

#### `seerr search`

- `title` — result title (string).
- `mediaType` — `mediaType` (string).
- `releaseDate` — `releaseDate` (string).
- `mediaInfo.tmdbId` — TMDB id (int).

**Top-level shape:** array of these objects.

#### `seerr available`

- `title` — result title (string).
- `mediaType` — `mediaType` (string).
- `releaseDate` — `releaseDate` (string).
- `mediaInfo.status` — `mediaInfo.status` (int).

**Top-level shape:** array of these objects.

#### `maintainerr pending`

- `title` — collection title (string).
- `mediaCount` — media count (int).
- `deleteAfterDays` — days until deletion (int).
- `isOnHold` — on-hold flag (bool).

**Top-level shape:** array of these objects.

### Safe to leave alone (verbatim JSON)

These commands stay on verbatim JSON pass-through. They are explicitly
out of scope for the curated summary: their responses are small by
design, or they are explicitly out of scope per the workboard ticket.

- `jellyfin item` — single-item lookup, response is one object.
- `jellyfin search` — empty-result-friendly, no chat-agent use case.
- `jellyfin nextup` — already bounded by `--limit`, response is small.
- `radarr calendar` — date-keyed array, callers pipe this directly.
- `radarr lookup` — term-keyed lookup, shell pipelines depend on it.
- `radarr movie` — single-item lookup, response is one object.
- `sonarr calendar` — same rationale as `radarr calendar`.
- `sonarr lookup` — same rationale as `radarr lookup`.
- `sonarr series` — single-item lookup, response is one object.
- `seerr request-count` — bare integer / boolean aggregate, out of
  scope per workboard ticket.
- `seerr media` — single-item lookup, response is one object.
- `seerr user` — auth self-check, callers depend on the raw identity
  object.
- `maintainerr health` — bare boolean, out of scope per workboard ticket.
- `maintainerr storage` — already compact, no chat-agent use case.

## Non-Functional Requirements

### Performance

- The summary renderer SHALL be a pure-Python function over an
  already-decoded JSON value; it SHALL NOT make additional HTTP calls,
  re-parse the payload, or copy the payload more than once per
  invocation. Measured overhead SHALL be under 1 ms per call on a
  developer laptop for the size-to-summary commands (10–100 item
  arrays).
- `--verbose` SHALL NOT introduce a measurable performance regression
  vs. the pre-change default: the verbatim path is a single
  `json.dumps(payload, ensure_ascii=False)` call followed by a
  `print`, identical to today's `emit(payload, human_mode=False)`
  branch.
- The dispatcher table SHALL be a process-wide `dict` built once at
  module import time (`output.py`); the per-call dispatch SHALL be
  a single `dict` lookup, not a chain of `if`/`elif` over every
  command.

### Security

- The summary renderer SHALL NOT read or echo any credential value;
  the `--verbose` flag SHALL NOT change the transport layer's
  `--debug` header-redaction behaviour (`***<length>`) documented in
  `AGENTS.md §5.6` and the `transport` module.
- The summary renderer SHALL NOT add any new logging path; no body
  bytes, no header values, no summary keys SHALL be written to
  stderr in the default path. The existing `--debug` redaction
  policy is preserved.
- No new runtime dependency MAY be introduced (per `AGENTS.md §4.1`:
  "Do not add new runtime dependencies without discussion"). The
  default at v0 of this change is plain Python 3.11+ stdlib only.

### Reliability

- `make ci` (`make test && make secret-scan && make smoke-dry`) and
  `make lint` SHALL both pass after the change is applied; the
  umbrella test gates enumerated in `AGENTS.md §3` are not weakened.
- The five stable exit codes and their class names SHALL be preserved
  byte-for-byte; downstream consumers scripting against those codes
  SHALL NOT observe any change.
- The summary renderer SHALL be defensive against malformed payloads:
  an empty list, a `null`, a missing nested object, or a payload
  whose keys are absent SHALL all produce a well-formed JSON value
  (e.g. `[]`, `{}`, `null`, or a documented placeholder) instead of
  a `KeyError` / `TypeError` that would map to exit code `1`.
- The new `--verbose` flag SHALL be a no-op on commands that are not
  size-to-summary candidates; the response shape, exit code, and
  stderr behaviour SHALL be byte-identical to the pre-change output.

### Usability

- The renderer priority chain SHALL be documented in the docstring
  of `output.emit` (or its dispatch sibling) so a future CLI author
  reading the source code sees the precedence order in one place.
- The per-command summary spec SHALL be in this requirements file
  (the bullet lists under §"Per-command summary spec"), not hidden
  inside the renderer — the contract is reviewable up-front and
  grep-able from the docs surface.
- The `--help` text for `--verbose` SHALL be a single line that
  names the verbatim JSON behaviour and points at the default
  summary for the size-to-summary commands (e.g. "emit the verbatim
  service JSON payload instead of the curated summary (default
  for size-to-summary commands)").
- The chat-agent ergonomics shall be the default: a single-turn
  invocation of `jellyfin now` (or any size-to-summary command)
  SHALL produce a JSON value that fits in a one-shot LLM response
  without summarization-driven token overflow.
