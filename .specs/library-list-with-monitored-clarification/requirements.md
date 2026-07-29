# Requirements Document

## Introduction

This change adds two `GET` library-list invocations to the existing read-only `arr-cli` suite (`sonarr series` with no id, `radarr movie` with no id) and clarifies that the `monitored` field on `lookup` results is a *source default* (TVDB for Sonarr, TMDB for Radarr), not the operator's library state. The existing single-fetch `series <id>` / `movie <id>` paths, the facade-mediated auth/transport/output pipeline, and the documented exit-code contract stay intact. The benefit is an operator can audit their library from the terminal without opening the web UI, and will not mistake a candidate-source flag for their own monitored state.

## Requirements

### Requirement 1 — Sonarr `series` (no id) — library list

**User Story:** As a self-hosted media stack operator, I want `sonarr series` (with no id argument) to enumerate every series in my Sonarr library, so that I can audit my collection from the terminal without opening the Sonarr web UI.

#### Requirement 1 Acceptance Criteria

1. WHEN the user runs `sonarr series` with no positional id THEN the CLI SHALL send `GET /api/v3/series` to the Sonarr base URL and emit the response on stdout.
2. WHEN the user runs `sonarr series` with no positional id THEN the `--human` rendering SHALL display a tabular view with exactly these column headers, in this order: `title, year, monitored, status, tvdbId, seasons`.
3. WHEN the response contains N series THEN the rendered table SHALL contain N data rows.
4. IF Sonarr returns HTTP 401/403 THEN the CLI SHALL exit with code 2 (`AuthError`); IF 4xx/5xx other than 401/403 THEN exit code 4 (`HttpError`); IF the response body is not valid JSON THEN exit code 5 (`ParseError`).

### Requirement 2 — Radarr `movie` (no id) — library list

**User Story:** As a self-hosted media stack operator, I want `radarr movie` (with no id argument) to enumerate every movie in my Radarr library, so that I can audit my collection from the terminal without opening the Radarr web UI.

#### Requirement 2 Acceptance Criteria

1. WHEN the user runs `radarr movie` with no positional id THEN the CLI SHALL send `GET /api/v3/movie` to the Radarr base URL and emit the response on stdout.
2. WHEN the user runs `radarr movie` with no positional id THEN the `--human` rendering SHALL display a tabular view with exactly these column headers, in this order: `title, year, monitored, status, tmdbId, imdbId`.
3. WHEN the response contains N movies THEN the rendered table SHALL contain N data rows.
4. IF Radarr returns HTTP 401/403 THEN the CLI SHALL exit with code 2 (`AuthError`); IF 4xx/5xx other than 401/403 THEN exit code 4 (`HttpError`); IF the response body is not valid JSON THEN exit code 5 (`ParseError`).

### Requirement 3 — Single-fetch behaviour preserved for `series <id>` / `movie <id>`

**User Story:** As an operator who scripts against `arr-cli`, I want the existing single-id invocation to keep working unchanged, so that my scripts do not break.

#### Requirement 3 Acceptance Criteria

1. WHEN the user runs `sonarr series <id>` THEN the CLI SHALL send `GET /api/v3/series/{id}` (the existing single-fetch endpoint, unchanged).
2. WHEN the user runs `radarr movie <id>` THEN the CLI SHALL send `GET /api/v3/movie/{id}` (the existing single-fetch endpoint, unchanged).
3. WHEN the user passes an id argument THEN the CLI SHALL percent-encode the id via `arr_cli.facade.transport.encode_path_segment` before constructing the URL.

### Requirement 4 — Rename `monitored` → `defaultMonitored` on lookup `--human` output (both services)

**User Story:** As an operator reading `--human` lookup output, I want the column that surfaces the candidate-source default to be labeled `defaultMonitored` (not `monitored`), so that I do not confuse it with my library's monitored state.

#### Requirement 4 Acceptance Criteria

1. WHEN the user runs `sonarr lookup <term> --human` THEN the rendered table SHALL contain a column header named exactly `defaultMonitored` (camelCase, no spaces).
2. WHEN the user runs `radarr lookup <term> --human` THEN the rendered table SHALL contain a column header named exactly `defaultMonitored` (camelCase, no spaces).
3. WHEN the user runs `sonarr lookup <term> --human` or `radarr lookup <term> --human` THEN the rendered table SHALL NOT contain a column header named exactly `monitored` (bare, no prefix).
4. WHEN the user runs `sonarr lookup <term>` (no `--human`) or with `--verbose` THEN the JSON output SHALL still expose the raw `monitored` key as it appears in the upstream API response payload.

### Requirement 5 — Docstrings on `cmd_lookup` handlers clarify the source-default semantics

**User Story:** As a developer reading `arr_cli/sonarr.py` or `arr_cli/radarr.py`, I want the docstring on `cmd_lookup` to explicitly call out that the `monitored` field on these records is the source default, not the user's library state, so that I do not propagate the misleading naming.

#### Requirement 5 Acceptance Criteria

1. WHEN a developer reads the docstring of `arr_cli.sonarr.cmd_lookup` THEN the docstring SHALL contain the phrase "the `monitored` field on these records is the source default (TVDB for Sonarr, TMDB for Radarr), not the user's library state" (verbatim).
2. WHEN a developer reads the docstring of `arr_cli.radarr.cmd_lookup` THEN the docstring SHALL contain the phrase "the `monitored` field on these records is the source default (TVDB for Sonarr, TMDB for Radarr), not the user's library state" (verbatim).

### Requirement 6 — SKILL.md recipes for lookup include the same clarification

