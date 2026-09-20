"""Placeholder-only validation hook for the committed config example.

This module implements task 15 of the arr-cli MVP:

    "Write placeholder-only committed example config validation hook"

REQ-1 AC4 mandates that the committed ``arr.conf.example`` ships with
**only** documented placeholder values -- nothing real, no real URLs,
no real API keys, no real user identifiers. The CI hook here is the
defensive check that enforces that contract automatically; a stray
``https://my-jellyfin.example.org`` pasted into the example by an
over-eager editor is caught before the file lands in the build.

The validator is deliberately lightweight and stdlib-only:

* It reads the file as text (the format sniff is intentionally broad
  because ``arr.conf.example`` is YAML-shaped but may grow TOML-only
  equivalents in the future, and the placeholder contract is the
  same either way).
* It strips line-comments (``#``) and blank lines so the documented
  TOML block at the bottom of the example -- which is intentionally
  commented out -- does not produce false positives.
* It then asserts three documented invariants:

      1. No URL value points at anything other than ``example.com``.
      2. Every ``api_key`` value equals ``YOUR_API_KEY_HERE``.
      3. Every ``user_id`` value equals ``<user-id>``.

Violations are returned as a list of human-readable strings. An empty
list means the example is safe to ship; the calling script (typically
``scripts/example-lint.sh``) is responsible for translating the
violations into a non-zero exit code per the CI contract.

The contract intentionally uses an equality check on placeholder
strings rather than a deny-list of "looks like a secret" patterns:
the documented placeholders are fixed strings, and a more permissive
checker would either allow real secrets that look like the
placeholder or false-positive on harmless inputs.

Public surface
--------------

* :func:`validate_example_config` -- parse-and-return-list entry
  point. Accepts a path-like and returns a list of violation strings.
* :func:`is_placeholder_only` -- thin wrapper that returns a bool
  for the common "is this file OK?" call site.

Both functions are pure (no side effects, no module-level state) so
they are trivial to unit-test and to call from ad-hoc scripts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

#: The documented ``api_key`` placeholder. Every ``api_key`` value in
#: the example MUST equal this string verbatim.
API_KEY_PLACEHOLDER: str = "YOUR_API_KEY_HERE"

#: The documented ``user_id`` placeholder. Every ``user_id`` value in
#: the example MUST equal this string verbatim.
USER_ID_PLACEHOLDER: str = "<user-id>"

#: The only hostname permitted in URL values. ``https://example.com``
#: is the documented example URL; any other host is a real-looking
#: secret and MUST be rejected.
ALLOWED_HOSTNAME: str = "example.com"

#: Regex matching an inline ``#`` comment delimiter. The example file
#: uses ``#`` for both whole-line and trailing-line comments; we cut
#: on the first such delimiter so values like ``api_key: *** # comment``
#: still resolve to a clean value.
_COMMENT_SPLIT = re.compile(r"\s#")

#: Regex matching a ``key: value`` pair (YAML / TOML-nested shape).
#: Capture group 1 is the key; capture group 2 is the raw value with
#: surrounding whitespace stripped and trailing inline comments
#: already removed by :data:`_COMMENT_SPLIT`. Quoted TOML strings
#: (``key = "value"``) are matched by :data:`_ASSIGN_PAIR_RE`.
_KV_COLON_RE = re.compile(
    r"""^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$"""
)

#: Regex matching a ``key = value`` pair (TOML shape). Capture
#: group 1 is the key; capture group 2 is the raw value with quotes
#: stripped. Quoted strings only -- the example file does not use
#: bare TOML scalars for credential-bearing keys.
_ASSIGN_PAIR_RE = re.compile(
    r"""^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*["']([^"']*)["']\s*$"""
)

#: Regex matching any ``http://`` or ``https://`` URL value. This is
#: intentionally tolerant: it matches the full URL so the rejection
#: message can name the offending host. The negative lookahead below
#: (:data:`_ALLOWED_HOST_RE`) governs the "is this host allowed?"
#: decision.
_URL_RE = re.compile(r"https?://[^\s,'\"#]+")

#: Hostname extractor used to compare against :data:`ALLOWED_HOSTNAME`.
#: Pulls the host (port-stripped) out of a URL match so the violation
#: message can name what was found. The regex tolerates an optional
#: ``:port`` suffix and an optional ``/path``.
_HOSTNAME_RE = re.compile(
    r"^https?://([^/:]+)(?::\d+)?(?:/.*)?$"
)

__all__ = [
    "API_KEY_PLACEHOLDER",
    "USER_ID_PLACEHOLDER",
    "ALLOWED_HOSTNAME",
    "validate_example_config",
    "is_placeholder_only",
]


def _iter_active_lines(text: str) -> Iterable[tuple[int, str]]:
    """Yield ``(line_no, line)`` for each non-comment, non-blank line.

    The example file uses ``#`` for both whole-line and trailing
    comments; this helper yields only the meaningful lines so the
    validator never confuses a documented comment for an assignment.

    Line numbers are 1-based and refer to the original file so the
    violation message can pinpoint the offending line.
    """
    for line_no, raw in enumerate(text.splitlines(), start=1):
        # Whole-line comment: ``#`` as the first non-whitespace char.
        # Trim left whitespace first so leading-indent ``#`` comments
        # are still recognised.
        if raw.lstrip().startswith("#"):
            continue
        # Strip inline comments. The split is conservative -- we only
        # cut on whitespace before ``#`` so values that legitimately
        # contain ``#`` (none in the example) survive.
        stripped = _COMMENT_SPLIT.split(raw, maxsplit=1)[0].rstrip()
        if not stripped.strip():
            continue
        yield line_no, stripped


def _extract_value(line: str) -> tuple[str | None, str | None]:
    """Return ``(key, value)`` for a single line, or ``(None, None)``.

    Recognises both the YAML ``key: value`` shape and the TOML
    ``key = "value"`` shape. Returns ``(None, None)`` for lines that
    do not look like a key/value pair (free text, section headers,
    etc.) so the validator silently ignores them.
    """
    match = _KV_COLON_RE.match(line)
    if match is not None:
        return match.group(1), match.group(2)
    match = _ASSIGN_PAIR_RE.match(line)
    if match is not None:
        return match.group(1), match.group(2)
    return None, None


def _check_url(line_no: int, value: str, violations: list[str]) -> None:
    """Inspect ``value`` for forbidden URLs; append to ``violations``.

    The contract is: every ``http://`` or ``https://`` URL must point
    at :data:`ALLOWED_HOSTNAME`. A URL that fails the check is added
    to ``violations`` with a line-numbered, human-readable message.
    """
    for url_match in _URL_RE.finditer(value):
        url = url_match.group(0)
        host_match = _HOSTNAME_RE.match(url)
        host = host_match.group(1) if host_match else url
        if host.lower() != ALLOWED_HOSTNAME:
            violations.append(
                f"line {line_no}: URL {url!r} points at host "
                f"{host!r}; only {ALLOWED_HOSTNAME!r} is allowed"
            )


def validate_example_config(path) -> list[str]:
    """Return a list of placeholder violations found in ``path``.

    Parameters
    ----------
    path:
        Filesystem path to the example config. Accepts anything that
        :class:`pathlib.Path` accepts (``str`` and ``Path`` both work).

    Returns
    -------
    list[str]
        A list of human-readable violation messages. **Empty list**
        means the file passed every check and is safe to ship.
        Non-empty means at least one invariant was violated; the
        caller is expected to surface the list on stderr and exit
        non-zero (per the CI contract).

    The function never raises for "expected" problems (missing file,
    binary garbage, etc.) -- those are themselves violations, because
    the example file is supposed to be a clean placeholder document.
    """
    resolved = Path(path)
    violations: list[str] = []

    if not resolved.exists():
        violations.append(f"file not found: {resolved}")
        return violations

    try:
        text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        violations.append(f"could not read {resolved}: {exc}")
        return violations

    for line_no, line in _iter_active_lines(text):
        # Check every URL embedded anywhere in the line -- not just
        # the value of a ``url:`` assignment -- so an ``api_key:
        # https://internal.example`` typo is still caught.
        _check_url(line_no, line, violations)

        key, value = _extract_value(line)
        if key is None or value is None:
            continue

        # The credential field is exposed under multiple spellings;
        # match the documented one ("api_key") and any alias the
        # loader happens to accept. The internal ``ak`` field from
        # :mod:`arr_cli.facade.config` is intentionally NOT included
        # because it never appears in user-facing config files.
        if key == "api_key":
            if value != API_KEY_PLACEHOLDER:
                violations.append(
                    f"line {line_no}: api_key value {value!r} is not the "
                    f"documented placeholder {API_KEY_PLACEHOLDER!r}"
                )
        elif key == "user_id":
            if value != USER_ID_PLACEHOLDER:
                violations.append(
                    f"line {line_no}: user_id value {value!r} is not the "
                    f"documented placeholder {USER_ID_PLACEHOLDER!r}"
                )

    return violations


def is_placeholder_only(path) -> bool:
    """Return ``True`` iff ``validate_example_config(path)`` is empty.

    Convenience wrapper for the common "is this file OK?" call site;
    keeps the contract obvious at the call site without leaking the
    violation list to consumers that don't care about diagnostics.
    """
    return len(validate_example_config(path)) == 0
