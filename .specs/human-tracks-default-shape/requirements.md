# Requirements Document

## Introduction

The `arr-cli` suite renders three output shapes per command: a curated summary (default JSON for the 15 size-to-summary candidate commands), a verbatim service payload (`--verbose`), and a tabular readable view (`--human` / `-h`). Today, `arr_cli.facade.output.emit` correctly summarizes the JSON default and correctly passes the verbatim payload through `--verbose`, but the `--human` branch hands the **raw verbatim payload** to `human()` instead of the summary shape — so the table columns are derived from the full service payload (`NowPlayingItem.Name`, `NowPlayingItem.SeriesName`, `PlayState`, ...) and show `<null>` everywhere even though the no-flag JSON is the curated summary that obviously has data.

This feature makes `--human` track the default data shape: the table describes the same data as the no-flag JSON, in a different format. `--verbose --human` remains the deliberate escape hatch that renders the full verbatim payload as a table. The renderer-selection layer (`emit`) — not the summary renderers themselves — is the single place where the shape that flows into the table is decided; that is where the fix must land.

Value to users: operators running `jellyfin -h now`, `radarr -h queue`, `sonarr -h recent`, `seerr -h requests`, and `maintainerr -h pending` get a readable table whose rows describe the same items as the default JSON, instead of `<null>` columns pulled from the wrong payload shape.

## Requirements

### Requirement 1

**User Story:** As a self-hosted media stack operator, I want `jellyfin -h now` to render the same data shape that `jellyfin now` emits (the curated `now` summary), so that the table I read on the terminal is actually describing the JSON I would have parsed if I had skipped the flag.

#### Requirement 1 Acceptance Criteria

1. WHEN the user runs `jellyfin -h now` against a non-empty `/Sessions` payload THEN `arr_cli.facade.output.emit` SHALL render a `human()` table whose columns are `user`, `device`, `client`, `playing.type`, `playing.name`, `playing.series`, `playing.season`, `playing.episode`, `progress.position_ticks`, `progress.is_paused` and whose rows are the same items that `jellyfin now` returns in its default JSON summary.
2. WHEN the user runs `jellyfin -h now` THEN `arr_cli.facade.output.emit` SHALL NOT derive columns from `NowPlayingItem.*`, `PlayState`, or any other key that lives only in the verbatim `/Sessions` payload.
3. WHEN the user runs `jellyfin -h now` and any row's `playing.name` is non-empty in the no-flag JSON summary THEN the corresponding cell in the rendered table SHALL contain that same non-empty value (no `<null>` for fields the summary populated).
4. IF the default JSON shape for `jellyfin now` ever changes (new fields added to `_summary_jellyfin_now`) THEN `arr_cli.facade.output.emit` SHALL render those same new fields in the `jellyfin -h now` table without requiring any per-command wiring.
5. WHEN the user runs `jellyfin -h now` and the JSON `summarize("jellyfin", "now", payload)` result is `payload` unchanged (no summary renderer registered) THEN `arr_cli.facade.output.emit` SHALL fall back to the verbatim payload table so that the feature never silently breaks an existing command.

### Requirement 2

**User Story:** As a self-hosted media stack operator, I want every size-to-summary candidate command's `--human` table to describe the same items as its no-flag JSON summary, so that the two output formats are interchangeable reading aids rather than two windows onto two different shapes.

#### Requirement 2 Acceptance Criteria