**User Story:** As an operator using the SKILL.md recipes, I want the lookup recipes for Sonarr and Radarr to call out the source-default semantics of the `monitored` field, so that I do not mistake it for library state when reading the table.

#### Requirement 6 Acceptance Criteria

1. WHEN an operator opens SKILL.md and finds the Sonarr lookup recipe THEN the recipe SHALL contain a note that the `monitored` column in the rendered table reflects the source default (TVDB), not the user's library state.
2. WHEN an operator opens SKILL.md and finds the Radarr lookup recipe THEN the recipe SHALL contain a note that the `monitored` column in the rendered table reflects the source default (TMDB), not the user's library state.

### Requirement 7 — Unit tests cover routing, column shape, and fixture-based endpoint exercise

**User Story:** As a maintainer, I want the new commands and the renamed lookup column to be pinned by unit tests, so that future refactors cannot silently re-break them.

#### Requirement 7 Acceptance Criteria

1. WHEN `pytest tests/unit/test_sonarr.py` runs THEN it SHALL include a test that asserts `sonarr series` (no id) is dispatched to `/api/v3/series` (using `responses` to mock the HTTP call).
2. WHEN `pytest tests/unit/test_sonarr.py` runs THEN it SHALL include a test that asserts `sonarr series <id>` is dispatched to `/api/v3/series/{id}` (regression-safety for the single-fetch path).
3. WHEN `pytest tests/unit/test_radarr.py` runs THEN it SHALL include a test that asserts `radarr movie` (no id) is dispatched to `/api/v3/movie`.
4. WHEN `pytest tests/unit/test_radarr.py` runs THEN it SHALL include a test that asserts `radarr movie <id>` is dispatched to `/api/v3/movie/{id}` (regression-safety).
5. WHEN `pytest tests/unit/test_sonarr.py` runs THEN it SHALL include a test that asserts `sonarr lookup` `--human` output uses the column `defaultMonitored` and does NOT use the bare column `monitored`.
6. WHEN `pytest tests/unit/test_radarr.py` runs THEN it SHALL include a test that asserts `radarr lookup` `--human` output uses the column `defaultMonitored` and does NOT use the bare column `monitored`.
7. WHEN the new endpoint tests run THEN they SHALL be fixture-based (canned JSON payloads, no live HTTP) and SHALL exercise both the no-arg and with-id paths.

### Requirement 8 — CI / lint / smoke pass

**User Story:** As a maintainer, I want `make ci`, `make lint`, and `scripts/smoke.sh` (grammar check) to keep passing after this change, so that I do not regress the CI signal.

#### Requirement 8 Acceptance Criteria

1. WHEN `make ci` runs THEN it SHALL pass (== `make test && make secret-scan && make smoke-dry`).
2. WHEN `make lint` runs THEN it SHALL pass (`py_compile` sweep over `arr_cli/` and `tests/`, no syntax errors).
3. WHEN `scripts/smoke.sh` runs in `--dry-run` mode THEN it SHALL pass (CLI grammar checks for both `sonarr` and `radarr`).
4. IF `make ci` fails THEN the contributor SHALL NOT consider the change done.

## Non-Functional Requirements

### Performance

- The library-list endpoints SHALL NOT add any pass over the payload beyond the existing `summarize()` invocation; the new column lists are static literals.
- The dot-path traversal in `human._row_from_mapping` (already in place from PR #6) SHALL continue to handle the new columns without modification.
- The new unit tests SHALL complete in well under a second each (no live HTTP, no network calls).

### Security

- The fix SHALL NOT introduce any new code path that logs or persists the verbatim service payload or any summary field; the redacted `--debug` trace plumbing in `arr_cli.facade.transport` remains untouched.
- The library-list endpoints SHALL go through `arr_cli.facade` (auth injection, timeouts, error mapping) — no per-service file should construct HTTP calls directly.
- Any user-supplied id or search term SHALL be percent-encoded via `arr_cli.facade.transport.encode_path_segment` before URL construction.
- No new credential handling, no new config parsing. `arr.conf.example` SHALL remain placeholder-only.

### Reliability

- The library-list endpoints SHALL preserve the documented exit-code contract (`AGENTS.md §6`): `ConfigError=1`, `AuthError=2`, `NetworkError=3`, `HttpError=4`, `ParseError=5`.
- The library-list endpoints SHALL preserve `stdout` pipe-cleanliness: JSON on stdout, diagnostics on stderr.
- The CLI grammar check in `scripts/smoke.sh` SHALL continue to recognize both `sonarr series` (no id) and `sonarr series <id>` as valid invocations.

### Usability

- The fix SHALL be invisible to operators using the documented flag triplet (`<exe>` for default JSON, `--verbose` for verbatim JSON, `-h` for summary table, `--verbose -h` for verbatim table).
- The renamed `defaultMonitored` column SHALL be the ONLY column shown for the lookup path; operators reading the table should immediately understand the semantics.

## Hard Constraints

- **Read-only:** all endpoints are HTTP `GET`, no writes.
- **Facade-only plumbing:** auth, transport, config, and `--debug` redaction go through `arr_cli.facade` — extend the facade, do not bypass it.
- **Thin per-service CLIs:** HTTP calls live in the facade; `arr_cli/sonarr.py` and `arr_cli/radarr.py` only orchestrate via `output.emit`.
- **Stable exit codes** per `AGENTS.md §6` (`ConfigError=1`, `AuthError=2`, `NetworkError=3`, `HttpError=4`, `ParseError=5`).
- **Style:** two-space indent, type hints on public functions, Python ≥ 3.11.
- **No new credentials / config keys:** `arr.conf.example` stays placeholder-only.