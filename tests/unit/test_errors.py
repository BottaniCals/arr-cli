"""Unit tests for :mod:`arr_cli.facade.errors`.

These tests cover the contract spelled out in task 2.3 of tasks.md:

* Each concrete subclass carries the documented ``exit_code`` (1..5).
* ``str(exc)`` produces a structured ``service=... op=... status=...
  message=...`` line for at least one example per class. The ``status=``
  token is omitted when the class does not carry one (e.g. ConfigError).
* ``__cause__`` is preserved when the exception is raised with ``from``.

The tests are intentionally stdlib-only (``unittest`` + ``unittest.mock``)
so they run without pytest or ``responses`` installed; the test runner
documented in design.md (``pytest``) wraps them via its standard
unittest discovery.
"""

from __future__ import annotations

import unittest

from arr_cli.facade.errors import (
    ArrError,
    AuthError,
    ConfigError,
    HttpError,
    NetworkError,
    ParseError,
)


class TestArrErrorBase(unittest.TestCase):
    """Tests for the :class:`ArrError` base class shared by all subclasses."""

    def test_exit_code_defaults_to_one(self) -> None:
        # The base class defaults to exit code 1 so accidental bare
        # raises degrade gracefully; subclasses override.
        exc = ArrError("config", "load", "boom")
        self.assertEqual(exc.exit_code, 1)

    def test_exit_code_can_be_overridden_via_constructor(self) -> None:
        # Subclasses can override the default without subclassing
        # again — useful for tests and for hypothetical subclasses
        # that need a different code.
        exc = ArrError("config", "load", "boom", exit_code=2)
        self.assertEqual(exc.exit_code, 2)

    def test_attributes_are_stored(self) -> None:
        exc = ArrError("svc", "op", "msg")
        self.assertEqual(exc.service, "svc")
        self.assertEqual(exc.op, "op")
        self.assertEqual(exc.message, "msg")

    def test_str_format_omits_status_when_absent(self) -> None:
        # Per REQ-4 AC3 the structured line uses ``service=`` ``op=``
        # ``status=`` ``message=`` tokens. The base class has no status
        # so the token must be omitted rather than emitted as empty.
        exc = ArrError("config", "load", "config file not found")
        self.assertEqual(
            str(exc),
            "service=config op=load message=config file not found",
        )

    def test_cause_is_preserved_with_from(self) -> None:
        # The PEP 3134 ``raise X from Y`` chain must surface through
        # __cause__ so the CLI's debug-mode traceback can include it.
        try:
            try:
                raise ValueError("trigger")
            except ValueError as inner:
                raise ConfigError("config", "load", "wrapped") from inner
        except ConfigError as captured:
            self.assertIsNotNone(captured.__cause__)
            self.assertIsInstance(captured.__cause__, ValueError)
            self.assertEqual(str(captured.__cause__), "trigger")

    def test_cause_is_none_without_from(self) -> None:
        # Plain ``raise ConfigError(...)`` leaves __cause__ as None;
        # this is the default for non-chained raises.
        exc = ConfigError("config", "load", "msg")
        self.assertIsNone(exc.__cause__)

    def test_suppressed_context_default(self) -> None:
        # When raise-without-from fires inside an except block, PEP 3134
        # attaches the prior exception to __context__ (implicit) but
        # __cause__ stays None. The CLI's debug path treats __cause__
        # as the authoritative trigger so this is the right default.
        try:
            try:
                raise ValueError("inner")
            except ValueError:
                raise ConfigError("config", "load", "outer")
        except ConfigError as captured:
            self.assertIsNone(captured.__cause__)
            self.assertIsInstance(captured.__context__, ValueError)


class TestConfigError(unittest.TestCase):
    """``ConfigError`` → exit code 1, no status token."""

    def test_exit_code_is_one(self) -> None:
        exc = ConfigError("config", "load", "config file not found")
        self.assertEqual(exc.exit_code, 1)

    def test_str_format(self) -> None:
        exc = ConfigError(
            "config",
            "load",
            "config file not found: ~/.config/lily/arr.conf",
        )
        self.assertEqual(
            str(exc),
            "service=config op=load "
            "message=config file not found: ~/.config/lily/arr.conf",
        )

    def test_str_does_not_contain_status_token(self) -> None:
        exc = ConfigError("config", "load", "msg")
        self.assertNotIn("status=", str(exc))

    def test_is_arr_error(self) -> None:
        # Subclass relationship is required so the CLI's ``except
        # ArrError`` handler catches every concrete error.
        exc = ConfigError("config", "load", "msg")
        self.assertIsInstance(exc, ArrError)
        self.assertIsInstance(exc, Exception)


