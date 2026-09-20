# AGENTS.md

Guidance for AI coding agents working in this repository. This file is the
authoritative, machine-readable companion to `README.md`. Read both before
making changes; the rules below are non-negotiable.

---

## 1. Project overview

`arr-cli` is a **read-only** Python CLI suite (5 executables, 30 commands
total) that wraps a self-hosted media server stack:

| Executable    | Service         | Auth header       |
| ------------- | --------------- | ----------------- |
| `jellyfin`    | Jellyfin        | `Authorization` (MediaBrowser envelope) |
| `radarr`      | Radarr          | `X-Api-Key`       |
| `sonarr`      | Sonarr          | `X-Api-Key`       |
| `maintainerr` | Maintainerr     | (none by default) |
| `seerr`       | Seer            | `X-Api-Key`       |

All commands are HTTP `GET`. There are **no write endpoints** in MVP. Every
command must:

- emit a curated per-command summary on stdout by default for the 15
  size-to-summary candidate commands; pass `--verbose` for the verbatim
  service payload,
- emit the verbatim service JSON on stdout for the 13
  safe-to-leave-alone commands (no summary renderer is registered for
  these, so `--verbose` is a no-op on them),
- emit a tabular readable view on `--human` / `-h`,
- emit diagnostics on stderr (so `stdout` is pipe-clean JSON),
- return one of the five stable exit codes documented in `README.md`.

The renderer priority chain (`--human` > `--verbose` > default summary)
is documented in the docstring of `arr_cli.facade.output.emit`; the
dispatch table `_SUMMARY_RENDERERS` is the single registration point for
new size-to-summary candidates.

**Seer note.** The `seerr` executable targets the unified
[Seer](https://github.com/seerr-team/seerr) project (the merged
Overseerr + Jellyseerr fork). When adding or fixing a `seerr` command,
the source of truth for endpoint paths and methods is the live
`/api-docs/swagger-ui-init.js` OpenAPI spec on the operator's instance,
**not** the historical Overseerr or Jellyseerr documentation --
endpoints frequently differ between Seer and its predecessors (the
failing `seerr` commands investigated in 2026-09 were all caused by
exactly this drift). Cross-check the live spec before committing to a
path shape.

**Seer endpoint drift on `request` / `media` records.** Both
`GET /api/v1/request` and `GET /api/v1/media` return records where
the nested `media` envelope (request) or top-level record (media)
carries **identity** fields (`id`, `mediaType`, `tmdbId`, `tvdbId`,
`externalServiceSlug`, `status`, ...) but does **not** carry a
human-readable `title` / `name`. The historical summary renderers
read `media.title` (movie) / `media.name` (TV) or a top-level
`title`, which always projected `null` on the operator's live
Seer instance. The fixed renderers
(`_summary_seerr_requests`, `_summary_seerr_available`) project the
identity fields instead, giving the operator a meaningful row
that can be chained into `seerr movie <id>` / `seerr tv <id>` or
cross-referenced via `tmdbId` / `tvdbId`. Do **not** add a
`title` projection back to these renderers without first
confirming against the live OpenAPI spec that the upstream payload
populates the field -- a regression that projects `null` for every
row was the symptom that surfaced this drift twice (2026-09-18:
`seerr-available-no-title-filter`, `seerr-requests-no-title-field`).

The shared HTTP, config, auth, and output code lives in `arr_cli.facade/`.
Per-service CLIs in `arr_cli/{jellyfin,radarr,sonarr,maintainerr,seerr}.py`
are thin command tables on top of the facade.

---

## 2. Layout

```
arr_cli/              # Python package
  facade/             # shared transport, config, auth, errors, output
  jellyfin.py         # per-service CLI (8 commands)
  radarr.py           # per-service CLI (6 commands)
  sonarr.py           # per-service CLI (6 commands)
  maintainerr.py      # per-service CLI (3 commands, no auth by default)
  seerr.py            # per-service CLI (7 commands)
tests/
  unit/               # default pytest target; always runs
  integration/        # opt-in via `pytest --run-integration`; skipped by default
scripts/
  smoke.sh            # CLI grammar + (gated) live-instance exercise
  secret-scan         # greps for committed secrets; enforces placeholder-only example
arr.conf.example      # placeholder-only sample config (committed)
pyproject.toml        # Python >=3.11; deps: requests, PyYAML; dev: pytest, responses
Makefile              # canonical CI entry point is `make ci`
.specs/arr-cli-mvp/   # requirements, design, tasks, review (read these to learn intent)
CHANGELOG.md          # release history
```

---

## 3. Build, test, lint

Install in editable mode:

```bash
pip install -e ".[dev]"
```

Run before opening a PR (the local CI chain):

```bash
make ci                       # == make test && make secret-scan && make smoke-dry
```

Individual targets (fast local feedback):

```bash
make test                     # pytest tests/unit
make lint                     # py_compile sweep over arr_cli/ and tests/
make secret-scan              # greps for committed secrets + example placeholder check
make smoke                    # scripts/smoke.sh (default = --dry-run; CI safe)
make smoke-dry                # explicit --dry-run alias
make integration-test         # opt-in; needs ARR_LIVE_URL + per-service creds exported
make smoke-live               # opt-in; requires RUN_LIVE=1
```

