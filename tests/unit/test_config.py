"""Unit tests for :mod:`arr_cli.facade.config`.

Covers the contract spelled out in task 3.5 of tasks.md:

* ``load_config`` returns a :class:`ServiceConfig` with the parsed
  per-service :class:`AuthConfig` instances and the transport defaults.
* Missing file → :class:`ConfigError` (``exit_code=1``) naming the
  canonical path (REQ-1 AC2).
* Bad POSIX permissions → :class:`ConfigError` with permission
  guidance (security NFR).
* Env overrides win over file values (REQ-1 AC7).
* ``ftp://`` URL rejected; ``https://`` accepted.
* Malformed YAML anchor / malformed TOML → :class:`ConfigError`.

The tests are stdlib-only (``unittest`` + ``tomllib`` from the standard
library). YAML-specific assertions are skipped when ``yaml`` is not
installed so this file always runs; the YAML contract is exercised by
the orchestrator's static review pass and by future live-CI runs.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Make the project importable regardless of the test runner's CWD.
_PROJ_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from arr_cli.facade.config import (  # noqa: E402  - sys.path tweak above
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_RETRY,
    AuthConfig,
    ServiceConfig,
    load_config,
)
from arr_cli.facade.errors import ConfigError  # noqa: E402


def _yaml_available() -> bool:
    """Return True iff PyYAML can be imported.

    YAML-specific assertions skip when PyYAML is missing so this file
    always runs in the orchestrator's review phase; the YAML contract is
    covered by both the static review pass and the live-CI run when
    ``PyYAML`` is available.
    """
    try:
        import yaml  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Dataclass contract — directly exercises 3.1 without touching the filesystem.
# ---------------------------------------------------------------------------


class TestAuthConfigDataclass(unittest.TestCase):
    """``AuthConfig`` field defaults and immutability (REQ-1 AC4, REQ-9 AC1)."""

    def test_default_field_values(self) -> None:
        # The default ``api_key`` is ``None`` so operators can stage a
        # config before all credentials are available (REQ-2 AC6).
        cfg = AuthConfig(url="https://example.com")
        self.assertEqual(cfg.url, "https://example.com")
        self.assertIsNone(cfg.api_key)
        self.assertIsNone(cfg.user_id)
        self.assertFalse(cfg.auth_enabled)
        self.assertEqual(cfg.extra, {})

    def test_frozen_dataclass_rejects_mutation(self) -> None:
        # The ServiceConfig bundle relies on AuthConfig being immutable
        # so a single AuthConfig can be shared across command handlers.
        cfg = AuthConfig(url="https://example.com", api_key="x")
        with self.assertRaises(Exception):
            cfg.url = "https://other.example"  # type: ignore[misc]

    def test_field_equality(self) -> None:
        # Two AuthConfigs with the same field values must compare equal
        # so test assertions can use ``assertEqual`` cleanly.
        a = AuthConfig(url="https://example.com", api_key="x")
        b = AuthConfig(url="https://example.com", api_key="x")
        self.assertEqual(a, b)

    def test_extra_is_a_mapping(self) -> None:
        # Maintainerr auth-proxy headers live in ``extra`` (REQ-9 AC1).
        cfg = AuthConfig(
            url="https://example.com",
            auth_enabled=True,
            extra={"Authorization": "***"},
        )
        self.assertEqual(cfg.extra["Authorization"], "***")


class TestServiceConfigDataclass(unittest.TestCase):
    """``ServiceConfig`` field shape and defaults (REQ-5 AC3, NFR-Perf)."""

    def test_defaults(self) -> None:
        # All five service slots are optional so operators can stage a
        # config before every service is wired up (REQ-1 AC6).
        cfg = ServiceConfig(
            jellyfin=None,
            radarr=None,
            sonarr=None,
            maintainerr=None,
            seerr=None,
        )
        self.assertEqual(cfg.connect_timeout, DEFAULT_CONNECT_TIMEOUT)
        self.assertEqual(cfg.read_timeout, DEFAULT_READ_TIMEOUT)
        self.assertEqual(cfg.retry, DEFAULT_RETRY)
        self.assertIsNone(cfg.deadline)

    def test_service_slot_names(self) -> None:
        # Lock the slot names so the loader can rely on them
        # (the env-override path uses ``setattr`` indirectly).
        cfg = ServiceConfig(
            jellyfin=None,
            radarr=None,
            sonarr=None,
            maintainerr=None,
            seerr=None,
        )
        self.assertIn("jellyfin", cfg.__dataclass_fields__)
        self.assertIn("radarr", cfg.__dataclass_fields__)
        self.assertIn("sonarr", cfg.__dataclass_fields__)
        self.assertIn("maintainerr", cfg.__dataclass_fields__)
        self.assertIn("seerr", cfg.__dataclass_fields__)

    def test_frozen_dataclass_rejects_mutation(self) -> None:
        # Frozen-by-design so a loaded config never mutates under the
        # caller's feet (REQ NFR-Reliability).
        cfg = ServiceConfig(
            jellyfin=None,
            radarr=None,
            sonarr=None,
            maintainerr=None,
            seerr=None,
        )
        with self.assertRaises(Exception):
            cfg.connect_timeout = 9999.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_toml(path: Path, body: str) -> Path:
    """Write a TOML body to ``path`` and ensure it has mode 0600 on POSIX."""
    path.write_text(body, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    return path


def _write_yaml(path: Path, body: str) -> Path:
    """Write a YAML body to ``path`` and ensure it has mode 0600 on POSIX."""
    path.write_text(body, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    return path


# ---------------------------------------------------------------------------
# TOML round-trip (always available on Python 3.11+; exercise every branch).
# ---------------------------------------------------------------------------


class TestLoadConfigTOML(unittest.TestCase):
    """``load_config`` against tmp TOML files (REQ-1 AC3, AC4, AC7)."""

    def setUp(self) -> None:
        # Snapshot env so each test can mutate ``ARR_*`` without leaks.
        self._env_snapshot = dict(os.environ)
        # Clear all per-service + transport env vars to a known baseline.
        for var in (
            "ARR_CLI_CONFIG",
            "ARR_CONNECT_TIMEOUT",
            "ARR_READ_TIMEOUT",
            "ARR_RETRY",
            "ARR_DEADLINE",
        ):
            os.environ.pop(var, None)
        for service in ("JELLYFIN", "RADARR", "SONARR", "MAINTAINERR", "SEERR"):
            for suffix in ("URL", "API_KEY", "USER_ID"):
                os.environ.pop(f"ARR_{service}_{suffix}", None)

    def tearDown(self) -> None:
        # Restore the env so the next test starts clean.
        for var in list(os.environ):
            if var not in self._env_snapshot:
                os.environ.pop(var, None)
        for var, value in self._env_snapshot.items():
            os.environ[var] = value

    def test_round_trip_all_services(self) -> None:
        # Full 5-service schema: each section parses to AuthConfig.
        with tempfile_TOML(
            """
            [jellyfin]
            url = "https://jellyfin.example"
            api_key = "***"
            user_id = "<user-id>"

            [radarr]
            url = "https://radarr.example"
            api_key = "***"

            [sonarr]
            url = "https://sonarr.example"
            api_key = "***"

            [maintainerr]
            url = "https://maintainerr.example"
            auth_enabled = false

            [seerr]
            url = "https://seerr.example"
            api_key = "***"

            connect_timeout = 5.0
            read_timeout = 30.0
            retry = 0
            """
        ) as path:
            cfg = load_config(path)
            self.assertIsNotNone(cfg.jellyfin)
            self.assertIsNotNone(cfg.radarr)
            self.assertIsNotNone(cfg.sonarr)
            self.assertIsNotNone(cfg.maintainerr)
            self.assertIsNotNone(cfg.seerr)
            self.assertEqual(cfg.jellyfin.url, "https://jellyfin.example")
            self.assertEqual(cfg.jellyfin.user_id, "<user-id>")
            self.assertFalse(cfg.maintainerr.auth_enabled)
            self.assertEqual(cfg.connect_timeout, 5.0)
            self.assertEqual(cfg.read_timeout, 30.0)
            self.assertEqual(cfg.retry, 0)

    def test_missing_section_leaves_slot_none(self) -> None:
        # REQ-1 AC6: a service slot can be entirely absent; the loader
        # must not raise — only the CLI surface does, when the service
        # is actually invoked.
        with tempfile_TOML(
            """
            [jellyfin]
            url = "https://jellyfin.example"
            api_key = "***"
            """
        ) as path:
            cfg = load_config(path)
            self.assertIsNotNone(cfg.jellyfin)
            self.assertIsNone(cfg.radarr)
            self.assertIsNone(cfg.sonarr)
            self.assertIsNone(cfg.maintainerr)
            self.assertIsNone(cfg.seerr)

    def test_section_without_url_is_none(self) -> None:
        # A section with no ``url`` key is treated as absent (slot
        # becomes ``None``) so operators can stage a partial config.
        with tempfile_TOML(
            """
            [jellyfin]
            api_key = "***"
            """
        ) as path:
            cfg = load_config(path)
            self.assertIsNone(cfg.jellyfin)

    def test_env_overrides_url(self) -> None:
        # REQ-1 AC7: env var wins over file value.
        os.environ["ARR_JELLYFIN_URL"] = "https://env.example"
        with tempfile_TOML(
            """
            [jellyfin]
            url = "https://file.example"
            api_key = "***"
            """
        ) as path:
            cfg = load_config(path)
            self.assertEqual(cfg.jellyfin.url, "https://env.example")

    def test_env_overrides_api_key(self) -> None:
        os.environ["ARR_JELLYFIN_API_KEY"] = "env-key"
        with tempfile_TOML(
            """
            [jellyfin]
            url = "https://file.example"
            api_key = "file-key"
            """
        ) as path:
            cfg = load_config(path)
            self.assertEqual(cfg.jellyfin.api_key, "env-key")

    def test_env_overrides_user_id(self) -> None:
        os.environ["ARR_JELLYFIN_USER_ID"] = "env-user"
        with tempfile_TOML(
            """
            [jellyfin]
            url = "https://file.example"
            api_key = "x"
            user_id = "file-user"
            """
        ) as path:
            cfg = load_config(path)
            self.assertEqual(cfg.jellyfin.user_id, "env-user")

    def test_env_overrides_transport_defaults(self) -> None:
        os.environ["ARR_CONNECT_TIMEOUT"] = "1.5"
        os.environ["ARR_READ_TIMEOUT"] = "9.0"
        os.environ["ARR_RETRY"] = "3"
        os.environ["ARR_DEADLINE"] = "12.5"
        with tempfile_TOML(
            """
            [jellyfin]
            url = "https://example.com"
            api_key = "x"
            connect_timeout = 5.0
            read_timeout = 30.0
            """
        ) as path:
            cfg = load_config(path)
            self.assertEqual(cfg.connect_timeout, 1.5)
            self.assertEqual(cfg.read_timeout, 9.0)
            self.assertEqual(cfg.retry, 3)
            self.assertEqual(cfg.deadline, 12.5)

    def test_env_overrides_disabled(self) -> None:
        # ``env_overrides=False`` returns the file content alone (used
        # by tests and tools that want to inspect the file verbatim).
        os.environ["ARR_JELLYFIN_URL"] = "https://env.example"
        with tempfile_TOML(
            """
            [jellyfin]
            url = "https://file.example"
            api_key = "x"
            """
        ) as path:
            cfg = load_config(path, env_overrides=False)
            self.assertEqual(cfg.jellyfin.url, "https://file.example")


# ---------------------------------------------------------------------------
# YAML round-trip (skipped when PyYAML is unavailable).
# ---------------------------------------------------------------------------


@unittest.skipUnless(_yaml_available(), "PyYAML not installed")
class TestLoadConfigYAML(unittest.TestCase):
    """``load_config`` against tmp YAML files (REQ-1 AC3)."""

    def setUp(self) -> None:
        self._env_snapshot = dict(os.environ)
        for var in ("ARR_CLI_CONFIG",):
            os.environ.pop(var, None)

    def tearDown(self) -> None:
        for var in list(os.environ):
            if var not in self._env_snapshot:
                os.environ.pop(var, None)
        for var, value in self._env_snapshot.items():
            os.environ[var] = value

    def test_yaml_round_trip(self) -> None:
        with tempfile_YAML(
            """
            jellyfin:
              url: https://jellyfin.example
              api_key: "***"
              user_id: <user-id>
            radarr:
              url: https://radarr.example
              api_key: "***"
            """
        ) as path:
            cfg = load_config(path)
            self.assertEqual(cfg.jellyfin.url, "https://jellyfin.example")
            self.assertEqual(cfg.jellyfin.user_id, "<user-id>")
            self.assertEqual(cfg.jellyfin.api_key, "***")

    def test_yaml_root_must_be_mapping(self) -> None:
        # YAML anchors / array roots are rejected per design.
        with tempfile_YAML(
            """
            - just
            - a
            - list
            """
        ) as path:
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertEqual(ctx.exception.exit_code, 1)
            self.assertIn("mapping", ctx.exception.message)

    def test_yaml_anchor_on_non_dict_root(self) -> None:
        # Specifically test that an anchored scalar/list root fails
        # closed — not just bare lists.
        with tempfile_YAML(
            """
            - &anchor first
            - second
            """
        ) as path:
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertEqual(ctx.exception.exit_code, 1)


# ---------------------------------------------------------------------------
# File-existence and permission checks (3.2).
# ---------------------------------------------------------------------------


class TestLoadConfigMissingFile(unittest.TestCase):
    """Missing-file behaviour (REQ-1 AC2)."""

    def test_missing_file_raises_config_error(self) -> None:
        # The canonical path is named in the message so the operator
        # can copy from ``arr.conf.example`` to the right location.
        bogus = Path("/tmp/arr-cli-nonexistent-xyzzy-12345.conf")
        if bogus.exists():  # paranoia for re-using /tmp
            bogus.unlink()
        with self.assertRaises(ConfigError) as ctx:
            load_config(bogus)
        self.assertEqual(ctx.exception.exit_code, 1)
        self.assertIn(str(bogus), ctx.exception.message)

    def test_default_path_resolved_via_ARR_CLI_CONFIG(self) -> None:
        # ``ARR_CLI_CONFIG`` overrides the default canonical path.
        with tempfile_TOML(
            """
            [jellyfin]
            url = "https://example.com"
            api_key = "x"
            """
        ) as path:
            os.environ["ARR_CLI_CONFIG"] = str(path)
            try:
                cfg = load_config()  # no explicit path
                self.assertEqual(cfg.jellyfin.url, "https://example.com")
            finally:
                os.environ.pop("ARR_CLI_CONFIG", None)


class TestLoadConfigPermissions(unittest.TestCase):
    """POSIX permission check (security NFR)."""

    @unittest.skipUnless(os.name == "posix", "POSIX-only")
    def test_world_readable_rejected(self) -> None:
        with tempfile_TOML(
            """
            [jellyfin]
            url = "https://example.com"
            api_key = "x"
            """
        ) as path:
            os.chmod(path, 0o644)  # group + world readable
            try:
                with self.assertRaises(ConfigError) as ctx:
                    load_config(path)
                self.assertEqual(ctx.exception.exit_code, 1)
                self.assertIn("insecure permissions", ctx.exception.message)
            finally:
                os.chmod(path, 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX-only")
    def test_group_readable_rejected(self) -> None:
        with tempfile_TOML(
            """
            [jellyfin]
            url = "https://example.com"
            api_key = "x"
            """
        ) as path:
            os.chmod(path, 0o640)  # group readable
            try:
                with self.assertRaises(ConfigError) as ctx:
                    load_config(path)
                self.assertEqual(ctx.exception.exit_code, 1)
            finally:
                os.chmod(path, 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX-only")
    def test_secure_mode_accepted(self) -> None:
        # 0600 (and 0700, since the loader only forbids group/world)
        # must parse cleanly.
        with tempfile_TOML(
            """
            [jellyfin]
            url = "https://example.com"
            api_key = "x"
            """
        ) as path:
            os.chmod(path, 0o600)
            cfg = load_config(path)
            self.assertEqual(cfg.jellyfin.url, "https://example.com")


# ---------------------------------------------------------------------------
# URL validation and structural rejection (3.4).
# ---------------------------------------------------------------------------


class TestURLValidation(unittest.TestCase):
    """URL scheme / host validation (REQ's security NFR)."""

    def test_https_accepted(self) -> None:
        with tempfile_TOML(
            """
            [jellyfin]
            url = "https://example.com"
            api_key = "x"
            """
        ) as path:
            cfg = load_config(path)
            self.assertEqual(cfg.jellyfin.url, "https://example.com")

    def test_http_accepted(self) -> None:
        with tempfile_TOML(
            """
            [jellyfin]
            url = "http://example.com"
            api_key = "x"
            """
        ) as path:
            cfg = load_config(path)
            self.assertEqual(cfg.jellyfin.url, "http://example.com")

    def test_ftp_rejected(self) -> None:
        with tempfile_TOML(
            """
            [jellyfin]
            url = "ftp://example.com"
            api_key = "x"
            """
        ) as path:
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertEqual(ctx.exception.exit_code, 1)
            self.assertIn("http(s)", ctx.exception.message)

    def test_empty_url_rejected(self) -> None:
        with tempfile_TOML(
            """
            [jellyfin]
            url = ""
            api_key = "x"
            """
        ) as path:
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertEqual(ctx.exception.exit_code, 1)

    def test_non_string_url_rejected(self) -> None:
        # TOML/HTTP coercion does not apply — a number is not a URL.
        with tempfile_TOML(
            """
            [jellyfin]
            url = 12345
            api_key = "x"
            """
        ) as path:
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertEqual(ctx.exception.exit_code, 1)
            self.assertIn("must be a string", ctx.exception.message)

    def test_url_with_empty_host_rejected(self) -> None:
        # ``scheme://path-only`` parses with no hostname.
        with tempfile_TOML(
            """
            [jellyfin]
            url = "https:///no-host"
            api_key = "x"
            """
        ) as path:
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertEqual(ctx.exception.exit_code, 1)


class TestStructuralValidation(unittest.TestCase):
    """Type / shape validation per service section."""

    def test_section_must_be_mapping(self) -> None:
        # A scalar / list / number where a mapping is expected → fail
        # closed with ConfigError.
        with tempfile_TOML(
            """
            jellyfin = "not-a-mapping"
            """
        ) as path:
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertEqual(ctx.exception.exit_code, 1)
            self.assertIn("mapping", ctx.exception.message)

    def test_auth_enabled_must_be_bool(self) -> None:
        with tempfile_TOML(
            """
            [maintainerr]
            url = "https://example.com"
            auth_enabled = "true"
            """
        ) as path:
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertEqual(ctx.exception.exit_code, 1)
            self.assertIn("auth_enabled", ctx.exception.message)

    def test_malformed_toml_rejected(self) -> None:
        # Write an actually-broken TOML file by hand (tomllib is strict
        # and surfaces the syntax error).
        tmp = Path("/tmp/arr-cli-malformed-12345.toml")
        try:
            tmp.write_text("this is not = valid toml [[[", encoding="utf-8")
            if os.name == "posix":
                os.chmod(tmp, 0o600)
            with self.assertRaises(ConfigError) as ctx:
                load_config(tmp)
            self.assertEqual(ctx.exception.exit_code, 1)
            self.assertIn("parse", ctx.exception.message)
        finally:
            tmp.unlink(missing_ok=True)

    def test_extra_must_be_mapping(self) -> None:
        with tempfile_TOML(
            """
            [maintainerr]
            url = "https://example.com"
            auth_enabled = true
            extra = "not-a-mapping"
            """
        ) as path:
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertEqual(ctx.exception.exit_code, 1)
            self.assertIn("extra", ctx.exception.message)


# ---------------------------------------------------------------------------
# Helpers (moved below the test classes to keep the file readable top-down).
# ---------------------------------------------------------------------------


import contextlib  # noqa: E402  (placed near the temp-file helpers)


@contextlib.contextmanager
def tempfile_TOML(body: str):
    """Yield a Path to a temporary .toml file with 0600 perms on POSIX."""
    import tempfile as _tempfile

    fd, raw_path = _tempfile.mkstemp(suffix=".toml")
    os.close(fd)
    path = Path(raw_path)
    path.write_text(body, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


@contextlib.contextmanager
def tempfile_YAML(body: str):
    """Yield a Path to a temporary .yaml file with 0600 perms on POSIX."""
    import tempfile as _tempfile

    fd, raw_path = _tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    path = Path(raw_path)
    path.write_text(body, encoding="utf-8")
    if os.name == "posix":
        os.chmod(path, 0o600)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