class TestAuthError(unittest.TestCase):
    """``AuthError`` → exit code 2, no status token."""

    def test_exit_code_is_two(self) -> None:
        exc = AuthError("jellyfin", "now", "401 Unauthorized")
        self.assertEqual(exc.exit_code, 2)

    def test_str_format(self) -> None:
        exc = AuthError(
            "jellyfin",
            "now",
            "401 Unauthorized; check jellyfin.api_key in arr.conf",
        )
        self.assertEqual(
            str(exc),
            "service=jellyfin op=now "
            "message=401 Unauthorized; check jellyfin.api_key in arr.conf",
        )

    def test_str_does_not_contain_status_token(self) -> None:
        # AuthError has no status attribute; the structured line must
        # not invent a status= token for it.
        exc = AuthError("jellyfin", "now", "msg")
        self.assertNotIn("status=", str(exc))

    def test_is_arr_error(self) -> None:
        exc = AuthError("jellyfin", "now", "msg")
        self.assertIsInstance(exc, ArrError)


class TestNetworkError(unittest.TestCase):
    """``NetworkError`` → exit code 3, carries ``url`` and surfaces it."""

    def test_exit_code_is_three(self) -> None:
        exc = NetworkError(
            "radarr",
            "calendar",
            "Timeout",
            url="https://radarr.example/api/v3/calendar",
        )
        self.assertEqual(exc.exit_code, 3)

    def test_url_is_stored(self) -> None:
        url = "https://radarr.example/api/v3/calendar"
        exc = NetworkError("radarr", "calendar", "Timeout", url=url)
        self.assertEqual(exc.url, url)

    def test_str_format_includes_url_as_status(self) -> None:
        # The structured line surfaces the URL via the ``status=``
        # token (qualified by the value being a URL rather than an HTTP
        # code) so operators can grep a single token class for ``ops``.
        exc = NetworkError(
            "radarr",
            "calendar",
            "Timeout",
            url="https://radarr.example/api/v3/calendar",
        )
        self.assertEqual(
            str(exc),
            "service=radarr op=calendar "
            "status=https://radarr.example/api/v3/calendar "
            "message=Timeout",
        )

    def test_cause_is_preserved_with_from(self) -> None:
        # NetworkError typically wraps a requests.exceptions.Timeout
        # (or similar); the chain must be preserved so debug mode
        # can render the full traceback.
        try:
            try:
                raise TimeoutError("timed out")
            except TimeoutError as inner:
                raise NetworkError(
                    "radarr",
                    "calendar",
                    "Timeout",
                    url="https://radarr.example/api/v3/calendar",
                ) from inner
        except NetworkError as captured:
            self.assertIsNotNone(captured.__cause__)
            self.assertIsInstance(captured.__cause__, TimeoutError)

    def test_is_arr_error(self) -> None:
        exc = NetworkError("radarr", "calendar", "msg", url="u")
        self.assertIsInstance(exc, ArrError)


class TestHttpError(unittest.TestCase):
    """``HttpError`` → exit code 4, carries ``status`` and surfaces it."""

    def test_exit_code_is_four(self) -> None:
        exc = HttpError("sonarr", "series", "Series not found", status=404)
        self.assertEqual(exc.exit_code, 4)

    def test_status_is_stored(self) -> None:
        exc = HttpError("sonarr", "series", "Series not found", status=404)
        self.assertEqual(exc.status, 404)

    def test_str_format_includes_status(self) -> None:
        exc = HttpError("sonarr", "series", "Series not found", status=404)
        self.assertEqual(
            str(exc),
            "service=sonarr op=series status=404 message=Series not found",
        )

    def test_str_format_with_5xx(self) -> None:
        # 5xx surfaces the same way as 4xx; only the code changes.
        exc = HttpError("seerr", "user", "Internal Server Error", status=500)
        self.assertEqual(
            str(exc),
            "service=seerr op=user status=500 message=Internal Server Error",
        )

    def test_cause_is_preserved_with_from(self) -> None:
        try:
            try:
                raise RuntimeError("body excerpt truncated")
            except RuntimeError as inner:
                raise HttpError(
                    "sonarr",
                    "series",
                    "Series not found",
                    status=404,
                ) from inner
        except HttpError as captured:
            self.assertIsNotNone(captured.__cause__)
            self.assertIsInstance(captured.__cause__, RuntimeError)

    def test_is_arr_error(self) -> None:
        exc = HttpError("sonarr", "series", "msg", status=404)
        self.assertIsInstance(exc, ArrError)


