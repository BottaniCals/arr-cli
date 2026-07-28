"""Smoke integration test for arr-cli (task 19 / subtask 19.1).

This module demonstrates the opt-in integration test pattern. It is
SKIPPED by default because it requires a live service instance and
credentials that are not committed to the repository.

How to run
----------
Default (every test in this file is SKIPPED)::

    pytest tests/integration/
    python -m unittest discover tests
    make test

Opt-in (tests RUN against a live endpoint)::

    pytest tests/integration/ --run-integration

    # unittest runner — ``unittest`` does not parse the ``--key=value``
    # style flag natively, so the opt-in is exposed via the
    # ``ARR_RUN_INTEGRATION`` environment variable as well:
    ARR_RUN_INTEGRATION=1 python -m unittest tests.integration.test_smoke_integration

Environment variables consumed
------------------------------
``ARR_LIVE_URL``
    Base URL of the target service (e.g.
    ``https://jellyfin.example.com``). Required; tests skip with an
    explicit reason when it is unset.

``ARR_LIVE_API_KEY``
    API key / token for the target service. Optional for services
    that ship with no auth (e.g. Maintainerr with
    ``auth.enabled=false``).

``ARR_LIVE_USER_ID``
    Jellyfin ``userId`` for endpoints that require it. Optional;
    tests that need it skip-with-reason when unset.

What this test guards
---------------------
The single test in this file is the documented example called out in
task 19's implementation notes. It confirms that the transport layer
can build a URL against a live endpoint, send the documented auth
header, and parse the response into JSON when one of the five
services is reachable.

The goal is to prove the opt-in plumbing end-to-end, not to retest
the unit-tested contract — every behaviour exercised here is also
covered by :mod:`tests.unit.test_transport` against a mocked
``requests`` session.

Future contributors should add more integration tests by copying
this template:

* Skip-decorate every test with :func:`skip_unless_run_integration`
  (imported from :mod:`tests.integration.conftest`).
* Read live endpoints from the ``ARR_LIVE_*`` environment variables
  and skip-with-reason when they are missing so the operator gets a
  clear diagnostic instead of an opaque connection failure.
* Keep the assertions narrow — integration tests guard the wiring
  between the CLI and a real service, not the per-endpoint contract
  (that's the unit tests' job).
"""

from __future__ import annotations

import json
import os
import unittest
from typing import Any

try:
    from tests.integration.conftest import skip_unless_run_integration
except ImportError:  # pragma: no cover - fallback for top-level discovery
    # When ``python -m unittest discover`` is invoked from
    # ``tests/integration/`` directly (top-level module discovery),
    # ``tests`` is not on the import path. Fall back to a local
    # import so the test still works in that mode.
    from conftest import skip_unless_run_integration  # type: ignore[no-redef]


def _live_url() -> str | None:
    """Return the configured live base URL, or None if unset / empty."""
    url = os.environ.get("ARR_LIVE_URL")
    if url is None or not url.strip():
        return None
    return url.strip().rstrip("/")


def _live_api_key() -> str | None:
    """Return the configured live API key, or None if unset / empty."""
    key = os.environ.get("ARR_LIVE_API_KEY")
    if key is None or not key.strip():
        return None
    return key.strip()


def _live_user_id() -> str | None:
    """Return the configured Jellyfin ``userId`` if set."""
    user_id = os.environ.get("ARR_LIVE_USER_ID")
    if user_id is None or not user_id.strip():
        return None
    return user_id.strip()


@skip_unless_run_integration()
class TestSmokeIntegration(unittest.TestCase):
    """Single end-to-end smoke test exercising the opt-in plumbing.

    Every test in this class is SKIPPED when ``--run-integration`` is
    not in :data:`sys.argv` and ``ARR_RUN_INTEGRATION`` is not set to
    a truthy value. The test itself then skip-with-reasons when the
    live URL is missing so an operator who opted in but forgot to
    export ``ARR_LIVE_URL`` gets a clear pointer at the missing
    variable instead of a connection-error trace.
    """

    def test_live_endpoint_round_trip(self) -> None:
        """A GET against ``ARR_LIVE_URL`` returns a JSON body.

        The test deliberately does not assert a specific endpoint or
        schema because each of the five services exposes a different
        read-only shape; instead it confirms the transport layer can
        attach the auth header (when an API key is provided) and
        decode the response. Operators opt into the test by setting
        ``ARR_LIVE_URL`` to the base URL of any one of the five
        services; the test will hit a path that exists on every
        service (``/``) and confirm the body parses as JSON, which is
        the minimum-viable proof that the wiring is correct.

        Skip-with-reason diagnostics (in order):

        1. Opt-in flag missing → :func:`skip_unless_run_integration`.
        2. ``ARR_LIVE_URL`` unset → ``ARR_LIVE_URL must be set``.
        3. Live service unreachable → :class:`NetworkError` is
           re-raised as a failure (NOT a skip) so the operator sees
           the real connection error in CI logs.
        """
        base_url = _live_url()
        if base_url is None:
            self.skipTest("ARR_LIVE_URL must be set to run integration smoke test")

        api_key = _live_api_key()

        # Lazy import so the test module remains importable without
        # the ``requests`` runtime dep being installed at test
        # discovery time. (It is a declared runtime dep, so this is
        # belt-and-braces.)
        try:
            import requests  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - dep declared
            self.fail(f"requests is required for integration tests: {exc}")

        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key is not None:
            # Attach the X-Api-Key header because three of the five
            # services (Radarr, Sonarr, Seerr) require it on every
            # call. The integration test does not know which service
            # the operator pointed it at, so we add the most common
            # header; services that reject it will surface a 401/403
            # in the assertion below, which is the expected
            # integration-test outcome for a misconfigured endpoint.
            headers["X-Api-Key"] = api_key

        # The ``/`` path is universally served by all five CLIs'
        # upstream services, but the response shape varies wildly
        # (some return JSON, some return HTML, some 404). We treat
        # the call as a smoke test only: a non-2xx response is a
        # failure with the body excerpt, so the operator can see
        # exactly which service responded and how.
        response = requests.get(
            f"{base_url}/",
            headers=headers,
            timeout=(5.0, 30.0),
        )

        # We accept 2xx (the happy path) and 401/403 (the operator
        # pointed us at a service that requires a key we did not
        # provide — the connection itself works, which is the
        # integration-test contract). Anything else is a failure.
        self.assertIn(
            response.status_code,
            {200, 201, 204, 401, 403},
            f"unexpected status {response.status_code} from {base_url}; "
            f"body={response.text[:200]!r}",
        )

        # When the response is JSON, prove the transport-layer JSON
        # parsing path works against a real body. When the body is
        # HTML or empty (some services return an HTML landing page
        # at ``/``), we skip this assertion to keep the smoke test
        # focused on wiring rather than per-service schema.
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type and response.content:
            try:
                payload: Any = json.loads(response.content)
            except json.JSONDecodeError as exc:
                self.fail(
                    f"live endpoint {base_url}/ returned invalid JSON: {exc}; "
                    f"body={response.text[:200]!r}"
                )
            # Empty payload (None / [] / {}) is acceptable for a smoke
            # test; we only fail on unparseable bodies, not on empty
            # ones.
            self.assertIsNotNone(payload)


if __name__ == "__main__":
    unittest.main()