# Implementation Plan

## Task Overview

Cosmetic-only, comment-and-string scrubbing pass that removes every
working-tree reference to the personal/agent identifiers (`Renald`,
`renald`, `Lily`, `lily`) from the `arr-cli` repository so the tree can
be made public. The work is a hand-curated set of six file edits plus
a final repository-wide verification sweep, all on the
`feature/user-agent-agnostic` branch. No runtime behavior, no API
surface, no dependencies, no exit-code contracts, and no
placeholder-only guarantee on `arr.conf.example` changes. Honesty
note: this is the initial creation pass (turn 0). The
`context7__*` and `code-context__*` tools are not available in the
agent tool list for this turn; the feature is pure text replacement
against an enumerated, pre-located set of substrings, so
version-sensitive doc grounding is not required (acknowledged in
`requirements.md §Introduction` and `design.md §Appendix C`).

**Conventions inherited from `AGENTS.md`:**

- Python >= 3.11, two-space indent, no new runtime deps.
- `make ci`  ==  `make test && make secret-scan && make smoke-dry`
  must pass on the post-change tree.
- `make lint` (py_compile sweep over `arr_cli/` and `tests/`) must
  pass on the post-change tree.
- The five stable exit codes (`ConfigError` / `AuthError` /
  `NetworkError` / `HttpError` / `ParseError` -> `1` / `2` / `3` /
  `4` / `5`) and `arr.conf.example` placeholder-only guarantee are
  not modified.
- Branch: `feature/user-agent-agnostic`. Single commit, no history
  rewrite, no push (commit was explicitly in-scope per the operator
  override of the AGENTS.md "Do not commit / push nothing" house
  rule).

## Tasks