class TestParseError(unittest.TestCase):
    """``ParseError`` → exit code 5, carries ``byte_offset`` and surfaces it."""

    def test_exit_code_is_five(self) -> None:
        exc = ParseError("seerr", "user", "invalid JSON", byte_offset=17)
        self.assertEqual(exc.exit_code, 5)

    def test_byte_offset_is_stored(self) -> None:
        exc = ParseError("seerr", "user", "invalid JSON", byte_offset=17)
        self.assertEqual(exc.byte_offset, 17)

    def test_str_format_includes_byte_offset(self) -> None:
        # The byte offset is emitted as ``status=byte_offset=N`` to keep
        # the structured-line schema consistent with HttpError while
        # disambiguating from HTTP status codes.
        exc = ParseError(
            "seerr",
            "user",
            "invalid JSON at byte offset 17",
            byte_offset=17,
        )
        self.assertEqual(
            str(exc),
            "service=seerr op=user status=byte_offset=17 "
            "message=invalid JSON at byte offset 17",
        )

    def test_byte_offset_zero_is_visible(self) -> None:
        # Zero is a valid offset (the first byte is non-JSON); verify
        # the rendering does not silently drop it.
        exc = ParseError("seerr", "user", "invalid JSON", byte_offset=0)
        rendered = str(exc)
        self.assertIn("status=byte_offset=0", rendered)

    def test_cause_is_preserved_with_from(self) -> None:
        try:
            try:
                raise ValueError("Expecting value: line 1 column 1 (char 0)")
            except ValueError as inner:
                raise ParseError(
                    "seerr",
                    "user",
                    "invalid JSON",
                    byte_offset=0,
                ) from inner
        except ParseError as captured:
            self.assertIsNotNone(captured.__cause__)
            self.assertIsInstance(captured.__cause__, ValueError)

    def test_is_arr_error(self) -> None:
        exc = ParseError("seerr", "user", "msg", byte_offset=0)
        self.assertIsInstance(exc, ArrError)


class TestExitCodeMap(unittest.TestCase):
    """Lock the full exit-code map (1..5) and reusability as ``ArrError``."""

    def test_exit_code_map_is_1_through_5(self) -> None:
        # The hierarchy covers exactly one class per documented code.
        # Future subclasses that introduce a new code should be added
        # here and in tasks.md task 2.2.
        self.assertEqual(ConfigError("s", "o", "m").exit_code, 1)
        self.assertEqual(AuthError("s", "o", "m").exit_code, 2)
        self.assertEqual(NetworkError("s", "o", "m", url="u").exit_code, 3)
        self.assertEqual(HttpError("s", "o", "m", status=404).exit_code, 4)
        self.assertEqual(ParseError("s", "o", "m", byte_offset=0).exit_code, 5)

    def test_subclasses_are_catchable_as_arr_error(self) -> None:
        # The CLI's error handler is ``except ArrError as exc: ...``. If
        # any subclass forgot to inherit ArrError this would fail.
        for exc in (
            ConfigError("s", "o", "m"),
            AuthError("s", "o", "m"),
            NetworkError("s", "o", "m", url="u"),
            HttpError("s", "o", "m", status=404),
            ParseError("s", "o", "m", byte_offset=0),
        ):
            with self.subTest(exc=exc):
                self.assertIsInstance(exc, ArrError)

    def test_subclasses_are_catchable_as_exception(self) -> None:
        # ``raise ... from ...`` and the standard except machinery both
        # rely on Exception inheritance.
        for cls, kwargs in (
            (ConfigError, {}),
            (AuthError, {}),
            (NetworkError, {"url": "u"}),
            (HttpError, {"status": 404}),
            (ParseError, {"byte_offset": 0}),
        ):
            with self.subTest(cls=cls):
                exc = cls("s", "o", "m", **kwargs)
                self.assertIsInstance(exc, Exception)


if __name__ == "__main__":
    unittest.main()
