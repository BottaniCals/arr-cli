"""Hermetic regression tests for the ``--version`` flag.

The flag is the operator-facing entry point into the package's release
identity, so the contract locked here spans four surfaces:

1. **Every entry point** -- ``jellyfin``, ``radarr``, ``sonarr``,
   ``maintainerr`` and ``seerr`` must accept ``--version``, print
   ``"<service> <version>"`` on stdout and exit ``0``. The version
   token must be byte-identical across all five.
2. **No config, no network** -- the flag short-circuits while argparse
   is parsing, so it works on a host with no ``arr.conf`` and never
   opens a socket.
3. **One definition site** -- the version literal must occur exactly
   once across ``arr_cli/`` and ``pyproject.toml``, and the module
   holding it must stay import-free so setuptools can resolve it
   statically at build time.
4. **Abbreviation ambiguity** -- registering ``--version`` next to
   ``--verbose`` narrows argparse's prefix-abbreviation surface:
   ``--v`` / ``--ver`` are rejected as ambiguous, the full long forms
   stay accepted.

The subprocess tests follow the pattern of
``tests/unit/test_argparse_universal_flags.py`` (scrubbed env,
``PYTHONPATH`` at the project root) and point ``HOME`` at an empty
temporary directory so no assertion can depend on a real
``~/.config/arr/arr.conf``. No network access, no live HTTP.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch

# Make the project importable regardless of the test runner's CWD.
_PROJ_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

import arr_cli  # noqa: E402  - sys.path tweak above
from arr_cli.facade.config import (  # noqa: E402
    AuthConfig,
    ServiceConfig,
)
from arr_cli.facade import transport  # noqa: E402


# ---------------------------------------------------------------------------
# Documented surfaces
# ---------------------------------------------------------------------------

#: Every CLI shipped by arr-cli, each paired with a representative
#: subcommand so both invocation forms (``<cli> --version`` and
#: ``<cli> <subcommand> --version``) are exercised.
SERVICES: tuple[tuple[str, str], ...] = (
    ("jellyfin", "now"),
    ("radarr", "wanted"),
    ("sonarr", "wanted"),
    ("maintainerr", "health"),
    ("seerr", "request-count"),
)

#: Wall-clock budget for every subprocess invocation. argparse and the
#: facade import are CPU-fast; 15 s mirrors the safety net used by
#: ``tests/unit/test_argparse_universal_flags.py``.
SUBPROCESS_TIMEOUT_SECONDS: int = 15

#: Path that does not exist on disk. Pointing ``--config`` here routes
#: the post-parse flow through ``ConfigError`` (exit code ``1``) with
#: the ``service=config op=load`` line -- the marker that separates
#: "the flag was accepted" from "argparse rejected the flag" (which
#: surfaces as ``service=config op=parse``).
MISSING_CONFIG_PATH: str = "/tmp/does-not-exist-arr-cli-version-test.conf"

#: The single file allowed to hold the version literal.
VERSION_DEFINITION_SITE: Path = Path("arr_cli") / "__init__.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _subprocess_env(home: str) -> dict[str, str]:
    """Return a clean env for the subprocess so imports work.

    ``home`` is the ``HOME`` the child process sees; the tests point
    it at an empty temporary directory so the canonical config path
    (``~/.config/arr/arr.conf``) cannot resolve.
    """
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": home,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PYTHONPATH": str(_PROJ_ROOT),
    }


@contextlib.contextmanager
def _empty_home() -> Iterator[str]:
    """Yield the path of an empty temporary ``HOME`` directory."""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


def _run_cli(
    cli: str,
    argv: list[str],
    home: str,
) -> subprocess.CompletedProcess[str]:
    """Run ``<cli> <argv>`` out-of-process and return the result.

    The invocation pattern matches
    ``tests/unit/test_argparse_universal_flags.py``: a one-liner that
    imports the per-service module's ``main`` and calls it with an
    explicit argv list. Wrapping the call in ``sys.exit(main(...))``
    surfaces the documented exit code as the subprocess return code.
    """
    argv_literal = ", ".join(repr(a) for a in argv)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                f"import sys; from arr_cli.{cli} import main; "
                f"sys.exit(main([{argv_literal}]))"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        env=_subprocess_env(home),
        cwd=str(_PROJ_ROOT),
    )


def _structured_parse_line(stderr: str) -> str | None:
    """Return the structured stderr line of a rejected invocation."""
    for line in stderr.splitlines():
        if line.startswith("service=config op=parse message="):
            return line
    return None


def _service_config(**kwargs: object) -> ServiceConfig:
    """Build a :class:`ServiceConfig` carrying only the Jellyfin auth."""
    jellyfin = AuthConfig(
        url="https://jellyfin.example.test",
        ak="test-token-1234",
        user_id=None,
        auth_enabled=False,
        extra={},
    )
    return ServiceConfig(
        jellyfin=jellyfin,
        radarr=None,
        sonarr=None,
        maintainerr=None,
        seerr=None,
        connect_timeout=5.0,
        read_timeout=30.0,
        retry=0,
        deadline=None,
    )


def _version_literal_hits() -> list[Path]:
    """Return every file under the scan scope that holds the literal.

    The scan pattern is derived from :data:`arr_cli.__version__` at
    runtime (regex-escaped), so the check keeps working after the next
    version bump. The scope is ``arr_cli/`` (Python sources only) plus
    ``pyproject.toml`` -- the ``"0.1.0"`` string in
    ``tests/unit/test_perf_budgets.py`` is fixture data outside the
    scope.
    """
    pattern = re.compile(re.escape(arr_cli.__version__))
    hits: list[Path] = []

    for path in sorted((_PROJ_ROOT / "arr_cli").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            hits.append(path.relative_to(_PROJ_ROOT))

    pyproject = _PROJ_ROOT / "pyproject.toml"
    if pattern.search(pyproject.read_text(encoding="utf-8")):
        hits.append(pyproject.relative_to(_PROJ_ROOT))

    return hits


# ---------------------------------------------------------------------------
# US-1 / US-3: the flag on every entry point
# ---------------------------------------------------------------------------


class TestVersionFlagPerService(unittest.TestCase):
    """``<cli> --version`` prints the version line and exits 0."""

    def test_version_flag_prints_version_line_and_exits_zero(self) -> None:
        with _empty_home() as home:
            for cli, _ in SERVICES:
                with self.subTest(cli=cli):
                    completed = _run_cli(cli, ["--version"], home)
                    self.assertEqual(
                        completed.returncode,
                        0,
                        (
                            f"{cli} --version exited {completed.returncode}; "
                            f"stderr={completed.stderr!r}"
                        ),
                    )
                    self.assertEqual(
                        completed.stdout,
                        f"{cli} {arr_cli.__version__}\n",
                        (
                            f"{cli} --version printed an unexpected line; "
                            f"stdout={completed.stdout!r}"
                        ),
                    )

    def test_version_flag_needs_no_config(self) -> None:
        # ``HOME`` points at an empty directory, so the canonical config
        # path cannot resolve. A config load would surface as the
        # ``service=config op=load`` line -- it must not appear.
        with _empty_home() as home:
            for cli, _ in SERVICES:
                with self.subTest(cli=cli):
                    completed = _run_cli(cli, ["--version"], home)
                    self.assertNotIn(
                        "service=config op=load",
                        completed.stderr,
                        (
                            f"{cli} --version must not load a config; "
                            f"stderr={completed.stderr!r}"
                        ),
                    )

    def test_version_token_is_byte_identical_across_services(self) -> None:
        tokens: list[str] = []
        with _empty_home() as home:
            for cli, _ in SERVICES:
                completed = _run_cli(cli, ["--version"], home)
                self.assertEqual(completed.returncode, 0)
                tokens.append(completed.stdout.rstrip("\n").split(" ", 1)[1])
        self.assertTrue(tokens)
        self.assertEqual(
            len(set(tokens)),
            1,
            f"the five executables reported different version tokens: {tokens!r}",
        )
        self.assertEqual(tokens[0], arr_cli.__version__)

    def test_subcommand_version_flag_prints_the_same_line(self) -> None:
        with _empty_home() as home:
            for cli, sub in SERVICES:
                with self.subTest(cli=cli, sub=sub):
                    completed = _run_cli(cli, [sub, "--version"], home)
                    self.assertEqual(
                        completed.returncode,
                        0,
                        (
                            f"{cli} {sub} --version exited "
                            f"{completed.returncode}; "
                            f"stderr={completed.stderr!r}"
                        ),
                    )
                    self.assertEqual(
                        completed.stdout,
                        f"{cli} {arr_cli.__version__}\n",
                        (
                            f"{cli} {sub} --version printed an unexpected "
                            f"line; stdout={completed.stdout!r}"
                        ),
                    )

    def test_version_flag_combined_with_other_flags(self) -> None:
        with _empty_home() as home:
            completed = _run_cli(
                "jellyfin",
                ["--human", "--debug", "--config", MISSING_CONFIG_PATH, "--version"],
                home,
            )
            self.assertEqual(
                completed.returncode,
                0,
                f"stderr={completed.stderr!r}",
            )
            self.assertEqual(
                completed.stdout,
                f"jellyfin {arr_cli.__version__}\n",
                f"stdout={completed.stdout!r}",
            )


# ---------------------------------------------------------------------------
# US-3 / US-6: the flag short-circuits before config and transport
# ---------------------------------------------------------------------------


class TestVersionFlagShortCircuits(unittest.TestCase):
    """``--version`` never loads a config and never opens a socket."""

    def _run_in_process(self, cli: str, argv: list[str]) -> tuple[int, str, str]:
        module = importlib.import_module(f"arr_cli.{cli}")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch(
            "arr_cli.facade.cli_common.load_config",
            side_effect=AssertionError("load_config must not run for --version"),
        ) as loader, patch(
            "arr_cli.facade.transport._ensure_session",
            side_effect=AssertionError("no HTTP request may run for --version"),
        ) as session:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = module.main(argv)
        loader.assert_not_called()
        session.assert_not_called()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_no_config_load_and_no_http_request(self) -> None:
        for cli, _ in SERVICES:
            with self.subTest(cli=cli):
                code, stdout, stderr = self._run_in_process(cli, ["--version"])
                self.assertEqual(code, 0, f"stderr={stderr!r}")
                self.assertEqual(
                    stdout,
                    f"{cli} {arr_cli.__version__}\n",
                    f"stdout={stdout!r}",
                )

    def test_no_config_load_and_no_http_request_on_subcommand(self) -> None:
        for cli, sub in SERVICES:
            with self.subTest(cli=cli, sub=sub):
                code, stdout, stderr = self._run_in_process(cli, [sub, "--version"])
                self.assertEqual(code, 0, f"stderr={stderr!r}")
                self.assertEqual(
                    stdout,
                    f"{cli} {arr_cli.__version__}\n",
                    f"stdout={stdout!r}",
                )


# ---------------------------------------------------------------------------
# US-4: the on-the-wire identity carries the reported version
# ---------------------------------------------------------------------------


class TestOutboundIdentity(unittest.TestCase):
    """The envelope version and the User-Agent follow the constant."""

    def test_mediabrowser_envelope_and_user_agent(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.content = b"[]"
        session = MagicMock()
        session.get.return_value = response
        session.close.return_value = None

        with patch(
            "arr_cli.facade.transport._ensure_session",
            return_value=session,
        ):
            transport.get("jellyfin", "/Items", cfg=_service_config())

        headers = session.get.call_args.kwargs["headers"]
        self.assertEqual(
            headers["User-Agent"],
            f"arr-cli/{arr_cli.__version__}",
            f"headers={headers!r}",
        )
        self.assertIn(
            f'Version="{arr_cli.__version__}"',
            headers["Authorization"],
            f"headers={headers!r}",
        )


# ---------------------------------------------------------------------------
# US-2: one definition site
# ---------------------------------------------------------------------------


class TestSingleDefinitionSite(unittest.TestCase):
    """The version literal lives in exactly one place."""

    def test_version_literal_occurs_once(self) -> None:
        hits = _version_literal_hits()
        self.assertEqual(
            [hit.as_posix() for hit in hits],
            [VERSION_DEFINITION_SITE.as_posix()],
            (
                "the version literal must exist in exactly one place "
                f"(arr_cli/__init__.py); found {hits!r}"
            ),
        )

    def test_definition_site_is_import_free(self) -> None:
        # setuptools resolves the ``attr:`` directive statically via AST;
        # an import in this module would force a build-time import of
        # the runtime dependencies, which the build environment does not
        # carry.
        source = (_PROJ_ROOT / VERSION_DEFINITION_SITE).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        self.assertEqual(
            imports,
            [],
            (
                "arr_cli/__init__.py must stay free of imports so setuptools "
                f"resolves __version__ statically; found {imports!r}"
            ),
        )


# ---------------------------------------------------------------------------
# US-1: the abbreviation-ambiguity contract
# ---------------------------------------------------------------------------


class TestAbbreviationAmbiguity(unittest.TestCase):
    """``--v`` / ``--ver`` are ambiguous; the full long forms are not."""

    def test_ambiguous_abbreviations_are_rejected(self) -> None:
        with _empty_home() as home:
            for cli, _ in SERVICES:
                for abbreviation in ("--v", "--ver"):
                    with self.subTest(cli=cli, abbreviation=abbreviation):
                        completed = _run_cli(cli, [abbreviation], home)
                        self.assertEqual(
                            completed.returncode,
                            1,
                            (
                                f"{cli} {abbreviation} exited "
                                f"{completed.returncode} (expected 1); "
                                f"stderr={completed.stderr!r}"
                            ),
                        )
                        self.assertIsNotNone(
                            _structured_parse_line(completed.stderr),
                            (
                                f"{cli} {abbreviation}: expected "
                                f"service=config op=parse message=...; "
                                f"stderr={completed.stderr!r}"
                            ),
                        )

    def test_full_long_forms_stay_accepted(self) -> None:
        with _empty_home() as home:
            for cli, sub in SERVICES:
                with self.subTest(cli=cli, sub=sub):
                    completed = _run_cli(
                        cli,
                        [sub, "--verbose", "--config", MISSING_CONFIG_PATH],
                        home,
                    )
                    # The flag parsed (the flow reached the config load),
                    # so the structured line is the ``op=load`` one and
                    # not the ``op=parse`` ambiguity error.
                    self.assertIsNone(
                        _structured_parse_line(completed.stderr),
                        (
                            f"{cli} {sub} --verbose must not be rejected as "
                            f"ambiguous; stderr={completed.stderr!r}"
                        ),
                    )
                    self.assertIn(
                        "service=config op=load",
                        completed.stderr,
                        (
                            f"{cli} {sub} --verbose: expected the "
                            f"ConfigError load line; "
                            f"stderr={completed.stderr!r}"
                        ),
                    )

                    version_completed = _run_cli(cli, [sub, "--version"], home)
                    self.assertEqual(
                        version_completed.returncode,
                        0,
                        (
                            f"{cli} {sub} --version exited "
                            f"{version_completed.returncode}; "
                            f"stderr={version_completed.stderr!r}"
                        ),
                    )


if __name__ == "__main__":
    unittest.main()
