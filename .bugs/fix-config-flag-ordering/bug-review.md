# Bug Report

## Bug Summary

The five arr-cli executables (`jellyfin`, `radarr`, `sonarr`, `maintainerr`, `seerr`) only register their universal flags (`--config`, `--debug`, `--quiet`, `--human`/`-h`, `--verbose`, `--connect-timeout`, `--read-timeout`, `--retry`, `--deadline`, `--limit`) on the top-level `argparse` parser. None of the per-service subcommand parsers inherit them, so the documented invocation form `jellyfin now --config /path/to/arr.local.yaml` (`README.md §2`) fails with an argparse usage error. The argparse usage error also exits with code `2`, which collides with the documented stable exit code for `AuthError` (`README.md §7`, `AGENTS.md §6`); scripts that branch on exit code misread a parse error as an auth failure.

## Bug Details

### Expected Behavior

1. `jellyfin now --config /path/to/arr.local.yaml` (and the analogous invocation for `radarr`, `sonarr`, `maintainerr`, `seerr`) should be accepted by argparse, with `args.config` populated for `main_wrapper`'s `load_config` call. The exact command shape is documented in `README.md §2` and `README.md §6`.
2. `jellyfin now --help` (and the equivalent for every subcommand of every CLI) should list every universal flag (`--config`, `--debug`, `--quiet`, `--human`, `--verbose`, `--connect-timeout`, `--read-timeout`, `--retry`, `--deadline`, `--limit`) in addition to the subcommand's own positionals / options. Per `README.md §6`: "Run any subcommand with `--help` for the per-service synopsis and flags."
3. Any argparse / argument-parse error (bad flag, missing positional, unknown subcommand, etc.) should surface as a structured stderr line shaped `service=config op=parse …` and exit with code `1` (`ConfigError`). Per `AGENTS.md §6`, exit code `1` is reserved for `ConfigError`, which explicitly includes "malformed CLI date input" today; parse failures are the same shape of failure.

### Actual Behavior

1. Putting `--config` after the subcommand is rejected with `unrecognized arguments: --config /tmp/arr.conf` and the process exits with code `2`.
2. The subcommand `--help` listing only shows `-h, --help` (no universal flags), so operators have no way to discover the universal flags from a subcommand's help.
3. The argparse error exits with code `2`, which is the documented `AuthError` code (`README.md §7` table; `AGENTS.md §6` table). Anything scripting against the stable exit-code table mis-classifies the parse failure as an auth failure.

### Steps to Reproduce

All commands run from `/workspace/projects/media-cli` against the installed `jellyfin` console script.

1. `jellyfin now --config /tmp/arr.conf; echo "EXIT=$?"`

   Stderr:
   ```text
   usage: jellyfin [--help] [--config PATH] [--debug | --no-debug]
                   [--quiet | --no-quiet] [--human] [--verbose]
                   [--connect-timeout SECONDS] [--read-timeout SECONDS]
                   [--retry N] [--deadline SECONDS] [--limit N]
                   COMMAND ...
   jellyfin: error: unrecognized arguments: --config /tmp/arr.conf
   ```
   Exit code: `2`. (This is **Bug 1 + Bug 2** — argparse rejects the documented form and exits with the AuthError code.)

2. `jellyfin --config /tmp/arr.conf now; echo "EXIT=$?"`

   Stderr:
   ```text
   service=config op=load message=config file not found: /tmp/arr.conf. Copy arr.conf.example to that path and fill in your values.
   ```
   Exit code: `1`. The un-documented order works today — `load_config` emits the structured `ConfigError` line. This is the only working form.

3. `jellyfin now --help; echo "EXIT=$?"`

   Stderr (and stdout):
   ```text
   usage: jellyfin now [-h]

   options:
     -h, --help  show this help message and exit
   ```
   Exit code: `0`. **Bug 3** — `--config` and the other universal flags are absent from the per-subcommand help.

