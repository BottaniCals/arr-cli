"""Unit tests for :mod:`arr_cli.facade.example_validator`.

Covers the contract spelled out in task 15 of tasks.md:

* ``validate_example_config`` returns an empty list when the file
  contains only documented placeholders (REQ-1 AC4).
* A real URL (any host other than ``example.com``) is reported as a
  violation with a line-numbered diagnostic.
* An ``api_key`` value other than ``YOUR_API_KEY_HERE`` is reported.
* A ``user_id`` value other than ``<user-id>`` is reported.
* Whole-line and trailing comments are ignored so the documented
  TOML block in ``arr.conf.example`` (intentionally commented out)
  does not produce false positives.
* The committed ``arr.conf.example`` file passes every check (golden
  test, catches accidental edits to the committed file).

The tests are stdlib-only (``unittest`` + ``tempfile`` + ``pathlib``)
so they run without pytest or third-party dependencies; the test
runner documented in design.md (``pytest``) wraps them via its
standard unittest discovery.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make the project importable regardless of the test runner's CWD.
_PROJ_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from arr_cli.facade.example_validator import (  # noqa: E402
    ALLOWED_HOSTNAME,
    API_KEY_PLACEHOLDER,
    USER_ID_PLACEHOLDER,
    is_placeholder_only,
    validate_example_config,
)


def _write_temp_example(body: str) -> Path:
    """Write ``body`` to a temp ``.yaml`` file and return its Path.

    The file uses ``0600`` on POSIX so it is readable by the
    validator without permission errors. The temp file is cleaned up
    automatically when the returned path goes out of scope (the
    caller is expected to call ``.unlink(missing_ok=True)`` in its
    own ``tearDown`` if it wants deterministic cleanup).
    """
    fd, raw_path = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    path = Path(raw_path)
    path.write_text(body, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    return path


# ---------------------------------------------------------------------------
# Placeholder-string constants
# ---------------------------------------------------------------------------


class TestPlaceholderConstants(unittest.TestCase):
    """The exported constants match the documented contract (REQ-1 AC4)."""

    def test_api_key_placeholder(self) -> None:
        # The literal string MUST be ``YOUR_API_KEY_HERE`` so the
        # validator and the README agree on what counts as a
        # placeholder.
        self.assertEqual(API_KEY_PLACEHOLDER, "YOUR_API_KEY_HERE")

    def test_user_id_placeholder(self) -> None:
        # The literal string MUST be ``<user-id>`` so the validator
        # and the README agree on what counts as a placeholder.
        self.assertEqual(USER_ID_PLACEHOLDER, "<user-id>")

    def test_allowed_hostname(self) -> None:
        # The only permitted host is the documented ``example.com``.
        self.assertEqual(ALLOWED_HOSTNAME, "example.com")


# ---------------------------------------------------------------------------
# validate_example_config — happy path
# ---------------------------------------------------------------------------


class TestValidateExampleConfigHappyPath(unittest.TestCase):
    """Validator returns an empty list for well-formed examples."""

    def setUp(self) -> None:
        self._paths: list[Path] = []

    def tearDown(self) -> None:
        for path in self._paths:
            path.unlink(missing_ok=True)

    def _write(self, body: str) -> Path:
        path = _write_temp_example(body)
        self._paths.append(path)
        return path

    def test_full_placeholder_yaml_passes(self) -> None:
        # A complete 5-service YAML with documented placeholders
        # must validate cleanly. Mirrors the committed file.
        body = (
            "jellyfin:\n"
            "  url: https://example.com\n"
            f"  {API_KEY_PLACEHOLDER.split('_')[0]}_key: {API_KEY_PLACEHOLDER}\n"
            f"  user_id: {USER_ID_PLACEHOLDER}\n"
            "radarr:\n"
            "  url: https://example.com\n"
            f"  {API_KEY_PLACEHOLDER.split('_')[0]}_key: {API_KEY_PLACEHOLDER}\n"
            "sonarr:\n"
            "  url: https://example.com\n"
            f"  {API_KEY_PLACEHOLDER.split('_')[0]}_key: {API_KEY_PLACEHOLDER}\n"
            "maintainerr:\n"
            "  url: https://example.com\n"
            "  auth_enabled: false\n"
            "seerr:\n"
            "  url: https://example.com\n"
            f"  {API_KEY_PLACEHOLDER.split('_')[0]}_key: {API_KEY_PLACEHOLDER}\n"
        )
        path = self._write(body)
        self.assertEqual(validate_example_config(path), [])
        self.assertTrue(is_placeholder_only(path))

    def test_empty_file_passes(self) -> None:
        # An empty file has no real values; trivially placeholder-only.
        path = self._write("")
        self.assertEqual(validate_example_config(path), [])

    def test_only_comments_passes(self) -> None:
        # A file of nothing but comments must not produce false
        # positives; the validator must ignore ``#`` lines entirely.
        body = (
            "# placeholder-only schema\n"
            "# nothing real here\n"
            "#\n"
            "   # indented comment\n"
        )
        path = self._write(body)
        self.assertEqual(validate_example_config(path), [])

    def test_trailing_inline_comments_are_ignored(self) -> None:
        # A placeholder value followed by an inline ``# comment``
        # must still pass; the validator must cut on the comment
        # delimiter before matching the value.
        body = (
            "jellyfin:\n"
            "  url: https://example.com  # any trailing text\n"
            f"  {API_KEY_PLACEHOLDER.split('_')[0]}_key: {API_KEY_PLACEHOLDER}   # trailing\n"
            f"  user_id: {USER_ID_PLACEHOLDER}  # another comment\n"
        )
        path = self._write(body)
        self.assertEqual(validate_example_config(path), [])

    def test_url_with_path_is_still_allowed(self) -> None:
        # ``https://example.com/some/path`` is still ``example.com``;
        # the validator must strip the path component before
        # comparing the host.
        body = (
            "jellyfin:\n"
            "  url: https://example.com/some/path\n"
            f"  {API_KEY_PLACEHOLDER.split('_')[0]}_key: {API_KEY_PLACEHOLDER}\n"
        )
        path = self._write(body)
        self.assertEqual(validate_example_config(path), [])

    def test_url_with_port_is_still_allowed(self) -> None:
        # ``https://example.com:8443`` is still ``example.com``; the
        # validator must strip the port before comparing.
        body = (
            "jellyfin:\n"
            "  url: https://example.com:8443\n"
            f"  {API_KEY_PLACEHOLDER.split('_')[0]}_key: {API_KEY_PLACEHOLDER}\n"
        )
        path = self._write(body)
        self.assertEqual(validate_example_config(path), [])

    def test_toml_style_placeholder_assignment_passes(self) -> None:
        # TOML shape ``key = "value"`` must be accepted alongside
        # the YAML ``key: value`` shape so a future TOML-only
        # equivalent file is covered by the same check.
        body = (
            '[jellyfin]\n'
            f'url = "https://example.com"\n'
            f'{API_KEY_PLACEHOLDER.split("_")[0]}_key = "{API_KEY_PLACEHOLDER}"\n'
            f'user_id = "{USER_ID_PLACEHOLDER}"\n'
        )
        path = self._write(body)
        self.assertEqual(validate_example_config(path), [])


# ---------------------------------------------------------------------------
# validate_example_config — violation cases
# ---------------------------------------------------------------------------


class TestValidateExampleConfigViolations(unittest.TestCase):
    """Each documented invariant produces a precise violation message."""

    def setUp(self) -> None:
        self._paths: list[Path] = []

    def tearDown(self) -> None:
        for path in self._paths:
            path.unlink(missing_ok=True)

    def _write(self, body: str) -> Path:
        path = _write_temp_example(body)
        self._paths.append(path)
        return path

    def _assert_has(self, violations: list[str], *, substring: str) -> None:
        """Assert at least one violation message contains ``substring``.

        Substring match (rather than full-string match) keeps the
        test resilient to small wording tweaks while still pinning
        the meaningful diagnostic content (URL, value, line number).
        """
        matched = [v for v in violations if substring in v]
        self.assertTrue(
            matched,
            msg=(
                f"expected a violation containing {substring!r}; "
                f"got {violations!r}"
            ),
        )

    def test_real_url_is_rejected(self) -> None:
        # A real-looking host MUST be reported as a violation.
        body = (
            "jellyfin:\n"
            "  url: https://my-jellyfin.duckdns.org\n"
            f"  {API_KEY_PLACEHOLDER.split('_')[0]}_key: {API_KEY_PLACEHOLDER}\n"
        )
        path = self._write(body)
        violations = validate_example_config(path)
        self.assertEqual(len(violations), 1)
        self._assert_has(
            violations, substring="my-jellyfin.duckdns.org"
        )
        self._assert_has(violations, substring="line 2")

    def test_similar_but_not_example_is_rejected(self) -> None:
        # ``example.com.evil.com`` looks like ``example.com`` but is
        # a different host; the validator must NOT mistake it for
        # the documented placeholder.
        body = (
            "jellyfin:\n"
            "  url: https://example.com.evil.com\n"
            f"  {API_KEY_PLACEHOLDER.split('_')[0]}_key: {API_KEY_PLACEHOLDER}\n"
        )
        path = self._write(body)
        violations = validate_example_config(path)
        self.assertEqual(len(violations), 1)
        self._assert_has(violations, substring="example.com.evil.com")

    def test_api_key_not_equal_to_placeholder_is_rejected(self) -> None:
        # Any ``api_key`` value other than the documented placeholder
        # is a violation -- this is the canonical "real key leaked
        # into the example" case.
        body = (
            "jellyfin:\n"
            "  url: https://example.com\n"
            "  api_key: abc123def456ghi789\n"
        )
        path = self._write(body)
        violations = validate_example_config(path)
        self.assertEqual(len(violations), 1)
        self._assert_has(violations, substring="abc123def456ghi789")
        self._assert_has(violations, substring="api_key")

    def test_user_id_not_equal_to_placeholder_is_rejected(self) -> None:
        # Any ``user_id`` value other than ``<user-id>`` is a
        # violation -- a real Jellyfin user id is 32 hex chars, so
        # this catches the common "copy/paste from dashboard" leak.
        body = (
            "jellyfin:\n"
            "  url: https://example.com\n"
            f"  {API_KEY_PLACEHOLDER.split('_')[0]}_key: {API_KEY_PLACEHOLDER}\n"
            "  user_id: 50789abcdef01234567890abcdef0123\n"
        )
        path = self._write(body)
        violations = validate_example_config(path)
        self.assertEqual(len(violations), 1)
        self._assert_has(violations, substring="50789abcdef01234567890abcdef0123")
        self._assert_has(violations, substring="user_id")

    def test_multiple_violations_all_reported(self) -> None:
        # When multiple invariants fail, ALL must be reported -- the
        # CI hook must not short-circuit on the first failure so the
        # editor fixes the file in a single pass.
        body = (
            "jellyfin:\n"
            "  url: https://real-jellyfin.example\n"
            "  api_key: actual-secret-value-12345\n"
            "  user_id: real-user-id-67890\n"
        )
        path = self._write(body)
        violations = validate_example_config(path)
        # Three violations: the URL, the api_key, the user_id.
        self.assertEqual(len(violations), 3)
        self._assert_has(violations, substring="real-jellyfin.example")
        self._assert_has(violations, substring="actual-secret-value-12345")
        self._assert_has(violations, substring="real-user-id-67890")

    def test_url_in_unrelated_position_is_still_caught(self) -> None:
        # A URL embedded anywhere in an active line is a violation
        # even if it isn't the value of a ``url:`` key, so a stray
        # ``api_key: https://internal.example`` typo is reported.
        body = (
            "jellyfin:\n"
            "  url: https://example.com\n"
            "  api_key: https://internal.example/secret\n"
        )
        path = self._write(body)
        violations = validate_example_config(path)
        # Both the URL and the api_key are violations.
        self.assertGreaterEqual(len(violations), 2)
        self._assert_has(violations, substring="internal.example")

    def test_commented_out_real_url_is_ignored(self) -> None:
        # The documented TOML block in ``arr.conf.example`` is
        # intentionally commented out; a real URL inside such a
        # comment must NOT trip the validator.
        body = (
            "# placeholder-only schema\n"
            "# url = https://my-real-jellyfin.duckdns.org\n"
            "jellyfin:\n"
            "  url: https://example.com\n"
            f"  {API_KEY_PLACEHOLDER.split('_')[0]}_key: {API_KEY_PLACEHOLDER}\n"
        )
        path = self._write(body)
        self.assertEqual(validate_example_config(path), [])

    def test_missing_file_is_a_violation(self) -> None:
        # A missing file is itself a violation -- the example is
        # supposed to be a clean placeholder document; an absent
        # example cannot be reviewed.
        missing = _PROJ_ROOT / "does-not-exist.yaml"
        violations = validate_example_config(missing)
        self.assertEqual(len(violations), 1)
        self._assert_has(violations, substring="file not found")


# ---------------------------------------------------------------------------
# Golden test against the committed arr.conf.example
# ---------------------------------------------------------------------------


class TestCommittedExampleIsValid(unittest.TestCase):
    """The committed ``arr.conf.example`` passes every check.

    This is the "golden" test: it catches the editor who pastes a
    real value into the committed file, even if every other test
    would pass on a synthetic copy.
    """

    def test_arr_conf_example_passes(self) -> None:
        example_path = _PROJ_ROOT / "arr.conf.example"
        if not example_path.exists():
            self.skipTest("arr.conf.example not found at repo root")
        violations = validate_example_config(example_path)
        self.assertEqual(
            violations,
            [],
            msg=(
                "arr.conf.example contains non-placeholder values; "
                "this is a REQ-1 AC4 violation. Replace the offending "
                "line with the documented placeholder and re-run.\n\n"
                + "\n".join(violations)
            ),
        )
        self.assertTrue(is_placeholder_only(example_path))


# ---------------------------------------------------------------------------
# Convenience wrapper: is_placeholder_only
# ---------------------------------------------------------------------------


class TestIsPlaceholderOnlyWrapper(unittest.TestCase):
    """``is_placeholder_only`` is a thin boolean wrapper."""

    def setUp(self) -> None:
        self._paths: list[Path] = []

    def tearDown(self) -> None:
        for path in self._paths:
            path.unlink(missing_ok=True)

    def _write(self, body: str) -> Path:
        path = _write_temp_example(body)
        self._paths.append(path)
        return path

    def test_returns_true_for_clean_file(self) -> None:
        path = self._write(
            "jellyfin:\n"
            "  url: https://example.com\n"
            f"  {API_KEY_PLACEHOLDER.split('_')[0]}_key: {API_KEY_PLACEHOLDER}\n"
        )
        self.assertTrue(is_placeholder_only(path))

    def test_returns_false_for_real_url(self) -> None:
        path = self._write(
            "jellyfin:\n"
            "  url: https://real.example\n"
        )
        self.assertFalse(is_placeholder_only(path))

    def test_returns_false_for_missing_file(self) -> None:
        # A missing file is "not placeholder-only" because there is
        # no file to be placeholder-only about.
        missing = _PROJ_ROOT / "definitely-not-a-real-file.yaml"
        self.assertFalse(is_placeholder_only(missing))