1. WHEN the user runs `<exe> -h <cmd>` for any `(service, command)` key registered in `arr_cli.facade.output._SUMMARY_RENDERERS` THEN `arr_cli.facade.output.emit` SHALL first apply `summarize(service, command, payload)` and SHALL pass the summarized shape into `human()`, so the table columns and row values come from the summary, not from the raw service payload.
2. WHEN the user runs `radarr -h queue` THEN the rendered table SHALL describe the same items as `radarr queue` default JSON (the queue summary from `_summary_radarr_queue`).
3. WHEN the user runs `sonarr -h recent` THEN the rendered table SHALL describe the same items as `sonarr recent` default JSON (the recent summary from `_summary_sonarr_recent`).
4. WHEN the user runs `seerr -h requests` THEN the rendered table SHALL describe the same items as `seerr requests` default JSON (the requests summary from `_summary_seerr_requests`).
5. WHEN the user runs `maintainerr -h pending` THEN the rendered table SHALL describe the same items as `maintainerr pending` default JSON (the pending summary from `_summary_maintainerr_pending`).
6. WHEN the user runs `<exe> -h <cmd>` for any of the remaining 10 size-to-summary candidate commands (`jellyfin` recent/favorites/resume/latest; `radarr` wanted/recent; `sonarr` wanted/queue; `seerr` search/available) THEN the rendered table SHALL describe the same items as the corresponding no-flag JSON summary.
7. IF `summarize(service, command, payload)` returns `payload` unchanged because the command is not registered in `_SUMMARY_RENDERERS` THEN `arr_cli.facade.output.emit` SHALL pass `payload` straight into `human()` so verbatim-only commands (the 14 listed in `AGENTS.md §1`) keep their current behaviour with no regression.

### Requirement 3

**User Story:** As a self-hosted media stack operator, I want `--human` to never silently expand to the full verbatim payload for a size-to-summary command, so that a one-character `-h` cannot dump 6000+ lines of raw service JSON onto my terminal.

#### Requirement 3 Acceptance Criteria

1. WHEN the user runs `<exe> -h <cmd>` for any size-to-summary candidate command THEN `arr_cli.facade.output.emit` SHALL bound the rendered table to the same row budget as the summary (`limit` parameter forwarded into `human()`), and SHALL NOT call `human()` with the verbatim service payload for a command that has a summary renderer registered.
2. IF a regression reintroduces the bug where `--human` is fed the verbatim payload for a size-to-summary candidate command THEN `arr_cli.facade.output.emit` SHALL fail visibly (an assertion in the test suite, not a silent `print`) so the bug cannot ship.
3. WHEN the user runs `jellyfin -h now` against a `/Sessions` payload whose summarized shape has fewer than 10 rows THEN the rendered table SHALL contain fewer than 10 data rows plus a header and SHALL NOT expand to the verbatim row count.

### Requirement 4

**User Story:** As a self-hosted media stack operator, I want `--verbose --human` to remain an explicit escape hatch that renders the full verbatim payload as a table, so that I can still inspect the raw shape when I deliberately choose to.

#### Requirement 4 Acceptance Criteria

1. WHEN the user runs `<exe> --verbose -h <cmd>` (i.e. both flags together) THEN `arr_cli.facade.output.emit` SHALL render `human(payload, ...)` against the verbatim service payload, bypassing `summarize()` entirely, so the columns reflect the full data.
2. WHEN the user runs `jellyfin --verbose -h now` THEN the rendered table SHALL include the verbatim `/Sessions` columns (e.g. `NowPlayingItem.Name`, `NowPlayingItem.SeriesName`, `PlayState`) and SHALL NOT apply `_summary_jellyfin_now` first.
3. IF `human_mode` and `verbose_mode` are both True THEN `arr_cli.facade.output.emit` SHALL document this precedence (verbose wins for the data shape; human wins for the rendering format) in the `emit()` docstring so future readers do not have to re-derive it.

### Requirement 5

**User Story:** As a self-hosted media stack operator, I want the existing unit test suite under `tests/unit/test_output.py` to gain coverage that pins the new behaviour, so that the bug cannot silently regress.

#### Requirement 5 Acceptance Criteria

1. WHEN the renderer-selection layer changes are implemented THEN the contributor SHALL add at least one hermetic unit test in `tests/unit/test_output.py` per size-to-summary command path (`emit(..., human_mode=True)` → `summarize()` applied → `human()` invoked), using only `responses` / in-process mocks — no live HTTP.
2. WHEN the new tests run THEN `make ci` (== `make test && make secret-scan && make smoke-dry`) SHALL pass locally and SHALL remain hermetic (no network, no live service credentials).
3. WHEN the new tests run THEN `make lint` SHALL pass (two-space indent, Python ≥3.11 syntax, type hints on public functions).
4. WHEN a regression reintroduces verbatim payload into the `--human` branch for `jellyfin now` (or any size-to-summary command) THEN at least one of the new unit tests SHALL fail.