4. The same three behaviors were confirmed for the other four CLIs:
   - `radarr recent --config /tmp/arr.conf` → `radarr: error: unrecognized arguments: --config /tmp/arr.conf`, exit `2`.
   - `radarr recent --help` → only `-h, --help` listed, exit `0`.
   - `sonarr recent --config /tmp/arr.conf` → same argparse failure, exit `2`.
   - `maintainerr recent --config /tmp/arr.conf` → `error: argument COMMAND: invalid choice: 'recent' (choose from 'pending', 'storage', 'health')`, exit `2`. (`recent` is not a maintainerr subcommand — used to provoke a different argparse failure path; same exit code 2.)
   - `seerr recent --config /tmp/arr.conf` → `error: argument COMMAND: invalid choice: 'recent' (choose from 'requests', 'request-count', 'search', 'available', 'media', 'user')`, exit `2`.

The exit-`2` parse-error response is identical across all five CLIs.

## Impact Assessment

### Severity

- [x] High - Major functionality broken

(Bugs 1 and 2 are both High per the caller; Bug 3 is Low but is fixed by the same change.)

### Affected Features

- **Universal-flag CLI surface** — the documented invocation form `jellyfin <sub> --config <path>` fails for every subcommand of every CLI. Operators reading `README.md §2` cannot follow the instructions as written; they have to discover the un-documented "flag before subcommand" form by trial and error.
- **Stable exit-code contract** (`README.md §7`, `AGENTS.md §6`) — argparse's usage-error exit `2` collides with `AuthError`. Any operator script (or downstream tooling like Lily / Sage, per the caller) that branches on the five stable codes misinterprets a malformed invocation as an auth failure, triggering the wrong remediation path.
- **Discoverability** — `jellyfin now --help` (and analogues) do not surface the universal flags, so the only place an operator can find them is the top-level `<cli> --help`. This is consistent with `--help` on the subcommand level failing its documentation role (caller marks this Low).
- **Pipeline hygiene** — the existing `service=... op=... status=... message=...` structured-line contract (`arr_cli/facade/errors.py`, `cli_common.main_wrapper`) is bypassed on argparse errors; the stderr written today is argparse's raw usage hint, which is a different shape from the rest of the error model.

## Additional Context

### Error Messages

```text
$ jellyfin now --config /tmp/arr.conf; echo "EXIT=$?"
usage: jellyfin [--help] [--config PATH] [--debug | --no-debug]
                [--quiet | --no-quiet] [--human] [--verbose]
                [--connect-timeout SECONDS] [--read-timeout SECONDS]
                [--retry N] [--deadline SECONDS] [--limit N]
                COMMAND ...
jellyfin: error: unrecognized arguments: --config /tmp/arr.conf
EXIT=2

$ jellyfin --config /tmp/arr.conf now; echo "EXIT=$?"
service=config op=load message=config file not found: /tmp/arr.conf. Copy arr.conf.example to that path and fill in your values.
EXIT=1

$ jellyfin now --help; echo "EXIT=$?"
usage: jellyfin now [-h]

options:
  -h, --help  show this help message and exit
EXIT=0
```

### Related Issues

- Caller notes: "Lily + Sage both depend on stable arg + exit semantics." (Caller-supplied bug context.)
- The same top-level-only flag pattern will recur if a sixth service is added under `parents=` is not adopted; the fix here also future-proofs the surface.
- `scripts/smoke.sh` (dry-run mode) exercises `<exe> --help` only and is not affected by Bugs 1 or 2; no smoke-script change is required.

## Analysis

### Investigation Summary

The investigation traced the bug from the documented surface (`README.md §2`, `§6`, `§7`) into the parser construction, then reproduced it at the shell.

