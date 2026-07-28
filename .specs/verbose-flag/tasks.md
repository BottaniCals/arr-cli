# Implementation Plan

## Task Overview

Implement the `--verbose` flag flip (per workboard ticket
`6a4dfded-9944-47e7-ad86-59a712e93fb0`, option (a) — incompatible flip).
The default JSON output for the 15 size-to-summary candidate commands
becomes a curated per-command summary sized for chat-agent consumption;
the previous verbatim JSON pass-through remains available via `--verbose`.
All verbatim behaviour is preserved for the 14 safe-to-leave-alone
commands. Implementation is split across (A) facade extensions in
`arr_cli/facade/output.py` and `arr_cli/facade/cli_common.py`, (B) the
15 per-command summary renderers plus a safe-access helper, (C) a
one-line `_emit` helper touch on each of the five per-service CLI files,
(D) hermetic unit tests under `tests/unit/`, and (E) docs + CI
verification. The five stable exit codes, the pipe-clean stdout
contract, and the `py.typed` PEP 561 marker are preserved by
construction. No new runtime dependencies; only stdlib `json` + existing
deps (`requests`, `PyYAML`, `pytest`, `responses`).

## Tasks

- [x] 1. Register `--verbose` universal flag in `arr_cli/facade/cli_common.py`
  - [ ] 1.1 Add `--verbose` argument immediately after the existing `--human` / `-h` entry in `build_parser`
    - File: `arr_cli/facade/cli_common.py`
    - Shape: `parser.add_argument("--verbose", action="store_true", default=False, help="...")` — followed by no short alias (REQ-2 AC5)
    - Help text is a single line per NFR-Usability: names the verbatim JSON behaviour and points at the default summary for size-to-summary commands
    - Update the `build_parser` docstring (currently enumerates the universal-flag set) to also enumerate the renderer priority chain: `--human` > `--verbose` > default summary (REQ-6 AC2)
  - [ ] 1.2 Confirm no signature change to `build_parser` and no other call site breaks
    - `args.verbose` is the new attribute; existing call sites use `args.human` only and remain untouched
    - `main_wrapper` is unchanged — `args.verbose` flows through `argparse` into the per-service `_emit` helpers exactly as `args.human` does today
    - _Requirements: REQ-2 AC5, REQ-6 AC2_

- [x] 2. Extend `arr_cli.facade.output.emit` signature with the priority chain
  - [ ] 2.1 Extend the public `emit(...)` signature with keyword-only `verbose_mode`, `service`, `command` parameters
    - File: `arr_cli/facade/output.py`
    - New signature: `def emit(payload, *, human_mode, verbose_mode=False, service="", command="", columns=None, limit=DEFAULT_LIMIT, max_width=DEFAULT_MAX_WIDTH, stream=None) -> None`
    - All existing keyword arguments keep their documented defaults; only `human_mode` stays required (consistent with the existing contract)
    - Branch order in the body (priority chain, REQ-3 AC1–AC4):
      1. `if human_mode: render via human(...)` — tabular view, return
      2. `elif verbose_mode: json.dumps(payload, ensure_ascii=False)` — verbatim, return
      3. `elif service and command and (service, command) in _SUMMARY_RENDERERS: rendered = summarize(service, command, payload); json.dumps(rendered, ensure_ascii=False)` — summary, return
      4. `else: json.dumps(payload, ensure_ascii=False)` — verbatim default
    - All four branches write to `out` (the resolved `stream` or `sys.stdout`) via `print(..., file=out)` to stay consistent with the existing stdout handling
  - [ ] 2.2 Confirm byte-identical fallback for callers that do not pass the new kwargs
    - When `service == ""` or `command == ""` or the lookup misses, the priority chain falls through to the default verbatim `json.dumps(payload, ensure_ascii=False)` — byte-identical to the pre-change behaviour (REQ-5 AC3, NFR-Reliability)
  - [ ] 2.3 Add a short docstring to `emit` describing the priority chain in one place
    - "Render `payload` on stdout. Priority: `--human` > `--verbose` > default summary (size-to-summary candidates) > verbatim JSON."
    - No mechanic comments; just the precedence order for future readers (REQ-3 AC5, NFR-Usability)
    - _Requirements: REQ-1 AC1–AC4, REQ-2 AC1–AC3, REQ-3 AC1–AC6, REQ-5 AC3, AC5_

