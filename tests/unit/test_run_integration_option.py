"""Regression guard for the ``--run-integration`` pytest opt-in flag.

The opt-in flag was documented in the README, in AGENTS.md and in the
``tests/integration/conftest.py`` docstring, but it was never registered
with pytest's own argument parser. Every documented invocation therefore
aborted with ``unrecognized arguments: --run-integration`` (pytest exit
code ``4``) while parsing the command line -- before a single test was
collected -- so the integration suite was unreachable through its
documented channel.

``pytestconfig.getoption`` raises ``ValueError`` when the option was never
registered, so this test fails loudly if the registration is removed or
moved into a conftest pytest does not treat as *initial*:
``pytest_addoption`` is silently ignored in a non-initial conftest, which
is exactly how the original defect stayed hidden.

The test is hermetic -- no network access, no live credentials, and the
option must be off in the default unit-test run.
"""

from __future__ import annotations

import os
import sys

import pytest


def _opt_in_requested(pytestconfig: pytest.Config) -> bool:
    """Return True iff this session asked for the integration opt-in.

    The flag can reach pytest through the command line, through
    ``PYTEST_ADDOPTS`` or through the ``addopts`` ini setting, and an
    in-process ``pytest.main([...])`` run keeps its own argv -- so all
    three channels are inspected.
    """
    candidates: list[str] = list(pytestconfig.invocation_params.args)
    candidates.extend(os.environ.get("PYTEST_ADDOPTS", "").split())
    candidates.extend(str(pytestconfig.getini("addopts")).split())
    candidates.extend(sys.argv)
    return "--run-integration" in candidates


def test_run_integration_option_is_registered(pytestconfig: pytest.Config) -> None:
    """``--run-integration`` is known to pytest and defaults to off."""
    value = pytestconfig.getoption("--run-integration")
    assert isinstance(value, bool), (
        "the --run-integration option must be registered with pytest's "
        "parser; getoption() raises ValueError when it was never registered"
    )
    if not _opt_in_requested(pytestconfig):
        assert value is False, (
            "--run-integration must default to off so the integration suite "
            "stays skipped without an explicit opt-in"
        )