1. Read `arr_cli/facade/cli_common.py` end-to-end. `build_parser` (lines 131–319) registers every universal flag on the *top-level* parser via individual `parser.add_argument(...)` calls. `main_wrapper` (lines 365–500) constructs a default parser via `build_parser` when the caller does not supply one, and accepts a caller-built parser via the `parser=` keyword.
2. `grep -n "subparsers.add_parser" arr_cli/` showed that all five service modules (jellyfin, radarr, sonarr, maintainerr, seerr) use `subparsers.add_parser(...)` for every per-command subparser, and `grep -n "parents=" arr_cli/` returned **zero hits** — no `parents=` argument is ever passed. Confirmed: universal flags are not shared with subparsers.
3. `main_wrapper` (lines 435–441 in `cli_common.py`) catches `SystemExit` from `parse_args()` and **returns `int(exc.code)` unchanged** (the comment explicitly says "we surface that unchanged because it is already a well-formed signal to the shell"). For argparse usage errors `exc.code` is `2`; this is the source of the Bug 2 exit-code collision.
4. Reproduced the bug at the shell against the installed `jellyfin`, `radarr`, `sonarr`, `maintainerr`, and `seerr` console scripts (Steps to Reproduce above). Every CLI exhibits both Bugs 1 and 3; every CLI also surfaces the Bug 2 exit-`2` collision for *any* argparse error (unrecognized flag, missing positional, unknown subcommand, etc.).
5. Read `arr_cli/facade/errors.py` to confirm the documented exit-code table: `1 = ConfigError`, `2 = AuthError`, `3 = NetworkError`, `4 = HttpError`, `5 = ParseError`. The argparse path bypasses this map entirely today.
6. Read `tests/unit/test_cli_common.py` (956 lines) and `tests/unit/test_perf_budgets.py` (uses `subprocess.run([sys.executable, "-c", f"from {module_name} import main; main(...)"], capture_output=True, text=True, ...)`) to confirm the existing subprocess-invocation pattern the new tests should follow. The existing tests around `argparse` use in-process `parse_args()` and `contextlib.redirect_stderr`; the caller explicitly asks for *subprocess*-invocation of the console scripts for the new regression tests.

### Root Cause

Two independent root causes combine into the three observable bugs:

1. **Top-level-only universal-flag registration.** `arr_cli/facade/cli_common.build_parser` registers `--config`, `--debug`/`--no-debug`, `--quiet`/`--no-quiet`, `--human`/`-h`, `--verbose`, `--connect-timeout`, `--read-timeout`, `--retry`, `--deadline`, `--limit` on the top-level `argparse.ArgumentParser` only. The per-service modules (`arr_cli/jellyfin.py` `build_jellyfin_parser`, `arr_cli/radarr.py` `build_radarr_parser`, `arr_cli/sonarr.py` `build_sonarr_parser`, `arr_cli/maintainerr.py` `build_maintainerr_parser`, `arr_cli/seerr.py` `build_seerr_parser`) call `parser.add_subparsers(...)` and then `subparsers.add_parser("now", ...)` etc. with **no `parents=` argument**. argparse does not propagate flags from the parent to subparsers unless `parents=[...]` is supplied. This is the underlying cause of Bugs 1 and 3: the subparser never sees `--config` (and the other nine universal flags), so it rejects it and never documents it.

2. **`main_wrapper` re-surfaces `SystemExit` from argparse.** `main_wrapper` (`arr_cli/facade/cli_common.py` lines 435–441) wraps `parser.parse_args(...)` in `try / except SystemExit` and returns `int(exc.code)` (typically `2`) directly. argparse's usage errors (`unrecognized arguments`, missing required argument, invalid choice) all `sys.exit(2)`, so every argument-parse failure currently propagates as exit `2` — the documented `AuthError` code. This is the underlying cause of Bug 2: the wrapper treats argparse's exit code as authoritative rather than translating it through the documented `ArrError` map.

## Technical Details

### Affected Code Locations

- **File**: `arr_cli/facade/cli_common.py`
  - **Function/Method**: `build_parser(prog, description, epilog)`
  - **Lines**: 131–319 (the function body; the universal-flag `add_argument` calls populate lines ~167–319 covering `--config`, `--debug`/`--no-debug`, `--quiet`/`--no-quiet`, `--human`/`-h`, `--verbose`, `--connect-timeout`, `--read-timeout`, `--retry`, `--deadline`, `--limit`)
  - **Issue**: Registers universal flags on the top-level parser only. There is no shared parent parser to pass via `parents=` to the per-service subparsers.

- **File**: `arr_cli/facade/cli_common.py`
  - **Function/Method**: `main_wrapper(service, handler, *, parser, argv)`
  - **Lines**: 365–500; specifically 435–441 (`try / except SystemExit` around `parser.parse_args(...)`)
  - **Issue**: Returns `int(exc.code)` from the `except SystemExit` branch, surfacing argparse's exit `2` verbatim. This bypasses the `ArrError` exit-code map (REQ-4).

- **File**: `arr_cli/jellyfin.py`
  - **Function/Method**: `build_jellyfin_parser()`
  - **Lines**: function defined at 399; `subparsers = parser.add_subparsers(...)` at 417; eight `subparsers.add_parser(...)` calls at 424, 428, 432, 437, 458, 463, 474, 484 — all without `parents=`
  - **Issue**: Subparsers don't inherit universal flags.