- [x] 3. Add `arr_cli.facade.output.summarize` public function with graceful default
  - [ ] 3.1 Implement the public `summarize(service, command, payload)` function
    - File: `arr_cli/facade/output.py`
    - Signature: `def summarize(service: str, command: str, payload: Any) -> Any`
    - Logic: `key = (service, command); return _SUMMARY_RENDERERS[key](payload) if key in _SUMMARY_RENDERERS else payload`
    - Pure function: no I/O, no logging, no `print` (REQ-5 AC5)
    - Graceful default: empty `service` / `command` (key `("", "")`) and any unknown key both return `payload` unchanged (REQ-1 AC4, REQ-5 AC5)
  - [ ] 3.2 Add a short docstring describing the graceful default and the dispatch-table contract
    - Mention that the dispatch table is `_SUMMARY_RENDERERS` (module-level)
    - _Requirements: REQ-1 AC4, REQ-3 AC6, REQ-5 AC5_

- [x] 4. Build the `_SUMMARY_RENDERERS` dispatch table with 15 entries
  - [ ] 4.1 Declare the module-level `_SUMMARY_RENDERERS` table
    - File: `arr_cli/facade/output.py`
    - Type: `_SUMMARY_RENDERERS: dict[tuple[str, str], Callable[[Any], Any]] = {...}`
    - Build the dict at module import time (NFR-Performance: dispatch is a single dict lookup)
    - Registration order (matches the per-command summary spec in REQ-4):
      - `("jellyfin", "now")`, `("jellyfin", "recent")`, `("jellyfin", "favorites")`, `("jellyfin", "resume")`, `("jellyfin", "latest")`
      - `("radarr", "wanted")`, `("radarr", "queue")`, `("radarr", "recent")`
      - `("sonarr", "wanted")`, `("sonarr", "queue")`, `("sonarr", "recent")`
      - `("seerr", "requests")`, `("seerr", "search")`, `("seerr", "available")`
      - `("maintainerr", "pending")`
    - Exactly 15 entries; the 14 safe-to-leave-alone commands are deliberately not registered
  - [ ] 4.2 Add a one-line comment naming the source-of-truth for additions
    - "Add a new size-to-summary candidate by appending one `_summary_<service>_<command>` function and one entry here (REQ-3 AC6)."
    - The 14 safe-to-leave-alone commands are intentionally absent; this is the documented contract
    - _Requirements: REQ-3 AC6, REQ-4 AC2, REQ-4 AC3_

- [x] 5. Add safe-access helpers in `arr_cli/facade/output.py`
  - [ ] 5.1 Implement `_safe_get(payload, *path, default=None)` for nested-dict traversal
    - File: `arr_cli/facade/output.py`
    - Behaviour: walks `payload[path[0]][path[1]]...`, returning `default` on any `KeyError` / `TypeError` / `IndexError` (covers dict-missing, list-index-OOB, scalar-instead-of-dict, and `None` intermediates)
    - `default=None`; per-call overrideable to `0`, `False`, `""`, `[]`, `{}` as the spec requires
  - [ ] 5.2 Implement `_safe_getattr(obj, name, default=None)` for attribute traversal
    - Mirrors `_safe_get` for `obj.attr.subattr` walks; returns `default` on `AttributeError` / `TypeError`
    - Rarely needed (service payloads are JSON-decoded dicts), but provided for parity with the human-renderer helpers
  - [ ] 5.3 Add short docstrings on each helper describing the no-raise contract
    - Renderers rely on these helpers for every field access; the no-raise contract is what guarantees REQ-1 AC4 / REQ-5 AC5
    - _Requirements: REQ-1 AC4, REQ-5 AC5_

