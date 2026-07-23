# Requirements Document

## Introduction

The `arr-cli` repository currently contains personal and agent identifiers
(`Renald`, `renald`, `Lily`, `lily`) in its documentation, packaging
metadata, source code comments, and test fixtures. Before the repository
can be made public, every such reference must be removed from the working
tree so that no trace of the original author or their chat-bott companion
remains in the public-facing artifacts. The change must be cosmetic-only:
no functional behavior, no exit-code contracts, no runtime dependencies,
no secret-handling guarantees, and no placeholder-only contract may be
weakened. Git history rewriting is explicitly out of scope; the fix lives
entirely in the working tree of branch `feature/user-agent-agnostic`.

This document captures the user stories, acceptance criteria, and
non-functional constraints for that scrubbing pass.

> **Note on context7 / code-context grounding.** The `context7__*` and
> `code-context__*` tools are not present in the agent tool list for this
> turn, so doc- and code-grounding lookups were not performed. The
> feature is pure text replacement against an enumerated, pre-located
> set of substrings; no library or framework API is being changed, so
> version-sensitive doc grounding is not required for correctness.

## Requirements

### Requirement 1

**User Story:** As a maintainer preparing `arr-cli` for a public release,
I want every reference to `Renald` / `renald` removed from `README.md`,
so that the project's public home page does not name the original
author.

#### Requirement 1 Acceptance Criteria

1. WHEN `README.md` is searched for the substring `Renald` (case-sensitive)
   THEN the working-tree copy SHALL contain zero matches outside of the
   existing prose the user has not flagged (specifically the `wraps Renald's`
   mention in the lede and the `Lily (Renald's chat companion Bott)`
   paragraph).
2. WHEN the §1 paragraph that names `Lily` and `Renald` as `two operators`
   is replaced THEN the replacement SHALL be a generic **intended uses**
   list describing who the CLIs are for (shell pipelines and ad-hoc
   terminal inspection), without any personal name, agent name, or
   reference to a chat-bott companion.
3. IF the new paragraph mentions any person, agent, or bott THEN the
   acceptance check SHALL fail; the replacement must be generic.
4. WHEN the file is read end-to-end THEN the prose SHALL still be
   self-consistent (no orphaned pronoun references such as `his rewatch
   patterns` left dangling after the operator names are removed); vague
   generic phrasing (e.g. `the operator's`, `the user's`) is acceptable.

### Requirement 2

**User Story:** As a maintainer preparing `arr-cli` for a public release,
I want the personal-author phrase in `AGENTS.md` §1 replaced with generic
wording, so that the house-rules document no longer names the original
operator, while every other house rule is preserved verbatim.

#### Requirement 2 Acceptance Criteria

1. WHEN `AGENTS.md` is searched for the substring `Renald`
   THEN the working-tree copy SHALL contain zero matches.
2. WHEN the original sentence `wraps Renald's self-hosted media server
   stack` is replaced THEN the replacement SHALL describe the project
   generically (e.g. `wraps a self-hosted media server stack`) and SHALL
   NOT introduce a replacement personal name.
3. WHEN the rest of `AGENTS.md` is diffed against the pre-change copy
   THEN only the one sentence in §1 SHALL differ; §2–§7 and the §1
   introduction sentence above the table SHALL be byte-identical.
4. IF any other section heading, list item, table cell, or rule body
   changes THEN the acceptance check SHALL fail.

### Requirement 3

**User Story:** As a maintainer preparing `arr-cli` for a public release,
I want the `authors` field in `pyproject.toml` updated to a generic
identity with a placeholder email, so that packaging metadata does not
name the original author.

#### Requirement 3 Acceptance Criteria

1. WHEN `pyproject.toml` is searched for the substring `Renald`
   THEN the working-tree copy SHALL contain zero matches.
2. WHEN the `authors` list is inspected THEN it SHALL contain exactly
   one entry: `{ name = "Cedar", email = "cedar@example.invalid" }`.
3. IF the email is a real-looking address (anything other than the
   `.invalid` TLD reserved for non-deliverable placeholders) THEN the
   acceptance check SHALL fail.
