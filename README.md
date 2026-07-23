# arr-cli

Read-only Python CLI wrappers around Renald's self-hosted media server stack —
Jellyfin, Radarr, Sonarr, Maintainerr, and Seerr/Overseerr. The package ships
five thin executables (`jellyfin`, `radarr`, `sonarr`, `maintainerr`, `seerr`)
backed by a single shared facade (`arr_cli.facade`) that owns configuration,
HTTP transport, authentication, error mapping, and output formatting. Together
they expose **29 read-only commands** (every call is an HTTP `GET`).

The CLIs target two operators. **Lily** (Renald's chat companion Bott) consumes
JSON on stdout from a shell pipeline to power conversational queries about
what Renald is watching, his rewatch patterns, newly added content, and what
Maintainerr is about to delete. **Renald** (the operator) uses `--human` /
`-h` for ad-hoc terminal inspection.

This is the **MVP** release. It is deliberately read-only, stateless per
invocation (no daemon, no mandatory cache), and safe to leave lying around —
a single broken service cannot break the others.

---

## 1. Purpose

`arr-cli` exists so that agents can talk to the media server stack.
Every service URL, API key, and user id lives in a single
gitignored config file (`~/.config/arr/arr.conf`); every command emits the
verbatim service JSON on stdout by default and a tabular readable view with
`--human` / `-h`. There are no write endpoints in MVP: this package cannot
mutate the media server state under any circumstance.

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
# Seerr / Overseerr (media requests)
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

| Command                   | HTTP | Path                                                        | Notes                                                                  |
| ------------------------- | :--: | ----------------------------------------------------------- | ---------------------------------------------------------------------- |
| `jellyfin now`            | GET  | `/Sessions`                                                 | All active sessions across users.                                      |
| `jellyfin resume`         | GET  | `/Users/{user_id}/Items/Resume`                             | Requires `jellyfin.user_id`.                                           |
| `jellyfin recent`         | GET  | `/Users/{user_id}/Items?SortBy=DatePlayed&Filters=IsPlayed` | Requires `jellyfin.user_id`.                                           |
| `jellyfin nextup`         | GET  | `/Shows/NextUp`                                             | Accepts optional `Limit`, `StartIndex`, `UserId` query params.         |
| `jellyfin latest`         | GET  | `/Users/{user_id}/Items/Latest`                             | Requires `jellyfin.user_id`.                                           |
| `jellyfin search <query>` | GET  | `/Items?searchTerm=<query>`                                 | Empty query returns the service's empty-array response (not an error). |
| `jellyfin item <id>`      | GET  | `/Items/{id}`                                               | 404 → exit code `4` with stderr naming the id.                         |
| `jellyfin favorites`      | GET  | `/Users/{user_id}/Items/Favorites`                          | Requires `jellyfin.user_id`.                                           |

### 4.2 Radarr (`radarr` — 6 commands)

| Command                         | HTTP | Path                                       | Notes                                                                                |
| ------------------------------- | :--: | ------------------------------------------ | ------------------------------------------------------------------------------------ |
| `radarr calendar`               | GET  | `/api/v3/calendar`                         | No date range.                                                                       |
| `radarr calendar <start> [end]` | GET  | `/api/v3/calendar?start=<start>&end=<end>` | Accepts ISO-8601 dates or datetimes; malformed input → exit `1` + stderr usage hint. |
| `radarr wanted`                 | GET  | `/api/v3/wanted/missing`                   | Missing movies.                                                                      |
| `radarr queue`                  | GET  | `/api/v3/queue`                            | Current download / import queue.                                                     |
| `radarr recent`                 | GET  | `/api/v3/history/movie`                    | Movie history (path-versioned, not the generic `/history`).                          |
| `radarr lookup <term>`          | GET  | `/api/v3/movie/lookup?term=<term>`         | Percent-encoded by the facade.                                                       |
| `radarr movie <id>`             | GET  | `/api/v3/movie/{id}`                       | 404 → exit code `4` with stderr naming the id.                                       |

### 4.3 Sonarr (`sonarr` — 6 commands)

| Command                         | HTTP | Path                                       | Notes                                                                                |
| ------------------------------- | :--: | ------------------------------------------ | ------------------------------------------------------------------------------------ |
| `sonarr calendar`               | GET  | `/api/v3/calendar`                         | No date range.                                                                       |
| `sonarr calendar <start> [end]` | GET  | `/api/v3/calendar?start=<start>&end=<end>` | Accepts ISO-8601 dates or datetimes; malformed input → exit `1` + stderr usage hint. |
| `sonarr wanted`                 | GET  | `/api/v3/wanted/missing`                   | Missing episodes.                                                                    |
| `sonarr queue`                  | GET  | `/api/v3/queue`                            | Current download / import queue.                                                     |
| `sonarr recent`                 | GET  | `/api/v3/history`                          | TV history (NOT `/history/movie` like Radarr).                                       |
| `sonarr lookup <term>`          | GET  | `/api/v3/series/lookup?term=<term>`        | Percent-encoded by the facade.                                                       |
| `sonarr series <id>`            | GET  | `/api/v3/series/{id}`                      | 404 → exit code `4` with stderr naming the id.                                       |

### 4.4 Maintainerr (`maintainerr` — 3 commands)

Maintainerr ships with **no auth** by default. The facade logs a one-line
stderr warning on each invocation reminding the operator that the endpoint
must be reachable only on a trusted / private network, unless `--quiet` is
passed.

| Command               | HTTP | Path                            | Notes                                                                            |
| --------------------- | :--: | ------------------------------- | -------------------------------------------------------------------------------- |
| `maintainerr pending` | GET  | `/api/collections/overlay-data` | Pending collection overlays.                                                     |
| `maintainerr storage` | GET  | `/api/storage-metrics`          | Storage metrics.                                                                 |
| `maintainerr health`  | GET  | `/api/health/ready`             | Readiness probe; bare boolean response. `--human` renders it indented, no crash. |

> **Out of MVP.** A `maintainerr rules` command is **not** implemented. Adding
> it requires confirming `/api/rules` against the operator's live
> `/api/swagger`.

### 4.5 Seerr (`seerr` — 6 commands)

| Command                   | HTTP | Path                                    | Notes                                                                                                           |
| ------------------------- | :--: | --------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `seerr requests`          | GET  | `/api/v1/request`                       | All requests.                                                                                                   |
| `seerr request-count`     | GET  | `/api/v1/request/count`                 | Aggregate request counts.                                                                                       |
| `seerr search <query>`    | GET  | `/api/v1/search/multi?query=<query>`    | Percent-encoded by the facade.                                                                                  |
| `seerr available <query>` | GET  | `/api/v1/media/available?query=<query>` | Percent-encoded by the facade.                                                                                  |
| `seerr media <tmdbId>`    | GET  | `/api/v1/media/{tmdbId}`                | Media details by TMDB id.                                                                                       |
| `seerr user`              | GET  | `/api/v1/user/me` (fallback `/auth/me`) | Auth self-check. Tries `/api/v1/user/me` first; on 404 falls back to `/auth/me` (the Overseerr-canonical path). |

> **Out of MVP.** A `seerr create-request` command (`POST /api/v1/request`) is
> **not** implemented and does not appear in `--help`.

---

## 5. Auth matrix

The facade injects exactly one auth header per request — no per-command
threading of credentials.

| Service       | Header            | Value source                                                                                                                                              |
| ------------- | ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `jellyfin`    | `X-Emby-Token`    | `jellyfin.api_key` in `arr.conf`                                                                                                                          |
| `radarr`      | `X-Api-Key`       | `radarr.api_key` in `arr.conf`                                                                                                                            |
| `sonarr`      | `X-Api-Key`       | `sonarr.api_key` in `arr.conf`                                                                                                                            |
| `seerr`       | `X-Api-Key`       | `seerr.api_key` in `arr.conf`                                                                                                                             |
| `maintainerr` | (none by default) | When `maintainerr.auth_enabled = true`, every `maintainerr.extra` key/value pair is added as a header. Otherwise no `Authorization`-style header is sent. |

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

| Flag                     | Description                                                                                             |
| ------------------------ | ------------------------------------------------------------------------------------------------------- |
| `--config <path>`        | Override the canonical config path for this invocation only.                                            |
| `--debug` / `--no-debug` | Enable / disable the full traceback + redacted request/response log.                                    |
| `--quiet` / `--no-quiet` | Suppress informational stderr lines (e.g. the Maintainerr auth-disabled warning). Errors still surface. |
| `--human` / `-h`         | Render tabular readable text instead of raw JSON on stdout.                                             |
| `--limit <int>`          | Page size for `--human` lists (default `20`).                                                           |
| `--connect-timeout <s>`  | Override the config's `connect_timeout` (default `5.0`).                                                |
| `--read-timeout <s>`     | Override the config's `read_timeout` (default `30.0`).                                                  |
| `--retry <int>`          | Number of retry attempts on `NetworkError` (default `0`).                                               |
| `--deadline <s>`         | Absolute wall-clock cap for `--retry`, in seconds.                                                      |

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

---

## 8. Out of scope (tier-2)

The MVP is intentionally read-only. The following are **deliberately not
implemented** and will be the focus of a tier-2 follow-up:

- **Maintainerr `veto`** (`POST /api/collections/media/handle`).
- **Seerr `create-request`** (`POST /api/v1/request`). If added, it MUST be
  guarded by a `--confirm` flag and MUST NOT appear in MVP help output.
- **Any write / mutate endpoint** on any service (Radarr, Sonarr, Jellyfin,
  Maintainerr, Seerr).
- **Webhook receivers** (no inbound HTTP in MVP).
- **Long-running daemon** or **persistent cache layer**. Each invocation is
  fully stateless; no cache file is written unless explicitly configured,
  and the MVP ships with no cache configured.
- **Maintainerr `/api/rules`** is **not in MVP scope** because the endpoint
  is not documented in the Maintainerr public API reference; it will be
  re-evaluated against the operator's live `/api/swagger` at tier-2 time.

---

## 9. Contributing

**Nothing gets hardcoded.** Any new service, command, or config key that
hardcodes a URL, key, or user-id will be rejected at review.

Before opening a PR, run `make ci` (or `pytest tests/unit` +
`scripts/secret-scan` + `scripts/smoke.sh --dry-run`). The CI hookup refuses
to merge any diff that breaks the placeholder-only guarantee of
`arr.conf.example` or that fails the secret scanner.