- [x] 6. Implement the 5 jellyfin summary renderers in `arr_cli/facade/output.py`
  - [ ] 6.1 Implement `_summary_jellyfin_now(payload)`
    - Top-level shape: array of objects (one per active Jellyfin `/Sessions` row); `[]` when no sessions
    - Per-session object: `{ user, device, client, playing, progress }` where `playing` is `null` when `NowPlayingItem` is missing/falsy (REQ-4 AC4)
    - `playing` keys: `type`, `name`, `series`, `season`, `episode` via `_safe_get`
    - `progress` keys: `position_ticks`, `is_paused` via `_safe_get` (default `0` / `False`)
    - Defensive: if `payload` is not a list, return `[]`; never raise (REQ-1 AC4)
  - [ ] 6.2 Implement `_summary_jellyfin_recent(payload)`
    - Top-level shape: array of `{ Name, Type, ProductionYear, SeriesName, UserData.LastPlayedDate }`
    - Defensive: non-list → `[]`; missing fields → `None` / `0`
  - [ ] 6.3 Implement `_summary_jellyfin_favorites(payload)`
    - Top-level shape: array of `{ Name, Type, ProductionYear, SeriesName }`
    - Defensive: non-list → `[]`; missing fields → `None` / `0`
  - [ ] 6.4 Implement `_summary_jellyfin_resume(payload)`
    - Top-level shape: array of `{ Name, Type, ProductionYear, SeriesName, UserData.PlaybackPositionTicks, UserData.PlayCount }`
    - Defensive: non-list → `[]`; missing field defaults `0` (int fields)
  - [ ] 6.5 Implement `_summary_jellyfin_latest(payload)`
    - Top-level shape: array of `{ Name, Type, ProductionYear, SeriesName, DateCreated }`
    - Defensive: non-list → `[]`; missing fields → `None` / `0`
  - [ ] 6.6 Confirm all five renderers register in `_SUMMARY_RENDERERS` (covered by Task 4.1)
    - _Requirements: REQ-1 AC1, AC4, REQ-4 AC1, AC4, AC5_

- [x] 7. Implement the 3 radarr summary renderers in `arr_cli/facade/output.py`
  - [ ] 7.1 Implement `_summary_radarr_wanted(payload)`
    - Top-level shape: array of `{ title, year, tmdbId, monitored }`
    - Defensive: non-list → `[]`; missing fields → `None` / `0` / `False`
  - [ ] 7.2 Implement `_summary_radarr_queue(payload)`
    - Top-level shape: array of `{ title, status, trackedDownloadStatus, size, sizeleft }`
    - Defensive: non-list → `[]`; missing fields → `None` / `0`
  - [ ] 7.3 Implement `_summary_radarr_recent(payload)`
    - Top-level shape: array of `{ "movie": {title, year}, eventType, date }` (nested `movie` object per spec)
    - Defensive: non-list → `[]`; missing `movie` → `{"title": None, "year": 0}`; missing fields → `None` / `0`
  - [ ] 7.4 Confirm all three renderers register in `_SUMMARY_RENDERERS` (covered by Task 4.1)
    - _Requirements: REQ-1 AC1, AC4, REQ-4 AC1, AC5_

- [x] 8. Implement the 3 sonarr summary renderers in `arr_cli/facade/output.py`
  - [ ] 8.1 Implement `_summary_sonarr_wanted(payload)`
    - Top-level shape: array of `{ title, seasonNumber, episodeNumber, airDate, monitored }`
    - Defensive: non-list → `[]`; missing fields → `None` / `0` / `False`
  - [ ] 8.2 Implement `_summary_sonarr_queue(payload)`
    - Top-level shape: array of `{ title, status, trackedDownloadStatus, size, sizeleft }`
    - Defensive: non-list → `[]`; missing fields → `None` / `0`
  - [ ] 8.3 Implement `_summary_sonarr_recent(payload)`
    - Top-level shape: array of `{ "series": {title}, "episode": {title}, eventType, date }` (nested `series` and `episode` objects per spec)
    - Defensive: non-list → `[]`; missing nested objects → `{"title": None}`; missing fields → `None` / `0`
  - [ ] 8.4 Confirm all three renderers register in `_SUMMARY_RENDERERS` (covered by Task 4.1)
    - _Requirements: REQ-1 AC1, AC4, REQ-4 AC1, AC5_