4. WHEN the rest of `pyproject.toml` is diffed against the pre-change
   copy THEN only the `authors` entry SHALL differ; `name`, `version`,
   `dependencies`, classifiers, scripts, and `[tool.*]` sections SHALL
   be byte-identical.

### Requirement 4

**User Story:** As a maintainer preparing `arr-cli` for a public release,
I want the parenthetical comments in `arr_cli/sonarr.py:204` and
`arr_cli/maintainerr.py:192-193` that name `Lily` / `Renald` as the
renderer's audience removed, so that source code does not leak personal
identifiers.

#### Requirement 4 Acceptance Criteria

1. WHEN `arr_cli/sonarr.py` line 204 is inspected THEN the substring
   `Lily / Renald` SHALL NOT appear at that line, and the rest of the
   comment that names the renderer's audience (`most likely to be useful
   to`) SHALL be removed with it.
2. WHEN `arr_cli/maintainerr.py` lines 192-193 are inspected THEN the
   substring `Lily / Renald` SHALL NOT appear, and the surrounding
   parenthetical naming `Lily` or `Renald` as the renderer's audience
   SHALL be removed.
3. IF either file's line count changes by more than the number of
   comment characters removed (i.e., executable code is deleted)
   THEN the acceptance check SHALL fail; the edits are comment-only.
4. WHEN the surrounding module/function code is diffed against the
   pre-change copy THEN only the targeted parenthetical comment text
   SHALL differ; all imports, function signatures, return statements,
   and HTTP-call wiring SHALL be byte-identical.

### Requirement 5

**User Story:** As a maintainer preparing `arr-cli` for a public release,
I want the three `"UserName": "renald"` test fixtures in
`tests/unit/test_jellyfin.py` replaced with a single generic value, so
that no personal username leaks into the test suite.

#### Requirement 5 Acceptance Criteria

1. WHEN `tests/unit/test_jellyfin.py` is searched for the substring
   `renald` (case-sensitive, in either identifier or string-literal form)
   THEN the working-tree copy SHALL contain zero matches.
2. WHEN the three fixtures at lines 294, 657, and 804 are inspected
   THEN each SHALL contain `"UserName": "operator"` (single, consistent
   value across all three occurrences).
3. IF any one occurrence uses a different generic string (e.g. one is
   `"user"`, another `"admin"`) THEN the acceptance check SHALL fail;
   all three must use the same generic value.
4. WHEN the surrounding test bodies, assertions, and helper imports are
   diffed against the pre-change copy THEN only the literal `"renald"`
   string SHALL change; test names, assertions, mock wiring, and
   expected exit codes SHALL be byte-identical.

### Requirement 6

**User Story:** As a maintainer preparing `arr-cli` for a public release,
I want a final repository-wide sweep across the working tree to confirm
no remaining `lily` / `Lily` / `renald` / `Renald` references exist, so
that the scrubbing pass is verifiable and complete.

#### Requirement 6 Acceptance Criteria

1. WHEN a recursive grep (case-sensitive) for the regex
   `lily|Lily|renald|Renald` is run against the working tree, scoped to
   file extensions `*.py`, `*.md`, `*.toml`, `*.yml`, `*.yaml`,
   `*.conf`, `*.example`, `*.sh`, `Makefile`, and `*.txt`, and excluding
   `.venv/`, `arr_cli.egg-info/`, and `.git/` THEN the command SHALL
   return zero matches.
2. WHEN the sweep is re-run with case-insensitive matching as a
   belt-and-braces check THEN it SHALL ALSO return zero matches.
3. IF any match is found THEN the acceptance check SHALL fail and the
   operator SHALL be shown the offending file path and line number
   before considering the ticket complete.
4. WHEN the sweep completes cleanly THEN a short report SHALL be
   appended to the change summary stating the file extensions scanned,
   the directories excluded, and the fact that no matches were found.

### Requirement 7

**User Story:** As the CI pipeline and downstream consumers of `arr-cli`,
I want all existing build, test, lint, secret-scan, and smoke-dry
guarantees preserved across this scrubbing pass, so that making the repo
public does not regress any current contract.

#### Requirement 7 Acceptance Criteria

1. WHEN `make ci` is run against the working tree THEN it SHALL pass
   (i.e. `make test` + `make secret-scan` + `make smoke-dry` all succeed).
2. WHEN `make lint` is run THEN it SHALL pass (every `.py` file under
   `arr_cli/` and `tests/` must still `py_compile`).
3. WHEN the five stable exit codes are inspected in
   `arr_cli/facade/errors.py` (or equivalent) THEN their numeric values
   and class names (`ConfigError`, `AuthError`, `NetworkError`,
   `HttpError`, `ParseError` → `1`, `2`, `3`, `4`, `5`) SHALL be
   byte-identical to the pre-change copy.
4. WHEN `pyproject.toml` `[project].dependencies` is inspected THEN it
   SHALL still contain exactly `requests>=2.28` and `PyYAML>=6.0`; no
   runtime dep may be added (this is a comment/text-only change).
5. WHEN `arr.conf.example` is inspected THEN every literal placeholder
   (`YOUR_API_KEY_HERE`, `<user-id>`, `https://example.com`) SHALL be
   preserved verbatim, and `scripts/secret-scan` SHALL still pass —
   the placeholder-only guarantee of the example file is not weakened.