- **File**: `arr_cli/radarr.py`
  - **Function/Method**: `build_radarr_parser()`
  - **Lines**: function defined at 393; `subparsers = parser.add_subparsers(...)` at 410; six `subparsers.add_parser(...)` calls at 417, 445, 450, 455, 460, 472 — all without `parents=`
  - **Issue**: Same as jellyfin.

- **File**: `arr_cli/sonarr.py`
  - **Function/Method**: `build_sonarr_parser()`
  - **Lines**: function defined at 415; `subparsers = parser.add_subparsers(...)` at 432; six `subparsers.add_parser(...)` calls at 439, 467, 472, 477, 482, 494 — all without `parents=`
  - **Issue**: Same as jellyfin.

- **File**: `arr_cli/maintainerr.py`
  - **Function/Method**: `build_maintainerr_parser()`
  - **Lines**: function defined at 305; `subparsers = parser.add_subparsers(...)` at 324; three `subparsers.add_parser(...)` calls at 331, 339, 344 — all without `parents=`
  - **Issue**: Same as jellyfin.

- **File**: `arr_cli/seerr.py`
  - **Function/Method**: `build_seerr_parser()`
  - **Lines**: function defined at 473; `subparsers = parser.add_subparsers(...)` at 491; six `subparsers.add_parser(...)` calls at 498, 503, 508, 523, 538, 548 — all without `parents=`
  - **Issue**: Same as jellyfin.

- **File**: `arr_cli/facade/errors.py`
  - **Function/Method**: `class ConfigError(ArrError)` / `class AuthError(ArrError)`
  - **Lines**: `ConfigError.exit_code = 1` set at the class body (~line 116); `AuthError.exit_code = 2` set at the class body (~line 125)
  - **Issue**: Not a defect — defines the stable exit-code map that the bug collides with. `ConfigError` is the natural target class for argument-parse failures.

- **File**: `pyproject.toml`
  - **Function/Method**: `[project.scripts]` table
  - **Lines**: 40–45 (console-script entry points: `jellyfin = "arr_cli.jellyfin:main"`, `radarr = "arr_cli.radarr:main"`, `sonarr = "arr_cli.sonarr:main"`, `maintainerr = "arr_cli.maintainerr:main"`, `seerr = "arr_cli.seerr:main"`)
  - **Issue**: Not a defect — confirmed the console-script names for the new subprocess-based regression tests.

### Data Flow Analysis

Today (broken):

```
shell:    jellyfin now --config /tmp/arr.conf
   │
   ▼
argparse top-level parser (build_jellyfin_parser → build_parser)
   │   universal flags registered here; subparser is added via
   │   parser.add_subparsers(...); subparsers.add_parser("now") registers
   │   NO universal flags.
   │
   ▼
argparse dispatches to the "now" subparser
   │   → "now" subparser does not know about --config.
   │
   ▼
subparser.parse_known_args() / parse_args() raises SystemExit(2)
   │   argparse writes "usage:" + "jellyfin: error: unrecognized arguments: --config ..."
   │
   ▼
main_wrapper except-SystemExit branch (cli_common.py:435–441)
   │   return int(exc.code)  → 2
   │
   ▼
console-script entry point: sys.exit(main(...))
   │   process exit code: 2
   ▼
shell reads $? = 2   ← collides with documented AuthError
```

Proposed (after fix):

```
shell:    jellyfin now --config /tmp/arr.conf
   │
   ▼
top-level parser recognises --config and the subcommand "now"
   │   subparser inherits --config via parents=[universal_parent]
   │
   ▼
parse_args() returns Namespace(config="/tmp/arr.conf", command="now", ...)
   │
   ▼
main_wrapper: load_config(args.config) → ServiceConfig
   │   on success: handler(args, cfg) is invoked
   │
   ▼
process exit code: 0 (or documented 1..5 from a facade ArrError)
```

```
shell:    jellyfin now --bogus     (malformed invocation, post-fix path)
   │
   ▼
argparse raises SystemExit(2)
   │
   ▼
main_wrapper except-SystemExit branch (cli_common.py:435–441, post-fix)
   │   catches SystemExit, writes `service=config op=parse message=argparse: ...`
   │   to stderr (using the same _emit_error_line + ConfigError formatting),
   │   returns 1.
   │
   ▼
process exit code: 1   ← matches ConfigError; no collision with AuthError
```