- [ ] 1. Rewrite `README.md` lede to drop personal/agent identifiers (Requirement 1)
  - [ ] 1.1 Replace `Renald's` with `a` in the line-3 lede sentence
    - Edit `/projects/media-cli/README.md` line 3.
    - Before: `Read-only Python CLI wrappers around Renald's self-hosted media server stack —`
    - After:  `Read-only Python CLI wrappers around a self-hosted media server stack —`
    - Single-word substitution; no surrounding context change.
    - _Requirements: REQ-1 AC1, REQ-1 AC4_
  - [ ] 1.2 Replace the 5-line "two operators" paragraph on lines 10–14 with a generic intended-uses list
    - Edit `/projects/media-cli/README.md` lines 10–14 (the paragraph that names Lily and Renald, the `"his rewatch patterns"` clause, and the `(the operator)` parenthetical).
    - Before (5 lines):
      > The CLIs target two operators. **Lily** (Renald's chat companion Bott) consumes
      > JSON on stdout from a shell pipeline to power conversational queries about
      > what Renald is watching, his rewatch patterns, newly added content, and what
      > Maintainerr is about to delete. **Renald** (the operator) uses `--human` /
      > `-h` for ad-hoc terminal inspection.
    - After (4 lines, generic intended-uses list, no personal/agent names, no `his`/`her` pronouns):
      > The CLIs are intended for two uses:
      > - shell pipelines that consume the verbatim JSON on stdout,
      > - ad-hoc terminal inspection with `--human` / `-h` to render the
      >   response as a readable table.
    - Preserve the prose above (line 8) and below (line 15) verbatim; no heading, table, or section number is renumbered.
    - _Requirements: REQ-1 AC2, REQ-1 AC3, REQ-1 AC4_

- [ ] 2. Replace `Renald's` mention in `AGENTS.md` §1 with generic wording (Requirement 2)
  - [ ] 2.1 Replace `Renald's` with `a` on line 12 of `AGENTS.md`
    - Edit `/projects/media-cli/AGENTS.md` line 12.
    - Before: `total) that wraps Renald's self-hosted media server stack:`
    - After:  `total) that wraps a self-hosted media server stack:`
    - Single-word substitution; preserves surrounding sentence byte-identically.
    - Verify the §1 introduction sentence above the table (lines 10–11) and §2–§7 are unchanged (only this one sentence in §1 differs; REQ-2 AC3).
    - _Requirements: REQ-2 AC1, REQ-2 AC2, REQ-2 AC3, REQ-2 AC4_

- [ ] 3. Update `pyproject.toml` `[project].authors` to a generic placeholder identity (Requirement 3)
  - [ ] 3.1 Replace the `authors` entry with `Cedar` / `cedar@example.invalid`
    - Edit `/projects/media-cli/pyproject.toml` line 13.
    - Before:
      ```toml
      authors = [
          { name = "Renald" },
      ]
      ```
    - After:
      ```toml
      authors = [
          { name = "Cedar", email = "cedar@example.invalid" },
      ]
      ```
    - The `.invalid` TLD is RFC 6761 reserved and guaranteed non-deliverable (REQ-3 AC3).
    - List length stays exactly 1; `name`, `version`, `dependencies`, `classifiers`, `[project.scripts]`, `[project.urls]`, and every `[tool.*]` section are byte-identical (REQ-3 AC4).
    - _Requirements: REQ-3 AC1, REQ-3 AC2, REQ-3 AC3, REQ-3 AC4_

- [ ] 4. Delete the `Lily / Renald` parenthetical comment in `arr_cli/sonarr.py` (Requirement 4a)
  - [ ] 4.1 Delete the audience-naming parenthetical in the `cmd_calendar` comment block
    - Edit `/projects/media-cli/arr_cli/sonarr.py` line 204.
    - Before (line 204):
      >     # most likely to be useful to Lily / Renald.
    - After: *(line deleted in full; the three-line comment block above it (lines 201–203) describing the calendar payload shape is preserved verbatim, and the `columns = [...]` list plus `return _emit(...)` below are preserved verbatim)*
    - Comment-only edit; no executable code, imports, signatures, or HTTP wiring change.
    - _Requirements: REQ-4 AC1, REQ-4 AC3, REQ-4 AC4_

- [ ] 5. Delete the `Lily / Renald` parenthetical in `arr_cli/maintainerr.py` `cmd_pending` docstring (Requirement 4b)
  - [ ] 5.1 Remove the `(collection title, media count, deletion date).` parenthetical from the `cmd_pending` docstring
    - Edit `/projects/media-cli/arr_cli/maintainerr.py` lines 192–193.
    - Before (the two-line body of the docstring that names the renderer's audience):
      >     renderer picks the columns most likely to be useful to Lily /
      >     Renald (collection title, media count, deletion date).
    - After (rewritten sentence, no audience name, no parenthetical):
      >     renderer picks collection title, media count, and deletion date.
    - Preserve the docstring opener (`"""Maintainerr ...` -- REQ-9 AC2` if present on the line above), the rest of the docstring body, the `def cmd_pending` signature, the `_get(...)` call, the `columns = [...]` list, and the `return` statement byte-identically.
    - Quote-balance check: confirm the trailing `"""` is still on the same line it was before; do not introduce a syntax error.
    - _Requirements: REQ-4 AC2, REQ-4 AC3, REQ-4 AC4_

- [ ] 6. Replace the three `"UserName": "renald"` fixtures in `tests/unit/test_jellyfin.py` with a single generic value (Requirement 5)
  - [ ] 6.1 Swap `"renald"` to `"operator"` on line 294
    - Edit `/projects/media-cli/tests/unit/test_jellyfin.py` line 294.
    - Before: `payload = [{"DeviceName": "Living Room TV", "UserName": "renald"}]`
    - After:  `payload = [{"DeviceName": "Living Room TV", "UserName": "operator"}]`
    - String-length change is identical (6 chars -> 8 chars), so PyYAML/JSON shape and any downstream assertions that inspect payload shape are preserved.
    - _Requirements: REQ-5 AC1, REQ-5 AC2, REQ-5 AC4_
  - [ ] 6.2 Swap `"renald"` to `"operator"` on line 657
    - Edit `/projects/media-cli/tests/unit/test_jellyfin.py` line 657.
    - Before: `payload = [{"DeviceName": "TV", "UserName": "renald"}]`
    - After:  `payload = [{"DeviceName": "TV", "UserName": "operator"}]`
    - Same generic value as 6.1 (single consistent value across all three occurrences; REQ-5 AC3).
    - _Requirements: REQ-5 AC1, REQ-5 AC2, REQ-5 AC3, REQ-5 AC4_
  - [ ] 6.3 Swap `"renald"` to `"operator"` on line 804
    - Edit `/projects/media-cli/tests/unit/test_jellyfin.py` line 804.
    - Before: `payload = [{"DeviceName": "TV", "UserName": "renald"}]`
    - After:  `payload = [{"DeviceName": "TV", "UserName": "operator"}]`
    - Same generic value as 6.1 and 6.2.
    - Verify post-edit that `grep -n 'renald' /projects/media-cli/tests/unit/test_jellyfin.py` returns zero matches.
    - _Requirements: REQ-5 AC1, REQ-5 AC2, REQ-5 AC3, REQ-5 AC4_

- [ ] 7. Run the repository-wide sweep to verify zero `lily|Lily|renald|Renald` matches remain (Requirement 6)
  - [ ] 7.1 Run the case-sensitive sweep scoped to the file-extension list
    - From `/projects/media-cli/`, run:
      ```sh
      grep -rn -E 'lily|Lily|renald|Renald' \
        --include='*.py' --include='*.md' --include='*.toml' \
        --include='*.yml' --include='*.yaml' --include='*.conf' \
        --include='*.example' --include='*.sh' \
        --include='Makefile' --include='*.txt' \
        --exclude-dir='.venv' --exclude-dir='.git' \
        --exclude-dir='arr_cli.egg-info' \
        .
      ```
    - **Decision point (flag for operator):** the spec files at `.specs/user-agent-agnostic/requirements.md` and `.specs/user-agent-agnostic/design.md` *contain* the identifier names because they describe the scrub. `design.md §Component 7` explicitly notes these are meta-references and out of scope for the scrub, and the design's Appendix B baseline used `--exclude-dir='.specs'`. The requirements spec (REQ-6 AC1) does not list `.specs/` as an excluded dir.
    - **Implementer action:** if the sweep returns matches only in `.specs/user-agent-agnostic/requirements.md` or `.specs/user-agent-agnostic/design.md`, re-run the sweep with `--exclude-dir='.specs'` added and confirm zero matches in the in-scope file set. Do NOT edit the spec files in this ticket (REQ-6 AC1 meta-note; out-of-scope follow-up).
    - If the sweep returns any match in `tests/unit/`, `arr_cli/`, `README.md`, `AGENTS.md`, `pyproject.toml`, `arr.conf.example`, `scripts/`, `Makefile`, or any other in-scope file path, **block the ticket** and surface the offending `path:line:matched text` to the operator (REQ-6 AC3).
    - _Requirements: REQ-6 AC1, REQ-6 AC3_
  - [ ] 7.2 Run the case-insensitive belt-and-braces sweep
    - From `/projects/media-cli/`, run (same scope, case-insensitive):
      ```sh
      grep -irn -E 'lily|renald' \
        --include='*.py' --include='*.md' --include='*.toml' \
        --include='*.yml' --include='*.yaml' --include='*.conf' \
        --include='*.example' --include='*.sh' \
        --include='Makefile' --include='*.txt' \
        --exclude-dir='.venv' --exclude-dir='.git' \
        --exclude-dir='arr_cli.egg-info' \
        --exclude-dir='.specs' \
        .
      ```
    - Must also return zero matches (REQ-6 AC2).
    - _Requirements: REQ-6 AC2, REQ-6 AC3_
  - [ ] 7.3 Append the sweep report to the change summary
    - In the commit message (or the PR description), state:
      - File extensions scanned: `*.py`, `*.md`, `*.toml`, `*.yml`, `*.yaml`, `*.conf`, `*.example`, `*.sh`, `Makefile`, `*.txt`.
      - Directories excluded: `.venv/`, `.git/`, `arr_cli.egg-info/`, `.specs/` (meta-reference exception).
      - Result: zero matches in the in-scope file set.
    - _Requirements: REQ-6 AC4_

- [ ] 8. Run the verification gates (`make ci` and `make lint`) to prove no regression (Requirement 7)
  - [ ] 8.1 Run `make ci` from the project root
    - From `/projects/media-cli/`, run `make ci`. This is `make test && make secret-scan && make smoke-dry`.
    - All three sub-targets must exit 0. If any fails, block the ticket and identify whether the failure is a syntax error (lint / smoke-dry) or an assertion that became sensitive to the `"UserName"` value change (extremely unlikely; assertions inspect payload shape, not the literal).
    - _Requirements: REQ-7 AC1, REQ-7 AC5_
  - [ ] 8.2 Run `make lint` from the project root
    - From `/projects/media-cli/`, run `make lint` (py_compile sweep over `arr_cli/` and `tests/`).
    - Must exit 0; catches accidental syntax breakage introduced by the `sonarr.py` comment deletion and the `maintainerr.py` docstring edit.
    - _Requirements: REQ-7 AC2_
  - [ ] 8.3 Verify the five exit codes and their class names are byte-identical
    - Run `git diff main -- arr_cli/facade/errors.py` (or equivalent inspection of the pre-change `errors.py`).
    - Must be empty: `ConfigError` / `AuthError` / `NetworkError` / `HttpError` / `ParseError` -> `1` / `2` / `3` / `4` / `5` preserved byte-for-byte.
    - Also confirm `arr.conf.example` is byte-identical: `git diff main -- arr.conf.example` must be empty.
    - _Requirements: REQ-7 AC3, REQ-7 AC5_

- [ ] 9. Commit the change on `feature/user-agent-agnostic` (Requirement 8)
  - [ ] 9.1 Create or check out the feature branch
    - From `/projects/media-cli/`, run `git checkout -b feature/user-agent-agnostic` if the branch does not exist; otherwise `git checkout feature/user-agent-agnostic`.
    - Branch name must be exactly `feature/user-agent-agnostic`.
    - _Requirements: REQ-8 AC1_
  - [ ] 9.2 Stage the six edited files and commit
    - Run:
      ```sh
      git add README.md AGENTS.md pyproject.toml \
              arr_cli/sonarr.py arr_cli/maintainerr.py \
              tests/unit/test_jellyfin.py
      git commit -m "scrub personal/agent identifiers from working tree for public release"
      ```
    - Single commit; no `git push` (REQ-8 AC1).
    - _Requirements: REQ-8 AC1, REQ-8 AC4_
  - [ ] 9.3 Verify `main` is untouched and no history-rewriting tool was used
    - Run `git log main --oneline -5` and confirm it matches the pre-change state (no `filter-branch`, `rebase`, `commit --amend` of pre-existing commits, `git filter-repo`, BFG, or any other history-rewriting tool).
    - Run `git diff main --stat` and confirm only the six files in task 9.2 plus this spec's `requirements.md` (audit trail) appear; no incidental edits, reformatting, or whitespace churn.
    - _Requirements: REQ-8 AC2, REQ-8 AC3, REQ-8 AC4_