Tooling is overridable on the command line, e.g. `make test PYTEST=pytest`,
`make ci SH=/bin/bash`.

The Makefile is **POSIX-portable** (no GNU-only constructs). Do not add
GNU-only recipes.

---

## 4. Conventions

### 4.1 Python style

- **Target Python ≥ 3.11.** Use modern syntax freely (PEP 604 unions,
  `match`, structural pattern matching, `tomllib`, etc.) — `tomllib` is in
  the stdlib, no extra dep needed for TOML parsing.
- **Two-space indent**, no tabs. Match the existing files.
- **Type hints everywhere** on public functions. The package ships
  `arr_cli/py.typed` (PEP 561); do not weaken this.
- **No comments unless they explain non-obvious "why".** Do not add
  docstring/comment changes for their own sake.
- Module-level docstrings are fine; function docstrings are not required
  if the signature is self-documenting.
- Prefer stdlib + already-vendored deps (`requests`, `PyYAML`, `pytest`,
  `responses`). Do **not** add new runtime dependencies without discussion.
  `responses` is for tests only.

### 4.2 Per-service CLI shape

- Each `arr_cli/<service>.py` exports a `main()` and a `build_parser()`.
- Each command is a thin function that calls into `arr_cli.facade` and
  prints JSON or a rendered table. **Do not** put new HTTP code directly
  inside the per-service files — extend the facade instead.
- New commands must:
  - be HTTP `GET`,
  - percent-encode user-supplied query/path fragments via the facade,
  - map non-2xx through the facade's typed error classes (see §6),
  - support `--human` rendering via the existing output helpers.

### 4.3 The facade is the only place that:

- talks to `requests`,
- injects auth headers (one header per service; never thread creds through
  command code),
- reads/parses `arr.conf`,
- emits redacted `--debug` traces (header values become `***<length>`),
- decides timeouts / retry / `--deadline` semantics.

If a change touches more than one of the per-service CLIs, the right home
is almost always `arr_cli/facade/`.

---

## 5. Configuration & secrets — the hard rules

> **Nothing gets hardcoded.** Any new service, command, or config key that
> hardcodes a URL, key, or user-id will be rejected at review.

1. **Config path is canonical:** `~/.config/arr/arr.conf`. Per-invocation
   override: `--config <path>`. Env-var overrides follow the `ARR_*`
   convention documented in `arr.conf.example`.
2. **File permissions:** POSIX mode must be `0600` or stricter; the CLI
   refuses to read a group/world-readable file with exit code `1`.
3. **Format:** selected by file extension (`.yaml`/`.yml`/`.toml`) with a
   leading-byte sniff fallback. Unknown format → exit code `1`.
4. **`arr.conf.example` is placeholder-only.** It must keep using literal
   `YOUR_API_KEY_HERE` / `<user-id>` / `https://example.com` strings.
   Real keys belong in the operator's local `arr.conf` only — never in
   this tree. `scripts/secret-scan` enforces this; CI will fail otherwise.
5. **`.gitignore`** must keep ignoring a real `arr.conf`. Do not add
   patterns that would re-commit secrets.
6. **Never log a credential value.** `--debug` redacts header values to
   `***<length>`; preserve that behavior in any new logging code path.

---

## 6. Error model

The facade raises typed errors that map to five stable exit codes.
CLI code must let them propagate (or re-raise the same class) — do not
catch them and `print()` yourself, or stdout pipe-cleanliness breaks.

| Code | Class          | Trigger                                                           |
| ---: | -------------- | ----------------------------------------------------------------- |
|  `1` | `ConfigError`  | Missing/bad-perm/unknown-format config; malformed CLI date input. |
|  `2` | `AuthError`    | HTTP 401/403 from the service; missing required credential.       |
|  `3` | `NetworkError` | DNS, connect refused, TLS, timeout.                               |
|  `4` | `HttpError`    | Other 4xx/5xx (e.g. 404 on `jellyfin item <id>` names the id).    |
|  `5` | `ParseError`   | Response body is not valid JSON.                                  |

Errors always include `service: op=...` (and an `id=` when relevant) on
stderr. Consumers scripting against this CLI depend on those stable
codes and shapes — do not rename the classes or reorganize the table.

---

## 7. Adding a command (checklist)

1. Confirm the operator's live service exposes the endpoint and the path
   matches what you think. For maintainerr, cross-check against
   `/api/swagger`.
2. Put the HTTP call in the facade (or a facade helper), not in the
   per-service CLI. Wire auth + timeouts + error mapping through it.
3. Percent-encode any user-supplied query/path fragments.
4. Add a `--human` renderer that reuses the existing output helpers.
5. Add unit tests under `tests/unit/` using `responses` to mock HTTP.
   (No live HTTP in `tests/unit/` — it must stay hermetic.)
6. If the endpoint needs network access, add an opt-in test under
   `tests/integration/` gated by `skip_unless_run_integration` (and
   `ARR_RUN_INTEGRATION=1` for the unittest runner). Never chain these
   into `make ci`.
7. Update the per-service command table in `README.md §4`.
8. Run `make ci` and `make lint` before considering the change done.

---

## 8. Documentation

This is a public project, when updating any documentation or adding comments, do not reference your personal environment or internal board tickets — no one else can access them.

**Do not** reference your personal environment.
**Do not** reference your internal board tickets.
**Do not** mention anything sensitive.

---
