# Requirements Document

## Introduction

The `arr-cli-mvp` feature delivers a set of read-only Python CLI wrappers around Renald's self-hosted media server stack: Jellyfin (media playback), Radarr (movies), Sonarr (TV), Maintainerr (collection cleanup), and Seerr/Overseerr (media requests). The deliverable is **five thin CLI scripts** (one per service) backed by a **single shared facade module** that handles configuration, HTTP transport, authentication, and output formatting. Together they expose **29 read-only commands** (all HTTP `GET`s) covering now-playing sessions, calendars, want lists, queues, recent history, lookup, item-by-id, library/health/status, search, and request counts.

The CLIs target **Lily** (Renald's chat companion Bott) as the primary operator. Lily uses them to converse with Renald about what he is watching, tease his rewatch patterns, surface newly added content, comment on what Maintainerr is about to delete, and help him discover and request media. The CLIs also serve Renald directly for ad-hoc shell inspection.

Value:
- **No hardcoded values** — every URL, API key, and user identifier lives in a single gitignored config file (`~/.config/lily/arr.conf`) so the repo can be public, contributed to, and audited.
- **Uniform operator experience** — JSON by default, `--human`/`-h` for readable text; consistent exit codes; consistent error surface across all five services.
- **Safe to leave lying around** — read-only MVP, stateless per invocation (no daemon, no mandatory cache), graceful failure when a service is down (one broken service cannot break the others).
- **Fast local iteration** — Python stdlib-first where reasonable, documented schema, placeholder-only examples in the repo.

Out of scope for this MVP (reserved for a tier-2 follow-up): Maintainerr `veto` (`POST /api/collections/media/handle`), Seerr `create-request` (`POST /api/v1/request`), any write/mutate endpoint, webhook receivers, and long-running/cache daemons.

Pre-locking verification items (must be confirmed in the design phase, then re-checked at integration time):
- Maintainerr `/api/rules` endpoint presence against live `/api/swagger`.
- Seerr `/api/v1/...` path correctness against live `/api/v1/openapi.json`.

## Requirements

### Requirement 1 — Single canonical config file with documented placeholder schema

**User Story:** As Renald (repository owner), I want all service URLs, API keys, and user identifiers stored in a single gitignored config file under `~/.config/lily/arr.conf`, so that nothing sensitive is ever committed and one file is the single source of truth for runtime configuration.

#### Requirement 1 Acceptance Criteria

1. WHEN any subcommand runs THEN the system SHALL read configuration from `~/.config/lily/arr.conf` by default and SHALL accept `--config <path>` to override the location for that invocation only.
2. IF the config file is missing THEN the system SHALL exit non-zero with a stderr message naming the canonical path and SHALL NOT attempt to create or auto-populate it.
3. WHEN the config file is parsed THEN the system SHALL accept both YAML and TOML formats (selected by file extension `.yaml`/`.yml`/`.toml` and by a leading format sniff) and SHALL reject malformed input with a non-zero exit and a line/key-pointing error message on stderr.
4. WHEN the repository is shipped THEN the committed config example SHALL contain **only placeholder values** (e.g. `https://example.com`, `YOUR_API_KEY_HERE`, `<user-id>`) and SHALL NOT contain any real URL, API key, token, or instance identifier.
5. WHEN a `.gitignore` is present at the project root THEN the config example filename (e.g. `arr.conf.example`) and any user-local copy (`arr.conf`, `arr.local.yaml`, etc.) SHALL be listed so a local file is never accidentally committed.
6. IF a referenced env var is unset while parsing the config THEN the system SHALL substitute the documented default and SHALL continue; HOWEVER IF a required service section (e.g. `jellyfin`) is entirely absent and a command for that service is invoked THEN the system SHALL exit non-zero with stderr naming the missing section.
7. WHEN config values are loaded THEN the system SHALL also accept environment variable overrides for all keys (e.g. `LILY_JELLYFIN_URL`, `LILY_JELLYFIN_API_KEY`), with env vars taking precedence over file values.

### Requirement 2 — Per-service authentication handled by the facade

**User Story:** As Lily (chat companion), I want each service's auth scheme to be applied transparently by the shared facade, so that command implementations stay focused on endpoints and never have to thread headers themselves.

#### Requirement 2 Acceptance Criteria

1. WHEN the facade makes a request to Jellyfin THEN it SHALL attach an `X-Emby-Token: <api_key>` header on every call.
2. WHEN the facade makes a request to Radarr or Sonarr THEN it SHALL attach an `X-Api-Key: <api_key>` header on every call.
3. WHEN the facade makes a request to Seerr THEN it SHALL attach an `X-Api-Key: <api_key>` header on every call.
4. IF Maintainerr has `auth.enabled = true` configured THEN the facade SHALL attach the configured header scheme; OTHERWISE (the documented default, since Maintainerr ships with no auth) the facade SHALL make the request with no Authorization-style header.
5. WHEN any service returns HTTP 401 or 403 THEN the facade SHALL translate that into a single shared error class (e.g. `AuthError`) with exit code `2`, a stderr message naming the service and the failing endpoint, and SHALL NOT dump a Python traceback.
6. WHEN auth credentials for a service are missing AND a command for that service is invoked THEN the system SHALL exit with code `2` and a stderr message naming the missing credential key (e.g. `jellyfin.api_key`).

### Requirement 3 — Uniform JSON output with `--human`/`-h` readable formatting

**User Story:** As Lily (chat companion), I want consistent JSON output from every command with a readable mode for direct human use, so that downstream parsing in chat is trivial and ad-hoc terminal use stays comfortable.

#### Requirement 3 Acceptance Criteria

1. WHEN a subcommand runs without formatting flags THEN the system SHALL emit a single JSON document on **stdout** whose top-level structure is the verbatim response payload from the service (array for list endpoints, object for single-item endpoints, primitive for scalar endpoints like `health`).
2. WHEN `--human` or `-h` is passed THEN the system SHALL emit a tabular, multi-line human-readable rendering on stdout, with at least: an ID/title column, the most relevant status/dates columns per resource type, and pagination of long lists (default 20 per page; override via `--limit`).
3. WHEN `--human`/`-h` is combined with a JSON-only endpoint (e.g. `health` returning a bare boolean) THEN the system SHALL render the JSON with indentation and SHALL NOT crash.
4. WHEN output is produced THEN the system SHALL write all data to **stdout** and all diagnostics (info, warnings, errors) to **stderr**; a consumer redirecting only stdout SHALL receive a clean JSON document with no interspersed log lines.
5. WHEN output encoding is non-ASCII THEN the system SHALL use UTF-8 throughout and SHALL set the locale so that `--human` tables render without mojibake.

### Requirement 4 — Graceful failure and uniform exit codes across all five services

**User Story:** As Lily (chat companion), I want one broken service to never break the others and want a clear signal on what went wrong, so that incident triage is fast and one Mantainerr outage does not poison a Jellyfin check in the same shell session.

#### Requirement 4 Acceptance Criteria

1. IF the target service is unreachable (DNS failure, connection refused, TLS error, timeout after a configurable default of 10s) THEN the system SHALL exit with code `3` and a stderr message naming the service, the URL attempted, and the underlying error class.
2. IF the target service returns any HTTP status in the 4xx or 5xx range THEN the system SHALL exit with code `4` and a stderr message that includes the service name, HTTP status, and a body excerpt (truncated to 500 chars).
3. WHEN any error occurs THEN the system SHALL write the error to stderr as a single structured message (e.g. `service=jellyfin op=item id=42 status=404 message=Item not found`) and SHALL NOT print a Python traceback unless `--debug` is passed.
4. IF the user invokes a subcommand for a service whose dependency is currently down THEN the failure SHALL be reported for that subcommand only; the process SHALL exit, not enter an inconsistent global state, so the next invocation starts clean.
5. WHEN `--debug` is passed THEN the system SHALL additionally emit the full traceback to stderr and the request/response pair (URL, headers with secrets redacted, status, body) to a debug log on stderr.
6. IF a JSON parse of the response body fails THEN the system SHALL exit with code `5` and a stderr message naming the service and the first non-JSON byte offset.

### Requirement 5 — Stateless per-invocation execution

**User Story:** As Renald (operator), I want each CLI invocation to be fully independent with no daemon, no mandatory cache, and no hidden global state, so that the tool runs cleanly from cron, CI, and ad-hoc shells alike.

#### Requirement 5 Acceptance Criteria

1. WHEN a subcommand exits THEN the system SHALL release all sockets, file handles, and temporary resources before returning; the process SHALL NOT leave background threads or child processes alive.
2. IF the user has not configured a persistent cache THEN the system SHALL NOT write to any cache location and SHALL NOT hint that a cache exists.
3. WHEN network or disk I/O is performed THEN the system SHALL use timeouts (connect timeout 5s default, read timeout 30s default, both CLI-overridable via `--connect-timeout`/`--read-timeout`) and SHALL abort cleanly when they fire.
4. WHEN a subcommand is invoked THEN the system SHALL NOT rely on or modify any user-level state beyond the config file; re-running an identical command SHALL be safe and SHALL produce identical output within the service-side freshness window.

### Requirement 6 — Jellyfin CLI (`jellyfin` executable, 8 commands)

**User Story:** As Lily (chat companion), I want to query Jellyfin for now-playing sessions, resume candidates, recently played items, next-up episodes, latest additions, search, item-by-id, and favorites, so that I can chat with Renald about what he is actively watching, mock his rewatch patterns, and surface what's new.

#### Requirement 6 Acceptance Criteria

1. WHEN `jellyfin now` is invoked THEN the system SHALL `GET /Sessions` and SHALL emit the raw JSON on stdout.
2. WHEN `jellyfin resume` is invoked THEN the system SHALL `GET /Users/{userId}/Items/Resume` using the configured `jellyfin.user_id` and SHALL emit the raw JSON on stdout.
3. WHEN `jellyfin recent` is invoked THEN the system SHALL `GET /Users/{userId}/Items?SortBy=DatePlayed&Filters=IsPlayed` using the configured `jellyfin.user_id` and SHALL emit the raw JSON on stdout.
4. WHEN `jellyfin nextup` is invoked THEN the system SHALL `GET /Shows/NextUp` (optionally honoring `Limit`, `StartIndex`, `UserId` as query params) and SHALL emit the raw JSON on stdout.
5. WHEN `jellyfin latest` is invoked THEN the system SHALL `GET /Users/{userId}/Items/Latest` and SHALL emit the raw JSON on stdout.
6. WHEN `jellyfin search <query>` is invoked THEN the system SHALL `GET /Items?searchTerm=<urlencoded query>` and SHALL emit the raw JSON on stdout; empty query SHALL return the same error path as "service down" (code 3) only if the service rejects it, otherwise the service's empty-array response.
7. WHEN `jellyfin item <id>` is invoked THEN the system SHALL `GET /Items/{id}` and SHALL exit with code `4` + stderr naming the id when the service returns 404.
8. WHEN `jellyfin favorites` is invoked THEN the system SHALL `GET /Users/{userId}/Items/Favorites` and SHALL emit the raw JSON on stdout.

### Requirement 7 — Radarr CLI (`radarr` executable, 6 commands)

**User Story:** As Lily (chat companion), I want to read Radarr's upcoming calendar, missing movies, current queue, recent history, lookup by term, and a single movie by id, so that I can tell Renald what's coming up, what he has not grabbed yet, and dig into any title on demand.

#### Requirement 7 Acceptance Criteria

1. WHEN `radarr calendar` is invoked THEN the system SHALL `GET /api/v3/calendar` and SHALL emit the raw JSON on stdout.
2. WHEN `radarr calendar <start> [end]` is invoked THEN the system SHALL append `start=<start>` and, if provided, `end=<end>` to `/api/v3/calendar` and SHALL emit the raw JSON on stdout; the system SHALL accept both ISO-8601 dates and ISO-8601 datetimes and SHALL reject other formats with exit code `1` + stderr usage hint.
3. WHEN `radarr wanted` is invoked THEN the system SHALL `GET /api/v3/wanted/missing` and SHALL emit the raw JSON on stdout.
4. WHEN `radarr queue` is invoked THEN the system SHALL `GET /api/v3/queue` and SHALL emit the raw JSON on stdout.
5. WHEN `radarr recent` is invoked THEN the system SHALL `GET /api/v3/history/movie` and SHALL emit the raw JSON on stdout.
6. WHEN `radarr lookup <term>` is invoked THEN the system SHALL `GET /api/v3/movie/lookup?term=<urlencoded term>` and SHALL emit the raw JSON on stdout.
7. WHEN `radarr movie <id>` is invoked THEN the system SHALL `GET /api/v3/movie/{id}` and SHALL exit with code `4` + stderr naming the id when the service returns 404.

### Requirement 8 — Sonarr CLI (`sonarr` executable, 6 commands)

**User Story:** As Lily (chat companion), I want the same six read-only shapes for Sonarr that Radarr provides, so that I can hold parallel conversations about Renald's TV queue and his movie queue without juggling two different command grammars.

#### Requirement 8 Acceptance Criteria

1. WHEN `sonarr calendar` is invoked THEN the system SHALL `GET /api/v3/calendar` and SHALL emit the raw JSON on stdout.
2. WHEN `sonarr calendar <start> [end]` is invoked THEN the system SHALL append `start=<start>` and, if provided, `end=<end>` to `/api/v3/calendar` and SHALL reject malformed dates with exit code `1` + stderr usage hint.
3. WHEN `sonarr wanted` is invoked THEN the system SHALL `GET /api/v3/wanted/missing` and SHALL emit the raw JSON on stdout.
4. WHEN `sonarr queue` is invoked THEN the system SHALL `GET /api/v3/queue` and SHALL emit the raw JSON on stdout.
5. WHEN `sonarr recent` is invoked THEN the system SHALL `GET /api/v3/history` (Sonarr's TV history; not path-versioned like Radarr's `/history/movie`) and SHALL emit the raw JSON on stdout.
6. WHEN `sonarr lookup <term>` is invoked THEN the system SHALL `GET /api/v3/series/lookup?term=<urlencoded term>` and SHALL emit the raw JSON on stdout.
7. WHEN `sonarr series <id>` is invoked THEN the system SHALL `GET /api/v3/series/{id}` and SHALL exit with code `4` + stderr naming the id when the service returns 404.

### Requirement 9 — Maintainerr CLI (`maintainerr` executable, 3 commands, no-auth-by-default)

**User Story:** As Lily (chat companion), I want to peek at Maintainerr's pending collection overlays, storage metrics, and health, so that I can tease Renald about what Maintainerr is about to delete and confirm the service is up; Maintainerr has no auth by default so the config MUST flag a private-network warning.

#### Requirement 9 Acceptance Criteria

1. IF Maintainerr is configured with `auth.enabled = false` (default) THEN the facade SHALL log a one-line stderr warning at startup: `maintainerr: auth disabled; ensure this CLI is reachable only on a trusted/private network`, and the warning SHALL NOT appear if `--quiet` is passed.
2. WHEN `maintainerr pending` is invoked THEN the system SHALL `GET /api/collections/overlay-data` and SHALL emit the raw JSON on stdout.
3. WHEN `maintainerr storage` is invoked THEN the system SHALL `GET /api/storage-metrics` and SHALL emit the raw JSON on stdout.
4. WHEN `maintainerr health` is invoked THEN the system SHALL `GET /api/health/ready` and SHALL emit the raw JSON on stdout.
5. IF the optional Maintainerr `/api/rules` endpoint is present (to be verified against live `/api/swagger` during integration) THEN a future `maintainerr rules` command MAY be added; for MVP this is out of scope and the system SHALL NOT silently assume the endpoint exists.
6. WHEN Maintainerr returns 401/403 unexpectedly THEN the system SHALL surface stderr guidance: `maintainerr: 401/403 received — set auth.enabled=true in arr.conf and restart`.

### Requirement 10 — Seerr CLI (`seerr` executable, 6 commands)

**User Story:** As Lily (chat companion), I want to read Seerr's request list, request counts, multi-search, media availability, single media by tmdb id, and the current user, so that I can summarize the household request queue, help Renald discover new things, and confirm Seerr's identity/auth against my own account.

#### Requirement 10 Acceptance Criteria

1. WHEN `seerr requests` is invoked THEN the system SHALL `GET /api/v1/request` and SHALL emit the raw JSON on stdout.
2. WHEN `seerr request-count` is invoked THEN the system SHALL `GET /api/v1/request/count` and SHALL emit the raw JSON on stdout.
3. WHEN `seerr search <query>` is invoked THEN the system SHALL `GET /api/v1/search/multi?query=<urlencoded query>` and SHALL emit the raw JSON on stdout.
4. WHEN `seerr available <query>` is invoked THEN the system SHALL `GET /api/v1/media/available?query=<urlencoded query>` and SHALL emit the raw JSON on stdout.
5. WHEN `seerr media <tmdbId>` is invoked THEN the system SHALL `GET /api/v1/media/{tmdbId}` and SHALL emit the raw JSON on stdout.
6. WHEN `seerr user` is invoked THEN the system SHALL `GET /api/v1/user/me` and SHALL emit the raw JSON on stdout; this is the canonical auth-self-check.
7. IF a future `seerr create-request` (`POST /api/v1/request`) is added in tier-2 THEN it SHALL be guarded by a `--confirm` flag and SHALL NOT appear in MVP help output.

### Requirement 11 — Shared facade library + 5 executable entry points

**User Story:** As a future contributor (Lily or Renald), I want one Python facade module (e.g. `lily_arr/facade.py`) that owns HTTP, auth, config parsing, and output formatting, plus 5 thin entry points (one per service) that only contain service-specific routing and argument glue, so that adding a sixth service or tier-2 mutations is a localized change.

#### Requirement 11 Acceptance Criteria

1. WHEN a subcommand runs THEN its executable SHALL be a thin wrapper that imports the shared facade and dispatches to the service-specific request builders; the executable SHALL contain no hardcoded URL or credential.
2. WHEN the facade needs to make a request THEN it SHALL expose at minimum: `get(service, path, params=None)`, `config()` (returns parsed config dict), `human(payload, columns)` (formats tabular readable view), and a structured error hierarchy (`AuthError`, `ConnectionError`, `HttpError`, `ParseError`).
3. WHEN the package is installed (e.g. via `pip install -e .` or equivalent) THEN each of the 5 entry points SHALL be invokable from any shell with a working PATH; `jellyfin --help`, `radarr --help`, `sonarr --help`, `maintainerr --help`, `seerr --help` SHALL each print usage and exit `0`.
4. WHEN a subcommand receives unknown arguments THEN the system SHALL emit a usage hint on stderr and exit with code `1`; the exit code SHALL be stable across all five services.
5. WHEN the package is laid out THEN it SHALL be importable as a single Python package, importable by Lily via the standard Python import path used by the chat runtime; using `tomllib` (Python 3.11+ stdlib) for TOML, `PyYAML` (`>=6.0`) for YAML, and either `requests` (`>=2.28`) or `urllib.request` for HTTP.

### Requirement 12 — README + CHANGELOG for MVP release

**User Story:** As Renald (operator) and any future contributor, I want a README that documents each command, each config key, and the canonical config path, and a CHANGELOG entry that describes the MVP scope and what is deliberately out of scope, so that onboarding is instant.

#### Requirement 12 Acceptance Criteria

1. WHEN the repository is shipped THEN `README.md` SHALL contain: a one-paragraph purpose statement; the canonical config path (`~/.config/lily/arr.conf`); a placeholder-only `arr.conf.example`; per-service command tables (command → endpoint → purpose); the auth matrix (which header each service uses); and explicit install/invoke instructions.
2. WHEN the repository is shipped THEN `CHANGELOG.md` SHALL contain an `## MVP` (or equivalent top entry) bullet list of the 5 CLIs and 29 commands, plus an explicit "out of scope" section listing Maintainerr `veto`, Seerr `create-request`, all mutations, and webhook receivers.
3. IF a contribution guide is present THEN it SHALL explicitly state Renald's rule "nothing gets hardcoded" as the first acceptance criterion reviewers apply.

## Non-Functional Requirements

### Performance

- Cold-start invocation of any single subcommand SHALL complete its first stdout byte in **≤ 2 seconds** on a warm Python install when the target service responds within 200 ms; the HTTP read timeout SHALL be `30s` by default and connect timeout `5s`, both CLI-overridable.
- Memory footprint of any single invocation SHALL stay **≤ 80 MiB RSS** peak, regardless of payload size, by streaming large JSON lists when the upstream service provides a paging API and falling back to bounded in-memory parsing otherwise (cap `10 000` items per response with a stderr warning if the upstream returns more and pagination params are unknown).
- `--human` formatting for a list of `1 000` items SHALL complete within **1.5 seconds** of receiving the JSON, exclusive of network.

### Security

- The CLIs SHALL never log, echo, or print API keys, tokens, headers, or cookie values, even with `--debug`; `--debug` SHALL redact the values while preserving header names and the redacted-token length.
- The canonical config path SHALL default to a user-owned mode (`0600` on POSIX); the loader SHALL refuse to parse a config file readable by group or world and SHALL exit with code `1` + a permission-error message in that case.
- The shared facade SHALL validate any URL from config parses to `https://` or `http://` with a parseable host; non-conforming URLs SHALL fail closed with exit code `1`.
- All CLI args that may flow into a URL (search terms, ids, calendar dates, tmdb ids) SHALL be **percent-encoded** by the facade using the standard library, not interpolated raw.
- The repo SHALL contain no real secrets: a `scripts/secret-scan` (or pre-commit equivalent) SHALL grep for common API-key token shapes and SHALL fail the build if any are found committed.

### Reliability

- Each subcommand SHALL be safely re-runnable with identical arguments and identical expected output within the service-side freshness window; no subcommand SHALL mutate local state outside the read of `arr.conf` and the creation of a single `.lily_arr.lock`-free temp scratch dir under `TMPDIR`.
- Network and disk failures SHALL be retried only when explicitly opted into via `--retry N` (default `0`); retries SHALL use exponential backoff with jitter and SHALL respect an overall deadline (`--deadline`).
- The 5 CLIs SHALL be independently deployable; a failure in one service command (e.g. `jellyfin item 42` returning 404) SHALL NOT affect any other service command and SHALL NOT corrupt any shared state.

### Usability

- Every subcommand SHALL print a usage line on stderr when called with `--help`, including: synopsis, every flag, every positional argument, and a one-line description; the usage SHALL fit in `≤ 40` terminal rows and SHALL be stable across services.
- Error messages SHALL name the service, the operation, and the failing input where relevant (e.g. `radarr: movie id=42 — 404 Not Found`); they SHALL NOT contain stack traces unless `--debug` is set.
- Command names and flags SHALL be stable for the lifetime of the MVP: renaming a subcommand SHALL require bumping the major in `CHANGELOG.md` and aliasing the old name for at least one minor.
- `--human` tables SHALL be at most `120` columns wide by default and SHALL honor the `COLUMNS` environment variable when set; long values SHALL be truncated with `…` and SHALL be fully recoverable via the default JSON output.
- The repository SHALL provide a one-line smoke test (e.g. `make smoke` or `scripts/smoke.sh`) that exercises one command per service against the documented endpoints with `--human` and `--json` and exits non-zero on any failure.

### Assumptions and Constraints (version-sensitive)

- **Python**: implementation targets Python 3.11+ so the standard library provides `tomllib`, `argparse` improvements, and stable `urllib.request` HTTP behavior; this is a working assumption and SHALL be confirmed in the design phase by inspecting the target host's Python.
- **TOML parsing**: uses `tomllib` (Python 3.11+ stdlib) — no third-party dependency.
- **YAML parsing**: uses `PyYAML >=6.0` (`safe_load` only); the loader SHALL refuse to construct arbitrary Python objects via `yaml.load` and SHALL reject YAML anchors that resolve to non-dict roots.
- **HTTP transport**: either `requests >=2.28` (with explicit `timeout=(connect, read)` per call) **or** `urllib.request` with manual `timeout=` and `context=` for TLS; the design phase SHALL pick one and stick with it for consistency.
- **CLI parsing**: uses `argparse` (stdlib); no third-party CLI framework dependency.
- **Greenfield**: `/projects/media-cli` is empty except for `.git/` and an empty `.specs/arr-cli-mvp/` scaffold; no existing code reuse is possible and the README SHALL state this.
- **Pre-locking verifications** (must be executed at design time before locking the spec):
  - Verify Maintainerr `/api/rules` endpoint presence against the live `/api/swagger`.
  - Verify all Seerr `/api/v1/...` paths against the live `/api/v1/openapi.json`.
  - Both checks SHALL be re-run at integration time against the operator's actual instance before tagging the MVP release.