### Dependencies

- `argparse` (stdlib) — the only relevant dependency. `parents=` is a documented `argparse.ArgumentParser.__init__` keyword (passed through `add_subparsers().add_parser(...)`).
- `arr_cli.facade.errors.ConfigError` (defined at `arr_cli/facade/errors.py:108–116`) — provides the structured `service=config op=parse message=…` line via the existing `__str__` implementation. (`ConfigError` accepts `service="config"`, `op="parse"`, `message=...` and emits `service=config op=parse message=...` because `_format_status` returns `None` at the base class.)
- `arr_cli.facade.cli_common._emit_error_line` (already used by `_handle_arr_error` and `_handle_unexpected_error`) — for the single stderr write.

No new runtime dependencies are required.

## Solution Approach

### Fix Strategy

Two coordinated, minimal changes — both confined to `arr_cli/facade/cli_common.py`. No per-service module needs editing.

1. **Share the universal flag set between top-level and subparser via `parents=`.**
   - In `arr_cli/facade/cli_common.py`, factor the universal-flag set out of `build_parser` into a helper that builds an `argparse.ArgumentParser(add_help=False)` containing *only* the universal flags. Concretely:
     - Introduce a module-private `_UNIVERSAL_PARENT` parser (or a builder `_build_universal_parent()`) holding the same `--config`, `--debug`/`--no-debug`, `--quiet`/`--no-quiet`, `--human`/`-h`, `--verbose`, `--connect-timeout`, `--read-timeout`, `--retry`, `--deadline`, `--limit` registrations.
     - Change `build_parser` so it constructs the top-level parser with `parents=[_UNIVERSAL_PARENT]` (in addition to or instead of the in-body `add_argument` calls). This keeps the public function signature unchanged so the per-service modules do not need to be edited and `tests/unit/test_cli_common.py` keeps passing.
   - Update `arr_cli/facade/cli_common.main_wrapper` to build the per-service parser with the universal parent as well: when `parser is None` it constructs `build_parser(...)` (which already includes the parent), so the default path is covered. The five per-service `build_*_parser` functions keep their current shape; their top-level parser already inherits the universal flags from the new `parents=` argument.
   - To surface the universal flags on the **subparser** too, each per-service module must pass `parents=[_UNIVERSAL_PARENT]` to its `subparsers.add_parser(...)` calls. The minimal, surgical way to do this without five hand-edits is a tiny helper — `arr_cli/facade/cli_common.universal_parents()` (or similar) returning the list — that the per-service modules import and use as `parents=arr_cli.facade.cli_common.universal_parents()`. (If the per-service modules are out of scope for this fix, the alternative is one of the alternatives below; see *Risks and Trade-offs*.)
   - The cleanest realisation is: keep the universal-flag `add_argument` calls in `build_parser` for backwards-compat, but expose a `_build_universal_parent()` helper that the per-service `build_*_parser` calls import and pass to *both* the top-level parser and every `subparsers.add_parser(...)`. Both Bug 1 and Bug 3 fall out of this — `--config` is accepted after the subcommand, and `<sub> --help` lists every universal flag.

2. **Translate argparse `SystemExit` into `ConfigError` (exit `1`) inside `main_wrapper`.**
   - In `arr_cli/facade/cli_common.main_wrapper`, replace the current `try / except SystemExit as exc: return int(exc.code)` block with:
     ```python
     except SystemExit as exc:
         code = exc.code if isinstance(exc.code, int) else 2
         # Argparse wrote the raw usage hint to stderr already; follow up
         # with the documented structured line so downstream consumers
         # see the same shape they get from every other error.
         return _handle_arr_error(
             ConfigError("config", "parse", f"argument parse error (argparse exit {code})"),
             debug=getattr(args, "debug", False) if 'args' in locals() else False,
         )
     ```
     (Exact wording of the message can match the surrounding style; the *shape* `service=config op=parse message=…` is what the acceptance criterion calls for.)
   - This brings argparse errors inside the documented exit-code map: `service=config op=parse message=…` on stderr, exit `1`. Codes `2/3/4/5` for non-parse failures are untouched.

### Alternative Solutions