- [x] 9. Implement the 3 seerr summary renderers in `arr_cli/facade/output.py`
  - [ ] 9.1 Implement `_summary_seerr_requests(payload)`
    - Top-level shape: array of `{ title, type, status, createdAt, requestedBy.displayName }`
    - Defensive: non-list → `[]`; missing `requestedBy` → `{"displayName": None}`; missing fields → `None`
  - [ ] 9.2 Implement `_summary_seerr_search(payload)`
    - Top-level shape: array of `{ title, mediaType, releaseDate, mediaInfo.tmdbId }`
    - Defensive: non-list → `[]`; missing `mediaInfo` → `{"tmdbId": 0}`; missing fields → `None` / `0`
  - [ ] 9.3 Implement `_summary_seerr_available(payload)`
    - Top-level shape: array of `{ title, mediaType, releaseDate, mediaInfo.status }`
    - Defensive: non-list → `[]`; missing `mediaInfo` → `{"status": 0}`; missing fields → `None` / `0`
  - [ ] 9.4 Confirm all three renderers register in `_SUMMARY_RENDERERS` (covered by Task 4.1)
    - _Requirements: REQ-1 AC1, AC4, REQ-4 AC1, AC5_

- [x] 10. Implement `_summary_maintainerr_pending` in `arr_cli/facade/output.py`
  - [ ] 10.1 Implement the single maintainerr renderer
    - Top-level shape: array of `{ title, mediaCount, deleteAfterDays, isOnHold }`
    - Defensive: non-list → `[]`; missing fields → `None` / `0` / `False`
  - [ ] 10.2 Confirm registration in `_SUMMARY_RENDERERS` (covered by Task 4.1)
    - `maintainerr health` and `maintainerr storage` are deliberately NOT in the table (out of scope per workboard ticket)
    - _Requirements: REQ-1 AC1, AC4, REQ-4 AC1, AC5_

- [x] 11. Update `arr_cli/jellyfin.py::_emit` to thread `verbose_mode`, `service`, and `command` into `output.emit`
  - [ ] 11.1 Add `SERVICE_NAME = "jellyfin"` module-level constant (if not already present)
    - File: `arr_cli/jellyfin.py`
    - Reuse the existing constant if the file already names one; otherwise declare it next to the other module-level constants
  - [ ] 11.2 Update the `_emit` helper body to the canonical shape
    - File: `arr_cli/jellyfin.py`
    - Body: `output.emit(payload, human_mode=bool(getattr(args, "human", False)), verbose_mode=bool(getattr(args, "verbose", False)), service=SERVICE_NAME, command=str(getattr(args, "command", "") or ""), columns=columns, limit=int(getattr(args, "limit", 20) or 20))`
    - Return `0` after the call (existing behaviour)
  - [ ] 11.3 Confirm no command-handler body changes — only the `_emit` helper is touched
    - The 8 jellyfin handlers (`cmd_now`, `cmd_resume`, `cmd_recent`, `cmd_nextup`, `cmd_latest`, `cmd_search`, `cmd_item`, `cmd_favorites`) remain byte-identical
    - Specifically, `cmd_now` is the canonical-size-to-summary candidate and exercises the new branch
    - _Requirements: REQ-2 AC1, AC3, REQ-3 AC1–AC5, REQ-5 AC3_