### Requirement 8

**User Story:** As the operator of this repository, I want the change
committed to a single branch (`feature/user-agent-agnostic`) in the
working tree only, with no git-history rewrites, so that the audit
trail is intact and the change is easy to review as a regular PR.

#### Requirement 8 Acceptance Criteria

1. WHEN the change is ready THEN it SHALL be committed on a branch
   named exactly `feature/user-agent-agnostic`; the user has explicitly
   overridden the AGENTS.md house rule "Do not commit / push nothing"
   for this ticket, so a commit (and only a commit — no `git push` is
   in scope unless explicitly asked) is permitted.
2. WHEN the commit log of `main` is inspected THEN it SHALL be
   unchanged (no `filter-branch`, `rebase`, `commit --amend` of
   pre-existing commits, `git filter-repo`, BFG, or any other
   history-rewriting tool was used).
3. IF any history-rewriting tool was run THEN the acceptance check
   SHALL fail and the operator SHALL be alerted before the PR is
   opened.
4. WHEN the diff of the branch against `main` is inspected THEN it
   SHALL touch only the files enumerated in Requirements 1–6 plus
   this requirements document; no incidental edits, reformatting, or
   whitespace churn outside the targeted substrings.

## Non-Functional Requirements

### Performance

- The scrubbing pass SHALL complete as a single human-scale edit pass
  (no scripts, no bulk regex across the whole tree beyond the final
  verification sweep) and SHALL NOT introduce any new runtime code
  path, so there is zero performance impact on CLI invocation
  latency, startup time, or memory footprint.
- The final verification sweep (Requirement 6) SHALL run in under five
  seconds on a developer laptop, scoped to the same file-extension list
  the user specified.

### Security

- No real or fake credential value SHALL be introduced anywhere in
  this change; the `authors` email uses the reserved `.invalid` TLD
  exactly so it cannot accidentally resolve to a real mailbox.
- `scripts/secret-scan` SHALL continue to pass after the change,
  preserving the placeholder-only guarantee of `arr.conf.example`.
- `--debug` header-redaction behavior SHALL be untouched (no logging
  code path was added).

### Reliability

- `make ci` (test + secret-scan + smoke-dry) and `make lint` SHALL both
  pass after the change; these are the canonical gates enumerated in
  `AGENTS.md §3` and §7.
- The five stable exit codes and their class names SHALL be preserved
  byte-for-byte; downstream consumers scripting against those codes
  SHALL NOT observe any change.

### Usability

- After the change, a public reader landing on `README.md` SHALL be
  able to learn what `arr-cli` is for without encountering any
  personal name or chat-bott companion reference; the new **intended
  uses** list (Requirement 1 AC2) SHALL be the only identity-bearing
  prose in the lede.
- The generic `Cedar` author name and `cedar@example.invalid` email
  SHALL be recognizable as placeholders (not a real identity) so that
  a future maintainer can see at a glance that they are intended to be
  replaced before the repo is published under a real identity.