- **Manual flag duplication.** Add every universal flag to every `subparsers.add_parser(...)` call by hand across the five per-service modules. This is more error-prone than `parents=` (any future universal flag requires editing every module), produces longer help listings that argparse still renders correctly but is harder to maintain, and still leaves Bug 2 unaddressed. Rejected.
- **`set_defaults(...)` propagation hack.** Move the universal flags' defaults into the top-level parser only and rely on argparse's namespace inheritance. This is non-standard (argparse does not actually do namespace inheritance across subparsers) and would not fix Bug 3. Rejected.
- **Leave Bug 2 unfixed.** After fix 1 above, argparse's `2` still surfaces on `--help`-after-malformed invocation or on truly malformed input. The caller's acceptance criterion requires exit `1` for *all* argument-parse errors, so this is not acceptable. Rejected.

### Risks and Trade-offs

- **Help-output verbosity.** With the universal flags now inherited by every subparser, `jellyfin now --help` will list all ten universal flags in addition to the subcommand's own options. This matches `README.md §6` ("Run any subcommand with `--help` for the per-service synopsis and flags") and the caller's acceptance criterion, but it does grow the help output per subcommand. Acceptable trade-off — the documented contract is that subcommand help lists the universal flags.
- **`--help` short alias.** `build_parser` re-registers `--help` as a long-only flag so that `-h` can alias `--human`. Because `_UNIVERSAL_PARENT` is constructed with `add_help=False` and only carries the long-only `--help`, this contract is preserved when the parent is shared. Care is needed during the refactor to ensure `--help` is registered exactly once on the **top-level** parser (not on every subparser, otherwise argparse will complain about duplicate `--help` actions).
- **Backwards compatibility.** `build_parser`'s public signature and behavior for the existing top-level use cases is unchanged — every universal flag still parses to the same default value at the top level. Existing tests in `tests/unit/test_cli_common.py` (956 lines, 100+ tests against `build_parser` + `main_wrapper`) should keep passing without modification.
- **`parents=` is the documented argparse idiom** for sharing flags; this is not a hack.

## Implementation Plan

### Changes Required

1. **Change 1**: Extract the universal-flag set into a shared parent parser.
   - File: `arr_cli/facade/cli_common.py`
   - Modification: Add a module-private helper (e.g. `_build_universal_parent()`) returning an `argparse.ArgumentParser(add_help=False)` populated with the same ten universal flags that `build_parser` currently registers. Reuse `build_parser`'s existing `add_argument` calls (move them into the helper, then have `build_parser` consume `parents=[_build_universal_parent()]` so the public function's surface stays identical). Make the helper exported (e.g. add `universal_parents` to `__all__`) so the per-service modules can pass it to their subparsers.

2. **Change 2**: Pass the universal parent to every subparser.
   - Files: `arr_cli/jellyfin.py` (`build_jellyfin_parser`, lines 399–490), `arr_cli/radarr.py` (`build_radarr_parser`, lines 393–497), `arr_cli/sonarr.py` (`build_sonarr_parser`, lines 415–519), `arr_cli/maintainerr.py` (`build_maintainerr_parser`, lines 305–356), `arr_cli/seerr.py` (`build_seerr_parser`, lines 473–563).
   - Modification: Import `universal_parents` from `arr_cli.facade.cli_common` and add `parents=universal_parents()` to **every** `subparsers.add_parser(...)` call in those five modules. (If a subparser needs its own command-local flags, those continue to be registered via the returned `Namespace` variable as today; `parents=` does not replace those.)

3. **Change 3**: Translate argparse `SystemExit` into `ConfigError` (exit `1`).
   - File: `arr_cli/facade/cli_common.py`
   - Modification: In `main_wrapper` (the `try / except SystemExit` block at lines 435–441), replace the `except SystemExit` branch so that the parsed-namespace failure surfaces as exit `1` with a structured `service=config op=parse message=…` stderr line. Do NOT change the exit codes returned from any other branch. The new branch should:
     - Build a `ConfigError("config", "parse", "<description>")` (the `__str__` already emits `service=config op=parse message=…`).
     - Write that structured line to stderr via the existing `_emit_error_line` helper (or `_handle_arr_error`, which already handles the `ArrError` formatting).
     - Return `1`.
     - Preserve `--debug` semantics if the parser actually produced an `args` namespace (rare; argparse raises before `args` is bound). The simplest defensive shape is to default `debug=False` here.