- [x] 12. Update `arr_cli/radarr.py::_emit` to thread `verbose_mode`, `service`, and `command` into `output.emit`
  - [ ] 12.1 Add `SERVICE_NAME = "radarr"` module-level constant (if not already present)
    - File: `arr_cli/radarr.py`
  - [ ] 12.2 Update the `_emit` helper body to the canonical shape (same as Task 11.2)
    - File: `arr_cli/radarr.py`
  - [ ] 12.3 Confirm no command-handler body changes — only the `_emit` helper is touched
    - The 6 radarr handlers (`cmd_calendar`, `cmd_wanted`, `cmd_queue`, `cmd_recent`, `cmd_lookup`, `cmd_movie`) remain byte-identical
    - `cmd_wanted` is the size-to-summary candidate that exercises the new branch
    - _Requirements: REQ-2 AC1, AC3, REQ-3 AC1–AC5, REQ-5 AC3_

- [x] 13. Update `arr_cli/sonarr.py::_emit` to thread `verbose_mode`, `service`, and `command` into `output.emit`
  - [ ] 13.1 Add `SERVICE_NAME = "sonarr"` module-level constant (if not already present)
    - File: `arr_cli/sonarr.py`
  - [ ] 13.2 Update the `_emit` helper body to the canonical shape (same as Task 11.2)
    - File: `arr_cli/sonarr.py`
  - [ ] 13.3 Confirm no command-handler body changes — only the `_emit` helper is touched
    - The 6 sonarr handlers (`cmd_calendar`, `cmd_wanted`, `cmd_queue`, `cmd_recent`, `cmd_lookup`, `cmd_series`) remain byte-identical
    - `cmd_wanted` is the size-to-summary candidate that exercises the new branch
    - _Requirements: REQ-2 AC1, AC3, REQ-3 AC1–AC5, REQ-5 AC3_

- [x] 14. Update `arr_cli/maintainerr.py::_emit` to thread `verbose_mode`, `service`, and `command` into `output.emit`
  - [ ] 14.1 Add `SERVICE_NAME = "maintainerr"` module-level constant (if not already present)
    - File: `arr_cli/maintainerr.py`
  - [ ] 14.2 Update the `_emit` helper body to the canonical shape (same as Task 11.2)
    - File: `arr_cli/maintainerr.py`
  - [ ] 14.3 Confirm no command-handler body changes — only the `_emit` helper is touched
    - The 3 maintainerr handlers (`cmd_pending`, `cmd_storage`, `cmd_health`) remain byte-identical
    - `cmd_pending` is the size-to-summary candidate that exercises the new branch; `cmd_health` is explicitly out of scope (verbatim stays put)
    - _Requirements: REQ-2 AC1, AC3, REQ-3 AC1–AC5, REQ-5 AC3_

- [x] 15. Update `arr_cli/seerr.py::_emit` to thread `verbose_mode`, `service`, and `command` into `output.emit`
  - [ ] 15.1 Add `SERVICE_NAME = "seerr"` module-level constant (if not already present)
    - File: `arr_cli/seerr.py`
  - [ ] 15.2 Update the `_emit` helper body to the canonical shape (same as Task 11.2)
    - File: `arr_cli/seerr.py`
  - [ ] 15.3 Confirm no command-handler body changes — only the `_emit` helper is touched
    - The 6 seerr handlers (`cmd_requests`, `cmd_request_count`, `cmd_search`, `cmd_available`, `cmd_media`, `cmd_user`) remain byte-identical
    - `cmd_requests` is the size-to-summary candidate that exercises the new branch; `cmd_request_count` is explicitly out of scope (verbatim stays put)
    - _Requirements: REQ-2 AC1, AC3, REQ-3 AC1–AC5, REQ-5 AC3_