### Requirement 6

**User Story:** As an agent operating both Sage and Lily workspaces, I want the SKILL.md "Output formats" sections in both workspace copies to drop the "Known issue" / "workaround" / "does not currently match its intended design" callouts once the fix lands, so that future agents do not waste cycles re-discovering a bug that no longer exists.

#### Requirement 6 Acceptance Criteria

1. WHEN the renderer-selection fix is merged THEN the contributor SHALL edit `~/.openclaw/workspace/skills/media-cli/SKILL.md` "Output formats" section to remove any prose that calls out `--human` as a known issue, a workaround, or as not matching its intended design.
2. WHEN the renderer-selection fix is merged THEN the contributor SHALL edit `~/.openclaw/workspace-lily/skills/media-cli/SKILL.md` "Output formats" section to apply the same edit, so Sage and Lily stay in sync.
3. IF the SKILL.md prose did not previously contain such callouts THEN the contributor SHALL NOT add any new warnings about the `--human` renderer in either workspace copy.

## Non-Functional Requirements

### Performance

- The renderer-selection fix SHALL NOT add a second pass over the payload that materially increases wall-clock time for any of the 15 size-to-summary commands. `summarize(service, command, payload)` is already pure and runs in O(payload size); calling it once from the `human_mode` branch instead of letting `human()` walk the verbatim payload SHALL be at worst neutral.
- The `human()` invocation in the `human_mode` branch SHALL respect the existing `limit` and `max_width` knobs and SHALL NOT bypass them when the input is the summary shape.
- No new runtime dependencies SHALL be added to `pyproject.toml`; the fix uses `arr_cli.facade.output.summarize`, `arr_cli.facade.output.human`, and the existing `_SUMMARY_RENDERERS` dispatch table only.

### Security

- The renderer-selection fix SHALL NOT introduce any new code path that logs, echoes, or persists the verbatim service payload in a way that bypasses the existing `--debug` redaction (header values become `***<length>`). The redacted debug trace plumbing in `arr_cli.facade.transport` remains untouched.
- No new credential handling, no new config parsing, no new HTTP. The fix is local to `arr_cli/facade/output.py` (`emit`) and its tests.

### Reliability

- The renderer-selection fix SHALL preserve the documented exit-code contract (`AGENTS.md §6`): the five stable exit codes and the stderr-only diagnostics guarantee SHALL NOT change. `stdout` remains pipe-clean JSON for the no-flag and `--verbose` paths and pipe-clean tabular text for `--human`.
- When `summarize(service, command, payload)` returns `payload` unchanged (graceful default for commands not in `_SUMMARY_RENDERERS`), `emit` SHALL still hand that payload to `human()` so that verbatim-only commands keep their current behaviour byte-for-byte.
- When the renderer-selection fix is applied, `make ci` SHALL continue to pass (== `make test && make secret-scan && make smoke-dry`), and `make lint` SHALL continue to pass.

### Usability

- The fix SHALL be invisible to operators using the documented flag triplet: `jellyfin` (default JSON), `jellyfin --verbose` (verbatim JSON), `jellyfin -h now` (summary table), `jellyfin --verbose -h now` (verbatim table). All four outputs SHALL continue to look the same as before for commands that were never broken (the 14 verbatim-only commands), and SHALL be corrected for the 15 size-to-summary commands.
- The `emit()` docstring SHALL be updated to spell out the precedence (`--human` over `--verbose` for shape selection: summary shape by default, verbatim shape only when `--verbose` is also passed) so future readers do not have to re-derive it from the code.
- The fix SHALL land in the renderer-selection layer (`arr_cli.facade.output.emit`) and SHALL NOT require changes to any of the 17 `_summary_*` renderers or to any per-service CLI command function in `arr_cli/{jellyfin,radarr,sonarr,maintainerr,seerr}.py`. The summary renderers are fine as-is.
- The contributor SHALL run `make ci` and `make lint` locally before opening the PR, and both SHALL pass.