4. **Change 4 (no-op if 1–3 above are clean)**: No changes to `arr_cli/facade/errors.py` — the existing `ConfigError` already produces the documented `service=... op=... message=...` line shape. No changes to `pyproject.toml` (console scripts unchanged). No changes to `arr.conf.example`, `scripts/smoke.sh`, or any spec doc.

### Testing Strategy

New tests under `tests/unit/`. The caller constraint is **subprocess-invocation of the console scripts, not in-process `main()`**. The existing pattern in `tests/unit/test_perf_budgets.py` (`subprocess.run([sys.executable, "-c", f"from {module_name} import main; main(...)"], capture_output=True, text=True, ...)`) is the model to follow.

Recommended new tests (location: either append to `tests/unit/test_cli_common.py` or add a focused `tests/unit/test_universal_flags.py`):

1. **`test_universal_flag_accepted_after_subcommand`** — for each of the five CLIs (`jellyfin`, `radarr`, `sonarr`, `maintainerr`, `seerr`), subprocess-invoke `main([<sub>, "--config", "/tmp/does-not-exist.toml"])` and assert:
   - Exit code is `1` (ConfigError because the path doesn't exist; argparse error from `--config` rejection is now impossible).
   - Stderr contains `service=config op=load message=config file not found` (the structured `ConfigError` line from `load_config`).
   - Stdout is empty (pipe-cleanliness, per AGENTS.md §6).

2. **`test_universal_flag_accepted_before_subcommand`** — same five CLIs, subprocess-invoke `main(["--config", "/tmp/does-not-exist.toml", <sub>])` and assert the same exit code `1` + same stderr shape. This protects the order-sensitivity going forward — both forms must keep working.

3. **`test_subcommand_help_lists_universal_flags`** — for each of the five CLIs, subprocess-invoke `main([<sub>, "--help"])` and assert the stdout contains every one of: `--config`, `--debug`, `--quiet`, `--human`, `--verbose`, `--connect-timeout`, `--read-timeout`, `--retry`, `--deadline`, `--limit`. Use a parameterized loop or one test per CLI to keep failures localized.

4. **`test_top_level_help_still_lists_universal_flags`** — regression guard for the existing top-level `--help`. Subprocess-invoke `main(["--help"])` for each CLI and assert the same ten flag names are still listed (catches a refactor that accidentally drops one).

5. **`test_argument_parse_error_exit_one_with_structured_stderr`** — for each of the five CLIs, subprocess-invoke `main([<sub>, "--bogus-flag"])` and assert:
   - Exit code is `1`.
   - Stderr starts with `service=config op=parse message=` (the structured `ConfigError` line).
   - This is the Bug 2 regression test: pre-fix the exit code was `2` and the stderr was argparse's raw usage line.

6. **`test_unknown_subcommand_exit_one_with_structured_stderr`** — subprocess-invoke `main(["not-a-real-subcommand"])` for each CLI; assert exit `1` and `service=config op=parse message=` on stderr. (Pre-fix this returned argparse's `2`.)

7. **`test_missing_required_positional_exit_one_with_structured_stderr`** — for subcommands with required positionals (e.g. `jellyfin item <id>`, `radarr lookup <term>`, `sonarr lookup <term>`, `seerr search <query>`, `seerr available <query>`, `seerr media <tmdbId>`), subprocess-invoke `main([<sub>])` (no positional) and assert exit `1` and the structured `service=config op=parse message=` line on stderr. This covers the third class of argparse error (missing required argument).

All seven tests follow the existing `_subprocess_env()` / `_project_root()` helpers from `tests/unit/test_perf_budgets.py` so they stay hermetic (no network, no live instance) and use the in-process venv interpreter (`sys.executable` of the test process). No live HTTP is required; the `load_config` call lands in a clean exit `1` because the test config path does not exist.

Out of scope reminder: the stable exit codes `2` (`AuthError`), `3` (`NetworkError`), `4` (`HttpError`), `5` (`ParseError`) remain unchanged. Only the argparse-error path is re-routed to exit `1` (`ConfigError`). Existing tests in `tests/unit/test_cli_common.py` that assert `main_wrapper`'s `ArrError` exit-code behavior (`_handle_arr_error` returns `exc.exit_code`) continue to pass without modification because `_handle_arr_error` is not touched.