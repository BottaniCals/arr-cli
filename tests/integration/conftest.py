"""Integration test configuration and skip helper (task 19.1).

This module is the single source of truth for the opt-in integration
test pattern. It exposes :func:`skip_unless_run_integration`, which
is a unit-test-friendly decorator that SKIPS the decorated test
unless the test runner is invoked with the ``--run-integration``
flag.

Why opt-in
----------
The integration tests in :mod:`tests.integration` exercise the
arr-cli CLIs against a real instance of one or more of the supported
services (Jellyfin, Radarr, Sonarr, Maintainerr, Seerr). They are
NOT appropriate for the default unit-test run because:

* They require live, reachable services.
* They require per-service credentials (``ARR_LIVE_URL`` and
  ``ARR_LIVE_API_KEY`` environment variables) the operator has not
  committed.
* They make the network calls that the unit tests deliberately
  avoid.

The MVP therefore keeps the integration tests off the default
``make test`` / ``pytest tests/`` path and only enables them behind
an explicit ``--run-integration`` opt-in.

Usage
-----
For pytest::

    @skip_unless_run_integration()
    def test_live_jellyfin_now(self) -> None:
        ...

For unittest::

    @skip_unless_run_integration()
    def test_live_jellyfin_now(self) -> None:
        ...

Both runners recognise the same flag. The decorator is intentionally
a no-op when neither pytest nor unittest is currently driving the
test, so the same import is safe from any context.

How to run
----------
Default (integration tests SKIPPED)::

    pytest tests/
    python -m unittest discover tests
    make test

Opt-in (integration tests RUN)::

    pytest tests/ --run-integration
    python -m unittest discover tests --run-integration   # argparse-style flag
                                                       # (ignored by unittest,
                                                       # opt-in via env var below)

.. note::

    ``unittest`` does not natively parse ``--key=value`` style
    arguments, so for the unittest runner the opt-in can also be
    triggered by setting the ``ARR_RUN_INTEGRATION`` environment
    variable to ``1``. Both runners honour the same name so a
    contributor can pick whichever runner they prefer.

Environment variables consumed by integration tests
--------------------------------------------------
``ARR_RUN_INTEGRATION`` (``"1"`` to enable; same effect as
``--run-integration`` on the pytest CLI). Honoured by the unittest
fallback path because ``unittest`` does not parse the flag natively.

Per-service live endpoints are configured via:

``ARR_LIVE_URL``
    Base URL of the target service (e.g. ``https://jellyfin.example.com``).

``ARR_LIVE_API_KEY``
    API key / token for the target service. ``ARR_LIVE_USER_ID`` is
    honoured by Jellyfin-specific tests for endpoints that require it.

A test that requires the live endpoint MUST skip with an explicit
``ARR_LIVE_URL``-missing reason when the variable is unset, so
operators who opt in without configuring an endpoint get a clear
diagnostic instead of an opaque connection failure.

How the decorator decides to skip
---------------------------------
The decorator looks at, in order:

1. ``"--run-integration"`` in :data:`sys.argv` (pytest passes it
   through when the operator adds it after the test paths).
2. ``"ARR_RUN_INTEGRATION"`` in :data:`os.environ` set to a truthy
   value (``"1"``, ``"true"``, ``"yes"``).
3. The ``PYTEST_RUN_INTEGRATION`` env var (set by ``pytest`` when
   ``-p`` plugins propagate it; we accept it as a fallback so
   ``pytest --run-integration`` works even when the flag is removed
   from ``sys.argv`` by a conftest hook).

If none of those signals is present the test is SKIPPED.

Module-level markers
--------------------
For pytest, you can also drop this line at the top of a test
module to apply the skip to every test in the file::

    pytestmark = skip_unless_run_integration()  # pytest only

For unittest, declare the helper in ``setUpClass`` / class-level
decorators instead — unittest has no module-level marker concept.
"""

from __future__ import annotations

import os
import sys
import unittest
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Opt-in signal detection
# ---------------------------------------------------------------------------


#: Environment variables that act as a second opt-in channel (the primary
#: channel is ``--run-integration`` on the test runner CLI). Both names
#: are recognised so contributors can pick whichever surface they prefer.
_INTEGRATION_ENV_VARS: tuple[str, ...] = (
    "ARR_RUN_INTEGRATION",
    "PYTEST_RUN_INTEGRATION",
)


def _env_truthy(name: str) -> bool:
    """Return True iff the named env var is set to a truthy value.

    Truthy values are ``"1"``, ``"true"``, ``"yes"`` (case-insensitive).
    Anything else (including unset and empty string) is treated as
    falsy so a stale export does not silently turn on integration
    tests.
    """
    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def integration_requested() -> bool:
    """Return True iff the current invocation opted into integration tests.

    The function inspects three signals, in order:

    1. ``--run-integration`` present anywhere in :data:`sys.argv`.
       pytest passes the flag through after the test paths, so this
       is the canonical pytest channel.
    2. ``ARR_RUN_INTEGRATION`` env var set to a truthy value.
       This is the unittest-friendly channel because ``unittest``
       does not natively parse ``--key=value`` style arguments.
    3. ``PYTEST_RUN_INTEGRATION`` env var set to a truthy value.
       A safety net in case a conftest hook stripped the flag from
       ``sys.argv``.

    The function is intentionally cheap (no I/O, no imports of
    pytest) so it can be called from class-level decorators without
    affecting test discovery time.
    """
    if "--run-integration" in sys.argv:
        return True
    for name in _INTEGRATION_ENV_VARS:
        if _env_truthy(name):
            return True
    return False


# ---------------------------------------------------------------------------
# Skip helper
# ---------------------------------------------------------------------------


def skip_unless_run_integration(
    reason: str = "integration tests are opt-in; pass --run-integration or set ARR_RUN_INTEGRATION=1",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Return a decorator that skips the test unless integration is requested.

    The returned decorator works under both :mod:`unittest` and
    :mod:`pytest`:

    * Under :mod:`unittest`, it uses :func:`unittest.skipUnless` with
      the result of :func:`integration_requested`, so the test is
      reported as ``skipped`` in the standard unittest summary.
    * Under :mod:`pytest`, the same decorator is recognised because
      :func:`unittest.skipUnless` produces a marker that pytest
      honours when it collects unittest-style classes.

    Parameters
    ----------
    reason:
        Message surfaced on the skip line. Defaults to a clear
        pointer at the opt-in flag and env var.

    Returns
    -------
    Callable
        A decorator that wraps the test with the skip behaviour.

    Examples
    --------
    Class-level (applies to every test in the class)::

        @skip_unless_run_integration()
        class TestLiveJellyfin(unittest.TestCase):
            ...

    Method-level (applies to a single test)::

        class TestSmoke(unittest.TestCase):
            @skip_unless_run_integration()
            def test_live_now(self) -> None:
                ...
    """
    return unittest.skipUnless(integration_requested(), reason)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


if __name__ == "__main__":  # pragma: no cover - manual smoke
    # ``python -m tests.integration.conftest`` is a one-line way to
    # check whether the current shell has opted in. Useful when an
    # operator wants to verify their env without launching the full
    # test suite.
    print(
        "integration_requested:",
        integration_requested(),
        "(argv has --run-integration:",
        "--run-integration" in sys.argv,
        ")",
    )
    sys.exit(0)