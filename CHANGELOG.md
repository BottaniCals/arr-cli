# Changelog

All notable changes to `arr-cli` are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
within the pre-1.0 contract documented in `README.md`.

## [Unreleased]

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