- [x] 16. Add `--verbose` parser tests in `tests/unit/test_cli_common.py`
  - [ ] 16.1 Add 3 tests covering the new flag
    - File: `tests/unit/test_cli_common.py`
    - Test 1: `build_parser().parse_args(["--verbose"])` → `args.verbose is True` (REQ-2 AC5)
    - Test 2: `build_parser().parse_args([])` → `args.verbose is False` (default; REQ-2 AC5)
    - Test 3: `build_parser().parse_args(["--verbose", "--human"])` → both `args.verbose` and `args.human` are True simultaneously (no conflict; the priority chain resolves at emit time)
  - [ ] 16.2 Confirm tests use stdlib `unittest` + `unittest.mock` only (matches the existing file's conventions)
    - _Requirements: REQ-2 AC5, REQ-6 AC4_

- [x] 17. Add dispatch + threading tests in `tests/unit/test_output.py`
  - [ ] 17.1 Add a `TestEmitPriorityChain` class with 5 cases
    - File: `tests/unit/test_output.py`
    - Case 1: `human_mode=True, verbose_mode=False` → tabular human view on stdout (REQ-3 AC1)
    - Case 2: `human_mode=False, verbose_mode=True` on a size-to-summary candidate → verbatim JSON (REQ-3 AC2)
    - Case 3: `human_mode=False, verbose_mode=False` on a size-to-summary candidate → summary (REQ-3 AC3)
    - Case 4: `human_mode=False, verbose_mode=False` on a non-candidate command → verbatim JSON unchanged (REQ-3 AC4)
    - Case 5: `human_mode=True, verbose_mode=True` → human table wins; `--verbose` has no effect on rendering (REQ-3 AC1, AC5)
    - Use `contextlib.redirect_stdout` + `io.StringIO` (matches the existing file's pattern)
  - [ ] 17.2 Add a `TestSummarizeGracefulDefault` class with 3 cases
    - File: `tests/unit/test_output.py`
    - Case 1: `summarize("jellyfin", "now", payload)` returns the summary shape (known key)
    - Case 2: `summarize("unknown_service", "unknown_command", payload)` returns `payload` unchanged (REQ-1 AC4)
    - Case 3: `summarize("", "", payload)` returns `payload` unchanged (graceful default for empty key)
  - [ ] 17.3 Add a `TestServiceCommandThreading` class with 6 cases
    - File: `tests/unit/test_output.py`
    - Case 1: `emit(payload, service="jellyfin", command="now", ...)` invokes `_summary_jellyfin_now` (verified via `unittest.mock.patch` on the registered renderer); assert the serialised summary bytes on stdout
    - Case 2: `emit(payload, ...)` with no `service` / `command` → verbatim JSON; the registered renderer is not invoked (the new seam's empty-key fallback)
    - Case 3: `emit(payload, service="jellyfin", command="item", ...)` (non-candidate key) → verbatim JSON; the lookup misses
    - Case 4: `output.summarize("unknown_service", "unknown_command", payload)` → `payload` unchanged (also covered in 17.3; documented as the same seam)
    - Case 5: `output.summarize("", "", payload)` → `payload` unchanged (also covered in 17.3; documented as the empty-key case)
    - Case 6: per-service `_emit` smoke — instantiate a minimal `argparse.Namespace(command="now", human=False, verbose=False, limit=20)` and assert `jellyfin._emit(payload, args)` calls `output.emit` with `service="jellyfin"` and `command="now"` keyword arguments (verified via `unittest.mock.patch` on `output.emit`); repeat or sample for one service per file to lock the threading in each per-service file
    - _Requirements: REQ-1 AC4, REQ-2 AC1, REQ-3 AC1–AC6, REQ-5 AC3, AC5_

- [x] 18. Add 15 per-renderer unit tests in `tests/unit/test_output.py`
  - [ ] 18.1 Add 5 jellyfin per-renderer tests
    - File: `tests/unit/test_output.py`
    - One test each for `_summary_jellyfin_now`, `_summary_jellyfin_recent`, `_summary_jellyfin_favorites`, `_summary_jellyfin_resume`, `_summary_jellyfin_latest`
    - Each test builds a hand-crafted payload matching the documented service shape and asserts the summary shape matches the requirements bullet list byte-for-byte (via `json.loads` for structural assertion, not string-based)
  - [ ] 18.2 Add 3 radarr per-renderer tests
    - Same pattern for `_summary_radarr_wanted`, `_summary_radarr_queue`, `_summary_radarr_recent`
  - [ ] 18.3 Add 3 sonarr per-renderer tests
    - Same pattern for `_summary_sonarr_wanted`, `_summary_sonarr_queue`, `_summary_sonarr_recent`
  - [ ] 18.4 Add 3 seerr per-renderer tests
    - Same pattern for `_summary_seerr_requests`, `_summary_seerr_search`, `_summary_seerr_available`
  - [ ] 18.5 Add 1 maintainerr per-renderer test
    - For `_summary_maintainerr_pending`
  - [ ] 18.6 Confirm all 15 tests are hermetic — no live HTTP, no `responses` mocking needed (the renderers are pure functions over pre-decoded payloads; AGENTS.md §7.5)
    - _Requirements: REQ-1 AC1, AC4, AC5, REQ-4 AC1, AC4, AC5, REQ-6 AC4_

- [x] 19. Add `jellyfin now` end-to-end handler test (summary + verbose paths) in `tests/unit/test_jellyfin.py`
  - [ ] 19.1 Add the `cmd_now` smoke test
    - File: `tests/unit/test_jellyfin.py`
    - Two sub-cases in one test or two sibling tests:
      - `cmd_now(...)` without `--verbose` → stdout is the curated summary shape (REQ-6 AC4(a))
      - `cmd_now(...)` with `--verbose` → stdout is the verbatim service payload (REQ-6 AC4(b))
    - Mock the upstream HTTP via `responses` (matches the existing file's pattern; no live HTTP)
    - Assert exit code `0` and pipe-clean JSON on stdout
    - _Requirements: REQ-2 AC1, REQ-6 AC4(a), AC4(b)_

- [x] 20. Add `radarr wanted` end-to-end handler test in `tests/unit/test_radarr.py`
  - [ ] 20.1 Add the `cmd_wanted` smoke test
    - File: `tests/unit/test_radarr.py`
    - Two sub-cases (summary + verbose), same pattern as Task 19.1
    - Mock the upstream HTTP via `responses`
    - _Requirements: REQ-2 AC1, REQ-6 AC4_

- [x] 21. Add `sonarr wanted` end-to-end handler test in `tests/unit/test_sonarr.py`
  - [ ] 21.1 Add the `cmd_wanted` smoke test
    - File: `tests/unit/test_sonarr.py`
    - Two sub-cases (summary + verbose), same pattern as Task 19.1
    - Mock the upstream HTTP via `responses`
    - _Requirements: REQ-2 AC1, REQ-6 AC4_

- [x] 22. Add `seerr requests` end-to-end handler test in `tests/unit/test_seerr.py`
  - [ ] 22.1 Add the `cmd_requests` smoke test
    - File: `tests/unit/test_seerr.py`
    - Two sub-cases (summary + verbose), same pattern as Task 19.1
    - Mock the upstream HTTP via `responses`
    - _Requirements: REQ-2 AC1, REQ-6 AC4_

- [x] 23. Add `maintainerr pending` end-to-end handler test in `tests/unit/test_maintainerr.py`
  - [ ] 23.1 Add the `cmd_pending` smoke test
    - File: `tests/unit/test_maintainerr.py`
    - Two sub-cases (summary + verbose), same pattern as Task 19.1
    - Mock the upstream HTTP via `responses`
    - _Requirements: REQ-2 AC1, REQ-6 AC4_

- [x] 24. Update `CHANGELOG.md` with the flip entry and the new `--verbose` flag
  - [ ] 24.1 Add a new release section under the existing `[MVP]` entry
    - File: `CHANGELOG.md`
    - Header: `## [Unreleased]` (or the next-release header per repo convention) above the `[MVP]` entry
    - Sub-sections: `### Changed` (default-output flip), `### Added` (`--verbose` flag), `### Breaking` (link to workboard ticket `6a4dfded-9944-47e7-ad86-59a712e93fb0`)
    - Explicit callout: "Default JSON output for the 15 size-to-summary candidate commands is now a curated per-command summary; pass `--verbose` for the verbatim service payload (workboard ticket `6a4dfded-9944-47e7-ad86-59a712e93fb0`, option (a))."
  - [ ] 24.2 Confirm the entry preserves the existing Keep-a-Changelog format conventions used by the file
    - _Requirements: REQ-6 AC1, REQ-6 AC3_

- [x] 25. Update `README.md` §4 with the new flag and the BREAKING note
  - [ ] 25.1 Update the universal-flag row(s) in §4 to include `--verbose`
    - File: `README.md`
    - Add a row mirroring the existing `--human` / `-h` row: `| \`--verbose\` | emit the verbatim service JSON payload instead of the curated summary (default for size-to-summary commands) |`
    - Place the new row directly below the `--human` / `-h` row
  - [ ] 25.2 Add a prominent "BREAKING" note in §1 (or whichever section describes the default output behaviour)
    - Wording: "**BREAKING:** the default JSON output for the 15 size-to-summary candidate commands is now a curated per-command summary; pass `--verbose` to restore the verbatim service payload. Workboard ticket `6a4dfded-9944-47e7-ad86-59a712e93fb0`."
  - [ ] 25.3 Confirm the obsolete "default JSON output is verbatim" phrasing is removed (REQ-6 AC1)
    - The replacement phrasing is "default JSON output is a curated per-command summary for the 15 size-to-summary commands; pass `--verbose` for the verbatim service payload"
    - _Requirements: REQ-6 AC1, AC3_

- [x] 26. Confirm `arr_cli/py.typed` and `pyproject.toml` remain consistent
  - [ ] 26.1 Confirm `arr_cli/py.typed` is unchanged
    - The PEP 561 marker file is preserved byte-for-byte; no deletion, no rename
    - The new `output.summarize` public function declares its full type signature (`(str, str, Any) -> Any`) so downstream type checkers see it (NFR-Technical Standards)
  - [ ] 26.2 Confirm `pyproject.toml` has no new runtime dependencies
    - The renderer is plain `json` + dict comprehensions + safe-access helpers; no new imports under `[project.dependencies]`
    - The existing dev deps (`pytest`, `responses`) are unchanged; no new entries under `[project.optional-dependencies]`
    - Run `pip install -e ".[dev]"` to confirm the lockfile state is unchanged
    - _Requirements: REQ-6 AC1, NFR-Security, NFR-Technical Standards_

- [x] 27. Run CI verification — `make ci` and `make lint` stay green
  - [ ] 27.1 Run `make ci` (test + secret-scan + smoke-dry)
    - Command: `make ci` (or `make test && make secret-scan && make smoke-dry` for explicit per-target feedback)
    - All targets must pass green; the test target exercises the new ~57 test cases against the existing ~150
    - `make secret-scan` must remain green; `arr.conf.example` is placeholder-only and unchanged
    - `make smoke-dry` must remain green; `--dry-run` mode is CI-safe and the smoke script is unchanged
  - [ ] 27.2 Run `make lint` (py_compile sweep over `arr_cli/` and `tests/`)
    - Command: `make lint`
    - Must pass green; the new modules (`output.py` extended, `cli_common.py` extended, five per-service `_emit` helpers touched) and the new test files must all compile
  - [ ] 27.3 Confirm the umbrella test gates in `AGENTS.md §3` are not weakened
    - No new skip markers, no new `pytest.mark.skip` introduced in `tests/unit/`
    - No new live-HTTP tests added; the hermetic contract of `tests/unit/` is preserved
    - `tests/integration/` is unchanged; still opt-in via `pytest --run-integration`
    - _Requirements: REQ-6 AC5, NFR-Reliability_
