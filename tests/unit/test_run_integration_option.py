"""Regression guards for the ``--run-integration`` pytest opt-in flag.

The opt-in flag was documented in the README, in AGENTS.md and in the
``tests/integration/conftest.py`` docstring, but it was never registered
with pytest's own argument parser. Every documented invocation therefore
aborted with ``unrecognized arguments: --run-integration`` (pytest exit
code ``4``) while parsing the command line -- before a single test was
collected -- so the integration suite was unreachable through its
documented channel.

Three layers are guarded here:

1. *Registration* -- ``pytestconfig.getoption`` raises ``ValueError``
   when the option was never registered, so the first test fails loudly
   if the registration is removed or moved into a conftest pytest does
   not treat as *initial*: ``pytest_addoption`` is silently ignored in
   a non-initial conftest, which is exactly how the original defect
   stayed hidden.
2. *The command-line channel* -- a subprocess run of
   ``pytest tests/integration --run-integration`` must unmask the suite:
   the skip reason becomes the credential guard instead of the opt-in
   guard.
3. *The bridge* -- an in-process ``pytest.main([...])`` run never puts
   the flag in :data:`sys.argv`, so the only channel that can unmask the
   suite there is the ``pytest_configure`` hook that publishes the parsed
   option into ``ARR_RUN_INTEGRATION``. The guard fails if that bridge
   is removed.

Every test here is hermetic -- no network access, no live credentials.
The subprocesses run with the opt-in and the credential variables
removed from the environment, so the assertions prove that the *flag*
did the unmasking and that the integration test cannot reach the
network.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

#: Wall-clock safety net for the pytest subprocesses. A bare
#: ``pytest tests/integration`` run is sub-second; 60 s leaves headroom
#: for very cold hosts while still failing fast on a hung subprocess.
SUBPROCESS_TIMEOUT_SECONDS: int = 60

#: Skip reason surfaced by the credential guard in
#: ``tests/integration/test_smoke_integration.py``. Its presence proves
#: the opt-in guard was lifted: the test got past the opt-in check and
#: only stopped at the missing live endpoint.
CREDENTIAL_SKIP_REASON: str = "ARR_LIVE_URL must be set to run integration smoke test"

#: Skip reason surfaced by the opt-in guard. Its presence means the
#: opt-in was NOT lifted.
OPT_IN_SKIP_REASON: str = "integration tests are opt-in"


def _project_root() -> Path:
    """Return the absolute path to the repository root."""
    return Path(__file__).resolve().parents[2]


def _subprocess_env() -> dict[str, str]:
    """Return a sanitised env for the pytest subprocess.

    The env is built from scratch, so none of the opt-in variables
    (``ARR_RUN_INTEGRATION``, ``PYTEST_RUN_INTEGRATION``,
    ``PYTEST_ADDOPTS``) and none of the live-credential variables
    (``ARR_LIVE_URL``, ``ARR_LIVE_API_KEY``, ``ARR_LIVE_USER_ID``) leak
    in from the developer's shell. The assertions therefore prove that
    the *flag* did the unmasking, and the integration test stops at the
    credential guard instead of reaching the network.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        # The project root stays importable regardless of the
        # developer's shell.
        "PYTHONPATH": str(_project_root()),
        # Do not litter the tree with __pycache__ directories.
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return env


def _run_pytest(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``python -m pytest <argv>`` in a subprocess and return it.

    ``-p no:cacheprovider`` keeps the child session from writing a
    ``.pytest_cache`` directory into the repository.
    """
    try:
        return subprocess.run(
            [sys.executable, "-m", "pytest", *argv, "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            env=_subprocess_env(),
            cwd=str(_project_root()),
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - hang guard
        pytest.fail(
            f"pytest {argv} did not finish within {SUBPROCESS_TIMEOUT_SECONDS}s: {exc}"
        )


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


def test_flag_unmasks_the_integration_suite() -> None:
    """The command-line flag unmasks the opt-in integration suite.

    The subprocess runs with no opt-in variable in its environment, so
    the only thing that can lift the opt-in guard is the flag itself.
    """
    completed = _run_pytest(["tests/integration", "--run-integration"])
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, (
        f"pytest tests/integration --run-integration exited "
        f"{completed.returncode}:\n{output}"
    )
    assert CREDENTIAL_SKIP_REASON in output, (
        "the --run-integration flag did not unmask the integration suite; "
        f"expected the credential guard reason:\n{output}"
    )
    assert OPT_IN_SKIP_REASON not in output, (
        "the integration suite was still skipped by the opt-in guard even "
        f"though --run-integration was passed:\n{output}"
    )


def test_bridge_unmasks_the_integration_suite() -> None:
    """The parsed flag reaches the skip helper through the env-var bridge.

    ``pytest.main([...])`` keeps its own argv, so the flag never shows
    up in :data:`sys.argv` of the interpreter running it. The only
    channel that can unmask the suite in that case is the
    ``pytest_configure`` hook that publishes the parsed option into
    ``ARR_RUN_INTEGRATION`` -- so this test fails if that bridge is
    removed or renamed.
    """
    code = (
        "import pytest, sys; "
        "sys.exit(int(pytest.main(['tests/integration', '--run-integration', "
        "'-p', 'no:cacheprovider'])))"
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            env=_subprocess_env(),
            cwd=str(_project_root()),
        )
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - hang guard
        pytest.fail(
            "in-process pytest run did not finish within "
            f"{SUBPROCESS_TIMEOUT_SECONDS}s: {exc}"
        )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, (
        f"in-process pytest run exited {completed.returncode}:\n{output}"
    )
    assert CREDENTIAL_SKIP_REASON in output, (
        "the parsed --run-integration flag did not reach the skip helper; "
        "the env-var bridge is missing:\n" + output
    )
    assert OPT_IN_SKIP_REASON not in output, (
        "the integration suite was still skipped by the opt-in guard even "
        f"though the flag was parsed:\n{output}"
    )
