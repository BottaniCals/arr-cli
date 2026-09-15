# Changelog

All notable changes to `arr-cli` are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
within the pre-1.0 contract documented in `README.md`.

## [Unreleased]

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
- The remaining 14 commands (`jellyfin item`/`search`/`nextup`,
  `radarr calendar`/`lookup`/`movie`, `sonarr calendar`/`lookup`/`series`,
  `seerr request-count`/`media`/`user`, `maintainerr health`/`storage`)
  continue to emit verbatim service JSON unchanged.

### Added

- `--verbose` universal flag (registered on every per-service CLI) — emits
  the verbatim service JSON payload on stdout instead of the curated
  summary. The renderer priority chain is `--human` > `--verbose` >
  default summary > verbatim JSON.
- `arr_cli.facade.output.summarize(service, command, payload)` public
  function and the underlying `_SUMMARY_RENDERERS` dispatch table --
  single audit point for per-command summary rendering.

### Fixed

- Jellyfin authentication: the facade now sends the full
  `Authorization: MediaBrowser ***` envelope (Client, Device, DeviceId,
  Version, Token) instead of the standalone `X-Emby-Token` header.
  Jellyfin 12.x deprecated the bare header, so all eight `jellyfin`
  subcommands had been returning 401 against current Jellyfin releases.
  `DeviceId` is anchored to the host hostname so the Jellyfin dashboard
  groups the CLI's activity under one stable device.

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
