"""Unit tests for :mod:`arr_cli.facade.retry`.

Covers the contract spelled out in task 5.1 / 5.2 of tasks.md:

* ``with_retry(fn, *, attempts, deadline)`` retries on
  :class:`arr_cli.facade.errors.NetworkError` only.
* :class:`AuthError`, :class:`HttpError`, and :class:`ParseError`
  propagate immediately without retry.
* Backoff uses exponential delay with jitter; tests assert the
  sequence of sleep durations is correct (without sleeping for
  real — :func:`time.sleep` is patched).
* The wall-clock ``deadline`` cap stops the loop mid-flight.
* ``with_retry`` re-raises the final :class:`NetworkError` with the
  attempt count appended to the message.
* ``arr_cli.facade.transport.get`` wires ``cfg.retry`` and
  ``cfg.deadline`` through to the retry layer and skips the layer
  entirely when ``cfg.retry == 0``.

The tests use ``unittest`` + ``unittest.mock`` only (no third-party
deps). :func:`time.sleep`, :func:`time.monotonic`, and
:func:`random.uniform` are patched so the suite finishes in
milliseconds even when the policy asserts several backoff intervals.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# Make the project importable regardless of the test runner's CWD.
_PROJ_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from arr_cli.facade.errors import (  # noqa: E402  - sys.path tweak above
    AuthError,
    HttpError,
    NetworkError,
    ParseError,
)
from arr_cli.facade.retry import (  # noqa: E402
    DEFAULT_BASE_DELAY,
    DEFAULT_JITTER_RANGE,
    _compute_delay,
    _is_retryable,
    with_retry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_clock(start: float = 1000.0) -> tuple[MagicMock, list[float]]:
    """Return a monotonic clock and a recorder for advance events.

    The returned ``clock`` mock returns ``start`` initially; tests
    can advance it with ``clock.return_value += dt``. The recorder
    accumulates every value the mock has returned so tests can assert
    that ``time.monotonic()`` was sampled between sleep calls.
    """
    clock = MagicMock(return_value=start)
    samples: list[float] = [start]

    def _advance(dt: float) -> None:
        new_value = clock.return_value + dt
        clock.return_value = new_value
        samples.append(new_value)

    clock.attach_mock = MagicMock()  # appease attribute lookupers
    clock.advance = _advance  # type: ignore[attr-defined]
    return clock, samples


def _network_error(
    *,
    service: str = "radarr",
    op: str = "request",
    message: str = "radarr: transport failure (ConnectionError): refused",
    url: str = "https://radarr.example/api/v3/calendar",
) -> NetworkError:
    """Build a :class:`NetworkError` with a stable URL for assertions."""
    return NetworkError(service, op, message, url=url)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestComputeDelay(unittest.TestCase):
    """``_compute_delay`` schedule (REQ NFR-Reliability backoff)."""

    def test_attempt_one_returns_zero(self) -> None:
        # No wait before the very first call — operators shouldn't
        # pay a startup tax on the happy path.
        self.assertEqual(
            _compute_delay(1, base=DEFAULT_BASE_DELAY, jitter=0.0),
            0.0,
        )

    def test_attempt_two_returns_base(self) -> None:
        # First retry: one base interval, no jitter when jitter=0.
        self.assertEqual(
            _compute_delay(2, base=DEFAULT_BASE_DELAY, jitter=0.0),
            DEFAULT_BASE_DELAY,
        )

    def test_attempt_three_returns_two_base(self) -> None:
        # Second retry: 2 * base.
        self.assertEqual(
            _compute_delay(3, base=DEFAULT_BASE_DELAY, jitter=0.0),
            DEFAULT_BASE_DELAY * 2,
        )

    def test_attempt_four_returns_four_base(self) -> None:
        # Third retry: 4 * base.
        self.assertEqual(
            _compute_delay(4, base=DEFAULT_BASE_DELAY, jitter=0.0),
            DEFAULT_BASE_DELAY * 4,
        )

    def test_jitter_is_added_on_top(self) -> None:
        # With a fixed jitter draw the schedule stays deterministic
        # for the assertion.
        with patch(
            "arr_cli.facade.retry.random.uniform",
            return_value=0.1,
        ):
            self.assertEqual(
                _compute_delay(2, base=DEFAULT_BASE_DELAY, jitter=0.25),
                DEFAULT_BASE_DELAY + 0.1,
            )


class TestIsRetryable(unittest.TestCase):
    """``_is_retryable`` only matches :class:`NetworkError`."""

    def test_network_error_is_retryable(self) -> None:
        self.assertTrue(_is_retryable(_network_error()))

    def test_auth_error_is_not_retryable(self) -> None:
        self.assertFalse(
            _is_retryable(
                AuthError(
                    "jellyfin",
                    "now",
                    "401 for /Sessions",
                )
            )
        )

    def test_http_error_is_not_retryable(self) -> None:
        self.assertFalse(
            _is_retryable(
                HttpError(
                    "radarr",
                    "movie",
                    "404 for /api/v3/movie/42",
                    status=404,
                )
            )
        )

    def test_parse_error_is_not_retryable(self) -> None:
        self.assertFalse(
            _is_retryable(
                ParseError(
                    "seerr",
                    "user",
                    "invalid JSON at byte offset 17",
                    byte_offset=17,
                )
            )
        )

    def test_unrelated_exception_is_not_retryable(self) -> None:
        # Bare ``Exception`` (e.g. an unexpected bug) bubbles up;
        # retrying would burn budget on something a retry can't fix.
        self.assertFalse(_is_retryable(RuntimeError("boom")))


# ---------------------------------------------------------------------------
# with_retry: happy path and non-retryable propagation
# ---------------------------------------------------------------------------


class TestWithRetryHappyPath(unittest.TestCase):
    """``with_retry`` runs the closure once and returns its value."""

    def test_returns_value_on_first_success(self) -> None:
        calls: list[int] = []

        def fn() -> str:
            calls.append(1)
            return "ok"

        with patch("arr_cli.facade.retry.time.sleep") as sleep:
            result = with_retry(fn, attempts=3, deadline=None)
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 1)
        # No sleep on the happy path.
        sleep.assert_not_called()

    def test_attempts_one_skips_retry(self) -> None:
        # ``attempts == 1`` means "single try, no retries" — the
        # same observable behaviour as skipping the layer entirely
        # (matches the cold-start budget path in transport.get).
        calls: list[int] = []

        def fn() -> None:
            calls.append(1)
            raise _network_error()

        with patch("arr_cli.facade.retry.time.sleep") as sleep:
            with self.assertRaises(NetworkError):
                with_retry(fn, attempts=1, deadline=None)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()


class TestWithRetryNonRetryable(unittest.TestCase):
    """Non-network errors bubble up verbatim (no retry, no sleep)."""

    def test_auth_error_propagates_immediately(self) -> None:
        calls: list[int] = []

        def fn() -> None:
            calls.append(1)
            raise AuthError("jellyfin", "now", "401 for /Sessions")

        with patch("arr_cli.facade.retry.time.sleep") as sleep:
            with self.assertRaises(AuthError) as ctx:
                with_retry(fn, attempts=5, deadline=None)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()
        self.assertEqual(ctx.exception.exit_code, 2)

    def test_http_error_propagates_immediately(self) -> None:
        calls: list[int] = []

        def fn() -> None:
            calls.append(1)
            raise HttpError(
                "radarr",
                "movie",
                "404 for /api/v3/movie/42",
                status=404,
            )

        with patch("arr_cli.facade.retry.time.sleep") as sleep:
            with self.assertRaises(HttpError) as ctx:
                with_retry(fn, attempts=5, deadline=None)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()
        self.assertEqual(ctx.exception.exit_code, 4)
        self.assertEqual(ctx.exception.status, 404)

    def test_parse_error_propagates_immediately(self) -> None:
        calls: list[int] = []

        def fn() -> None:
            calls.append(1)
            raise ParseError(
                "seerr",
                "user",
                "invalid JSON at byte offset 17",
                byte_offset=17,
            )

        with patch("arr_cli.facade.retry.time.sleep") as sleep:
            with self.assertRaises(ParseError) as ctx:
                with_retry(fn, attempts=5, deadline=None)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()
        self.assertEqual(ctx.exception.exit_code, 5)

    def test_unrelated_exception_propagates(self) -> None:
        # A non-ArrError exception (e.g. a programming bug) bubbles
        # up so operators see a real traceback rather than a silent
        # retry loop on something a retry cannot fix.
        def fn() -> None:
            raise ValueError("boom")

        with patch("arr_cli.facade.retry.time.sleep") as sleep:
            with self.assertRaises(ValueError):
                with_retry(fn, attempts=3, deadline=None)
        sleep.assert_not_called()


# ---------------------------------------------------------------------------
# with_retry: retryable path
# ---------------------------------------------------------------------------


class TestWithRetryRetriesNetwork(unittest.TestCase):
    """Retry network-class failures, with the documented backoff."""

    def test_succeeds_on_second_attempt(self) -> None:
        calls: list[int] = []

        def fn() -> str:
            calls.append(1)
            if len(calls) < 2:
                raise _network_error()
            return "recovered"

        with patch(
            "arr_cli.facade.retry.random.uniform",
            return_value=0.0,
        ):
            with patch("arr_cli.facade.retry.time.sleep") as sleep:
                result = with_retry(fn, attempts=3, deadline=None)

        self.assertEqual(result, "recovered")
        self.assertEqual(len(calls), 2)
        # One sleep before the second attempt: base + jitter (jitter=0).
        sleep.assert_called_once_with(DEFAULT_BASE_DELAY)

    def test_exhausts_attempts_and_raises_final(self) -> None:
        calls: list[int] = []

        def fn() -> None:
            calls.append(1)
            raise _network_error()

        with patch(
            "arr_cli.facade.retry.random.uniform",
            return_value=0.0,
        ):
            with patch("arr_cli.facade.retry.time.sleep") as sleep:
                with self.assertRaises(NetworkError) as ctx:
                    with_retry(fn, attempts=3, deadline=None)

        # 3 total attempts, 2 sleeps between them.
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            sleep.call_args_list,
            [
                unittest.mock.call(DEFAULT_BASE_DELAY),
                unittest.mock.call(DEFAULT_BASE_DELAY * 2),
            ],
        )
        # Final message carries the attempt count.
        self.assertIn("after 3 attempts", ctx.exception.message)
        # Underlying cause is preserved so debug-mode traceback
        # attribution still works.
        self.assertIsInstance(ctx.exception.__cause__, NetworkError)

    def test_cause_chain_preserved(self) -> None:
        # ``raise from`` ensures the original exception's class name
        # stays reachable through ``__cause__`` for debug logging.
        original = _network_error(message="radarr: transport failure (Timeout): read timed out")

        def fn() -> None:
            raise original

        with patch("arr_cli.facade.retry.time.sleep"):
            with self.assertRaises(NetworkError) as ctx:
                with_retry(fn, attempts=2, deadline=None)
        self.assertIs(ctx.exception.__cause__, original)

    def test_exhausted_message_mentions_deadline(self) -> None:
        def fn() -> None:
            raise _network_error()

        with patch("arr_cli.facade.retry.time.sleep"):
            with self.assertRaises(NetworkError) as ctx:
                with_retry(fn, attempts=4, deadline=12.5)
        self.assertIn("deadline=12.50s", ctx.exception.message)

    def test_exhausted_message_mentions_unset_deadline(self) -> None:
        def fn() -> None:
            raise _network_error()

        with patch("arr_cli.facade.retry.time.sleep"):
            with self.assertRaises(NetworkError) as ctx:
                with_retry(fn, attempts=2, deadline=None)
        self.assertIn("deadline=unset", ctx.exception.message)


class TestWithRetryDeadline(unittest.TestCase):
    """Deadline stops the loop mid-flight (REQ NFR-Reliability)."""

    def test_deadline_blown_stops_retries(self) -> None:
        # ``fn`` advances the clock past the deadline so the very
        # first post-failure check sees ``elapsed > deadline`` and
        # breaks out of the loop. We assert that the function ran
        # exactly once and no retry sleep happened.
        clock, _ = _fake_clock(start=0.0)
        calls: list[int] = []

        def fn() -> None:
            calls.append(1)
            # Pretend the request itself took long enough to blow
            # the deadline.
            clock.advance(10.0)
            raise _network_error()

        with patch(
            "arr_cli.facade.retry.time.monotonic",
            clock,
        ):
            with patch("arr_cli.facade.retry.time.sleep") as sleep:
                with self.assertRaises(NetworkError) as ctx:
                    with_retry(fn, attempts=5, deadline=1.0)

        # Exactly one attempt: the deadline check aborted the
        # retry before any sleep happened.
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()
        # The final error message references the deadline.
        self.assertIn("after 1 attempts", ctx.exception.message)
        self.assertIn("deadline=1.00s", ctx.exception.message)

    def test_deadline_none_means_no_cap(self) -> None:
        # Without a deadline the loop is bounded only by ``attempts``.
        calls: list[int] = []

        def fn() -> None:
            calls.append(1)
            raise _network_error()

        with patch("arr_cli.facade.retry.time.sleep"):
            with self.assertRaises(NetworkError):
                with_retry(fn, attempts=4, deadline=None)
        self.assertEqual(len(calls), 4)


class TestWithRetryArgumentValidation(unittest.TestCase):
    """Reject obviously-invalid ``attempts`` values."""

    def test_attempts_zero_raises(self) -> None:
        # ``attempts == 0`` would mean "no attempts at all", which
        # is meaningless; force the caller to pass ``1`` for the
        # single-try contract.
        with self.assertRaises(ValueError):
            with_retry(lambda: "x", attempts=0, deadline=None)

    def test_attempts_negative_raises(self) -> None:
        with self.assertRaises(ValueError):
            with_retry(lambda: "x", attempts=-1, deadline=None)

    def test_attempts_non_int_raises(self) -> None:
        with self.assertRaises(TypeError):
            with_retry(lambda: "x", attempts=1.5, deadline=None)


# ---------------------------------------------------------------------------
# transport.get wiring
# ---------------------------------------------------------------------------


def _service_config(
    *,
    retry: int = 0,
    deadline: float | None = None,
    jellyfin=None,
    radarr=None,
    sonarr=None,
    maintainerr=None,
    seerr=None,
):
    """Build a minimal ServiceConfig for transport tests."""
    # Local import to avoid a sys.path-ordering trap when the
    # facade package is re-exported.
    from arr_cli.facade.config import AuthConfig, ServiceConfig

    if jellyfin is None:
        jellyfin = AuthConfig(url="https://example.test", ak="tok")
    return ServiceConfig(
        jellyfin=jellyfin,
        radarr=radarr,
        sonarr=sonarr,
        maintainerr=maintainerr,
        seerr=seerr,
        connect_timeout=5.0,
        read_timeout=30.0,
        retry=retry,
        deadline=deadline,
    )


def _fake_response(*, status_code: int = 200, body: Any = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.reason = "OK"
    if body is not None:
        response.content = json.dumps(body).encode("utf-8")
    else:
        response.content = b""
    return response


def _patch_session(response: MagicMock):
    session = MagicMock()
    session.get.return_value = response
    session.close.return_value = None
    return patch(
        "arr_cli.facade.transport._ensure_session",
        return_value=session,
    )


class TestTransportRetryWiring(unittest.TestCase):
    """``transport.get`` consults ``cfg.retry`` / ``cfg.deadline``."""

    def test_default_retry_zero_skips_layer(self) -> None:
        # ``cfg.retry == 0`` (default) must NOT touch the retry
        # layer at all so the cold-start budget stays under 2 s
        # (NFR-Performance).
        from arr_cli.facade.transport import get

        response = _fake_response(status_code=200, body={"ok": True})
        with patch("arr_cli.facade.retry.time.sleep") as sleep:
            with patch("arr_cli.facade.transport.with_retry") as wrapped:
                # ``with_retry`` is the re-export path that
                # ``transport.get`` uses; patching it as a no-op
                # lets us detect whether ``transport.get`` invoked
                # it at all.
                wrapped.side_effect = (
                    lambda fn, *, attempts, deadline: fn()
                )
                with _patch_session(response):
                    payload = get(
                        "jellyfin",
                        "/Sessions",
                        cfg=_service_config(retry=0),
                    )
        self.assertEqual(payload, {"ok": True})
        sleep.assert_not_called()
        wrapped.assert_not_called()

    def test_retry_nonzero_invokes_with_retry(self) -> None:
        from arr_cli.facade.transport import get

        response = _fake_response(status_code=200, body={"ok": True})
        captured: dict[str, Any] = {}

        def _spy(fn, *, attempts, deadline):
            captured["attempts"] = attempts
            captured["deadline"] = deadline
            return fn()

        with patch("arr_cli.facade.retry.time.sleep"):
            with patch(
                "arr_cli.facade.transport.with_retry",
                side_effect=_spy,
            ):
                with _patch_session(response):
                    get(
                        "jellyfin",
                        "/Sessions",
                        cfg=_service_config(retry=2, deadline=4.0),
                    )
        # ``retry == 2`` should map to ``attempts == 3`` (1 initial + 2 retries).
        self.assertEqual(captured["attempts"], 3)
        self.assertEqual(captured["deadline"], 4.0)

    def test_retry_zero_with_deadline_still_skips_layer(self) -> None:
        # Even when a deadline is set, ``retry == 0`` skips the
        # layer entirely; deadline only matters when retries are
        # actually attempted.
        from arr_cli.facade.transport import get

        response = _fake_response(status_code=200, body={"ok": True})
        with patch("arr_cli.facade.retry.time.sleep") as sleep:
            with patch("arr_cli.facade.transport.with_retry") as wrapped:
                wrapped.side_effect = (
                    lambda fn, *, attempts, deadline: fn()
                )
                with _patch_session(response):
                    get(
                        "jellyfin",
                        "/Sessions",
                        cfg=_service_config(retry=0, deadline=2.0),
                    )
        sleep.assert_not_called()
        wrapped.assert_not_called()


if __name__ == "__main__":
    unittest.main()