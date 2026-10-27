"""Project-level pytest configuration.

This file lives at the repository root (the pytest ``rootdir``) on
purpose: ``pytest_addoption`` is only honoured in *initial* conftest
files, and the root directory is an ancestor of every test path, so the
``--run-integration`` opt-in flag is accepted for every documented
invocation form -- ``pytest tests/integration --run-integration``,
``pytest tests/ --run-integration`` and the no-argument ``testpaths``
form alike.

The parsed flag is published into the ``ARR_RUN_INTEGRATION`` environment
variable that the integration skip helper already honours, so the flag
genuinely unmasks the suite even when it is absent from ``sys.argv``
(in-process ``pytest.main([...])`` runs).

The option must be registered in exactly one place: a second
``pytest_addoption`` for the same option name aborts the session with
``ValueError: option names {'--run-integration'} already added``.
"""

from __future__ import annotations

import os

import pytest

#: Environment variable the integration skip helper already honours.
RUN_INTEGRATION_ENV_VAR = "ARR_RUN_INTEGRATION"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the ``--run-integration`` opt-in flag with pytest."""
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="run the opt-in integration suite (tests/integration/)",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Surface the parsed opt-in flag through the documented env var."""
    if config.getoption("--run-integration"):
        os.environ[RUN_INTEGRATION_ENV_VAR] = "1"
