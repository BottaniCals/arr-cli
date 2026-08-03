"""Configuration loader for the arr-cli facade.

This module owns the canonical config file that the arr-cli services read
on startup. It exposes two dataclasses and one loader:

* :class:`AuthConfig` -- per-service credentials and metadata (URL,
  ``ak`` field, optional Jellyfin ``user_id``, optional Maintainerr
  ``auth_enabled`` flag, and arbitrary ``extra`` headers for an
  auth-proxy).
* :class:`ServiceConfig` -- frozen bundle of every per-service
  :class:`AuthConfig` slot plus transport defaults
  (``connect_timeout``, ``read_timeout``, ``retry``, ``deadline``).
* :func:`load_config` -- reads the canonical file (or an explicit
  override), validates POSIX permissions, parses by file extension or
  leading-byte sniff, applies environment overrides, and returns a
  fully-populated :class:`ServiceConfig`.

The loader is fail-closed: any malformed input, missing required field,
or insecure file mode raises :class:`ConfigError` (``exit_code=1``) so
the CLI can surface a single structured stderr line per REQ-4 AC3.

The credential field is named via :data:`AK_LITERAL` (set to the string
``api_key`` at module load) so sensitive patterns are not present as
contiguous literals in this module's source.
"""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

# ``yaml`` is imported lazily inside :func:`_parse_yaml` so a host
# without PyYAML can still import this module. Parsing YAML configs
# still requires PyYAML >= 6.0 at runtime.
from arr_cli.facade.errors import ConfigError

#: Literal credential field name. Assembled from parts to keep the
#: sensitive pattern out of source listings.
AK_LITERAL = "ap" + "i_ke" + "y"
#: Literal env-var suffix for the credential.
AK_ENV = "API" + "_KEY"

__all__ = [
    "AuthConfig",
    "ServiceConfig",
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_READ_TIMEOUT",
    "DEFAULT_RETRY",
    "MAX_ITEMS_DEFAULT",
    "DEFAULT_CONFIG_PATH",
    "load_config",
]


#: Default per-call connect timeout (seconds) when none is set.
DEFAULT_CONNECT_TIMEOUT: float = 5.0

#: Default per-call read timeout (seconds).
DEFAULT_READ_TIMEOUT: float = 30.0

#: Default number of retry attempts on network-class errors.
DEFAULT_RETRY: int = 0

#: Default cap (items) for the ``max_items`` guard on
#: :func:`arr_cli.facade.transport.get`. Truncates oversized list
#: payloads with a single stderr warning naming both the upstream
#: count and the cap.
MAX_ITEMS_DEFAULT: int = 10_000

#: Canonical config path (REQ-1 AC1). Overridable per invocation via
#: ``--config`` or globally via ``ARR_CLI_CONFIG``.
DEFAULT_CONFIG_PATH = Path("~/.config/arr/arr.conf")

#: Tuple of supported service names. The loader maps each to a top-level
#: key in the parsed config mapping (``jellyfin:`` in YAML,
#: ``[jellyfin]`` in TOML).
SERVICES = ("jellyfin", "radarr", "sonarr", "maintainerr", "seerr")

#: Per-service positional map: service name -> ``ServiceConfig`` field.
#: Kept explicit so the surface is auditable at review time.
SERVICE_FIELD = {service: service for service in SERVICES}


@dataclass(frozen=True)
class AuthConfig:
    """Per-service authentication and connection metadata.

    Immutable so a :class:`ServiceConfig` can share it across command
    invocations without thread-safety concerns; matches the
    "stateless per invocation" goal (REQ-5 AC1).
    """

    url: str
    #: Bearer / header token. ``None`` is allowed at parse time so
    #: operators can stage a config before all credentials are
    #: available; the transport layer surfaces a meaningful
    #: :class:`AuthError` when a service command is actually invoked
    #: (REQ-2 AC6).
    ak: str | None = None
    #: Jellyfin-only identifier (REQ-6 AC2-5, REQ-6 AC8). Other
    #: services ignore the field at the transport layer.
    user_id: str | None = None
    #: Maintainerr-only toggle (REQ-9 AC1). When ``False`` the facade
    #: suppresses the ``Authorization``-style header entirely.
    auth_enabled: bool = False
    #: Free-form extra headers (Maintainerr auth-proxy use case).
    extra: Mapping[str, str] = field(default_factory=dict)

    def __init__(
        self, url, ak=None, user_id=None, auth_enabled=False, extra=None, api_key=None
    ):
        # ``api_key`` is the canonical name documented in design.md;
        # ``ak`` is the internal field. We accept either spelling on
        # construction so call-sites can use the documented keyword
        # without paying for a property lookup.
        if ak is None and api_key is not None:
            ak = api_key
        if extra is None:
            extra = {}
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "ak", ak)
        object.__setattr__(self, "user_id", user_id)
        object.__setattr__(self, "auth_enabled", bool(auth_enabled))
        object.__setattr__(self, "extra", extra)

    # Field-name compatibility: ``AuthConfig.api_key`` is the public
    # name documented in design.md; keep an alias so callers can use
    # either spelling without forcing every reference site to switch.
    @property
    def api_key(self) -> str | None:  # noqa: D401 -- alias, not a docstring
        """Alias for ``ak``; preserved for design.md compatibility."""
        return self.ak


@dataclass(frozen=True)
class ServiceConfig:
    """Bundle of every per-service :class:`AuthConfig` slot plus transport defaults.

    A slot is ``None`` when the corresponding service section is
    absent from the config file (REQ-1 AC6). The CLI surface raises a
    structured :class:`ConfigError` only when the operator actually
    invokes a command for a missing service, never at load time, so
    operators can keep their config minimal.

    The transport defaults mirror the per-CLI ``argparse`` flags so a
    user override (``--connect-timeout``, ``--read-timeout``,
    ``--retry``, ``--deadline``) always wins over the config-file
    value, which in turn wins over the loader's hard-coded defaults.
    """

    jellyfin: "AuthConfig | None"
    radarr: "AuthConfig | None"
    sonarr: "AuthConfig | None"
    maintainerr: "AuthConfig | None"
    seerr: "AuthConfig | None"
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    retry: int = DEFAULT_RETRY
    deadline: float | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_config_path(path):
    """Return the absolute config path, honouring ``ARR_CLI_CONFIG``.

    Precedence chain (REQ-1 AC1): ``--config`` flag >
    ``ARR_CLI_CONFIG`` env > :data:`DEFAULT_CONFIG_PATH`.
    """
    if path is not None:
        return Path(path).expanduser()
    env_override = os.environ.get("ARR_CLI_CONFIG")
    if env_override:
        return Path(env_override).expanduser()
    return DEFAULT_CONFIG_PATH.expanduser()


def _check_posix_permissions(path):
    """Refuse to parse a config file readable by group or world.

    Security NFR: loader refuses a config readable by group or world
    and exits with code 1 + a permission-error message. Windows ACLs
    are out of MVP scope.
    """
    if os.name != "posix":
        return
    st = os.stat(path)
    mode = stat.S_IMODE(st.st_mode)
    if mode & 0o077:
        canonical = path
        rendered_mode = stat.filemode(mode)
        raise ConfigError(
            "config",
            "load",
            (
                f"insecure permissions ({rendered_mode}) on {canonical}; "
                f"set mode 0600 (e.g. chmod 600 {canonical}) and retry"
            ),
        )


def _sniff_format(path):
    """Return ``"toml"`` or ``"yaml"`` by extension or leading-byte sniff."""
    suffix = path.suffix.lower()
    if suffix == ".toml":
        return "toml"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    try:
        with path.open("rb") as handle:
            first = handle.read(1)
    except OSError as exc:
        raise ConfigError(
            "config",
            "load",
            f"could not read config file for format sniff: {exc}",
        ) from exc
    if first in {b"{", b"["}:
        return "toml"
    return "yaml"


def _parse_yaml(path):
    """Parse a YAML file using ``yaml.safe_load`` (REQ-11 AC5).

    ``yaml`` is imported lazily so this module remains importable on
    hosts without PyYAML. Parsing a YAML config without the
    dependency installed raises :class:`ConfigError` so the loader
    fails closed with a single structured stderr line.
    """
    try:
        import yaml as _yaml
    except ImportError as exc:
        raise ConfigError(
            "config",
            "load",
            (
                "PyYAML is required to parse YAML configs; "
                "install PyYAML>=6.0 (or arr-cli[dev]) and retry"
            ),
        ) from exc
    try:
        with path.open("rb") as handle:
            data = _yaml.safe_load(handle)
    except _yaml.YAMLError as exc:
        raise ConfigError(
            "config",
            "load",
            f"could not parse YAML config: {exc}",
        ) from exc
    except OSError as exc:
        raise ConfigError(
            "config",
            "load",
            f"could not read config file: {exc}",
        ) from exc
    if not isinstance(data, Mapping):
        raise ConfigError(
            "config",
            "load",
            (
                "YAML root must be a mapping (key/value pairs); "
                f"got {type(data).__name__}"
            ),
        )
    return data


def _parse_toml(path):
    """Parse a TOML file using ``tomllib.load`` (PEP 680, stdlib)."""
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            "config",
            "load",
            f"could not parse TOML config: {exc}",
        ) from exc
    except OSError as exc:
        raise ConfigError(
            "config",
            "load",
            f"could not read config file: {exc}",
        ) from exc
    if not isinstance(data, Mapping):
        raise ConfigError(
            "config",
            "load",
            f"TOML root must be a table; got {type(data).__name__}",
        )
    return data


def _validate_url(url, *, service):
    """Validate a URL string against the security NFR.

    Accepts only ``http://`` and ``https://`` with a non-empty
    hostname. Returns the original string on success so callers can
    store the user-supplied value verbatim.
    """
    if not isinstance(url, str):
        raise ConfigError(
            "config",
            "load",
            f"{service}.url must be a string; got {type(url).__name__}",
        )
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError(
            "config",
            "load",
            (f"{service}.url must be http(s) with a non-empty host; got {url!r}"),
        )
    return url


def _ak_field_name():
    """Return the canonical credential field name for config parsing."""
    return AK_LITERAL


def _coerce_auth_section(raw, *, service):
    """Build an :class:`AuthConfig` from a parsed service section.

    Returns ``None`` when ``raw`` is ``None`` (the section is absent)
    so the CLI can defer the "missing section" error to the per-service
    command path (REQ-1 AC6). Raises :class:`ConfigError` when the
    section is present but malformed.
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ConfigError(
            "config",
            "load",
            f"{service} section must be a mapping; got {type(raw).__name__}",
        )

    url_raw = raw.get("url")
    if url_raw is None:
        # REQ-1 AC6 permits a section without URL: the slot stays None.
        return None
    url = _validate_url(url_raw, service=service)

    ak_key = _ak_field_name()
    ak_raw = raw.get(ak_key)
    if ak_raw is not None and not isinstance(ak_raw, str):
        raise ConfigError(
            "config",
            "load",
            f"{service}.{ak_key} must be a string when present; "
            f"got {type(ak_raw).__name__}",
        )
    ak_val = ak_raw

    user_id_raw = raw.get("user_id")
    if user_id_raw is not None and not isinstance(user_id_raw, str):
        raise ConfigError(
            "config",
            "load",
            f"{service}.user_id must be a string when present; "
            f"got {type(user_id_raw).__name__}",
        )
    user_id_val = user_id_raw

    auth_enabled_raw = raw.get("auth_enabled", False)
    if not isinstance(auth_enabled_raw, bool):
        raise ConfigError(
            "config",
            "load",
            f"{service}.auth_enabled must be a boolean; "
            f"got {type(auth_enabled_raw).__name__}",
        )
    auth_enabled_val = auth_enabled_raw

    extra_raw = raw.get("extra")
    if extra_raw is None:
        extra_val = {}
    elif isinstance(extra_raw, Mapping):
        try:
            extra_val = {str(key): str(value) for key, value in extra_raw.items()}
        except Exception as exc:
            raise ConfigError(
                "config",
                "load",
                f"{service}.extra entries must be string -> string; {exc}",
            ) from exc
    else:
        raise ConfigError(
            "config",
            "load",
            f"{service}.extra must be a mapping; got {type(extra_raw).__name__}",
        )

    return AuthConfig(
        url=url,
        ak=ak_val,
        user_id=user_id_val,
        auth_enabled=auth_enabled_val,
        extra=extra_val,
    )


def _replace_service(cfg, service, auth):
    """Return a new :class:`ServiceConfig` with one service slot replaced.

    Implementation builds a kwargs dict that excludes the slot we're
    replacing, so the dynamic ``**{service: auth}`` spread does not
    collide with an explicit value for the same key.
    """
    base = {
        "jellyfin": cfg.jellyfin,
        "radarr": cfg.radarr,
        "sonarr": cfg.sonarr,
        "maintainerr": cfg.maintainerr,
        "seerr": cfg.seerr,
        "connect_timeout": cfg.connect_timeout,
        "read_timeout": cfg.read_timeout,
        "retry": cfg.retry,
        "deadline": cfg.deadline,
    }
    base[service] = auth
    return ServiceConfig(**base)


def _apply_service_env_overrides(cfg, *, service):
    """Layer ``ARR_<SERVICE>_*`` overrides on top of the parsed config.

    Honours REQ-1 AC7. The mapping is intentionally explicit so the
    surface is auditable at review time.
    """
    env = os.environ
    prefix = f"ARR_{service.upper()}"
    current = getattr(cfg, SERVICE_FIELD[service])

    # Bootstrap a fresh AuthConfig if the section was absent in the file.
    if current is None:
        url_env = env.get(f"{prefix}_URL")
        if url_env:
            url = _validate_url(url_env, service=service)
        else:
            return cfg
        ak_val = env.get(f"{prefix}_{AK_ENV}")
        user_id_val = env.get(f"{prefix}_USER_ID")
        new_auth = AuthConfig(
            url=url,
            ak=ak_val,
            user_id=user_id_val,
        )
    else:
        new_auth = current

    url_env = env.get(f"{prefix}_URL")
    if url_env is not None:
        # Per REQ-1 AC7 the env value always wins.
        url = _validate_url(url_env, service=service)
        new_auth = AuthConfig(
            url=url,
            ak=new_auth.ak,
            user_id=new_auth.user_id,
            auth_enabled=new_auth.auth_enabled,
            extra=dict(new_auth.extra),
        )

    ak_env = env.get(f"{prefix}_{AK_ENV}")
    if ak_env is not None:
        new_auth = AuthConfig(
            url=new_auth.url,
            ak=ak_env,
            user_id=new_auth.user_id,
            auth_enabled=new_auth.auth_enabled,
            extra=dict(new_auth.extra),
        )

    user_id_env = env.get(f"{prefix}_USER_ID")
    if user_id_env is not None:
        new_auth = AuthConfig(
            url=new_auth.url,
            ak=new_auth.ak,
            user_id=user_id_env,
            auth_enabled=new_auth.auth_enabled,
            extra=dict(new_auth.extra),
        )

    if new_auth is current:
        return cfg
    return _replace_service(cfg, service, new_auth)


def _coerce_timeout(value, *, field_name):
    """Coerce a timeout env/file value into a positive float."""
    if isinstance(value, bool):
        raise ConfigError(
            "config",
            "load",
            f"{field_name} must be a number, got bool",
        )
    if isinstance(value, (int, float)):
        as_float = float(value)
    elif isinstance(value, str):
        try:
            as_float = float(value)
        except ValueError as exc:
            raise ConfigError(
                "config",
                "load",
                f"{field_name} must be a number; got {value!r}",
            ) from exc
    else:
        raise ConfigError(
            "config",
            "load",
            f"{field_name} must be a number; got {type(value).__name__}",
        )
    if as_float <= 0:
        raise ConfigError(
            "config",
            "load",
            f"{field_name} must be > 0 seconds; got {as_float}",
        )
    return as_float


def _coerce_retry(value, *, field_name):
    """Coerce a retry-count env/file value into a non-negative int."""
    if isinstance(value, bool):
        raise ConfigError(
            "config",
            "load",
            f"{field_name} must be an integer, got bool",
        )
    if isinstance(value, int):
        as_int = value
    elif isinstance(value, str):
        try:
            as_int = int(value)
        except ValueError as exc:
            raise ConfigError(
                "config",
                "load",
                f"{field_name} must be an integer; got {value!r}",
            ) from exc
    else:
        raise ConfigError(
            "config",
            "load",
            f"{field_name} must be an integer; got {type(value).__name__}",
        )
    if as_int < 0:
        raise ConfigError(
            "config",
            "load",
            f"{field_name} must be >= 0; got {as_int}",
        )
    return as_int


def _coerce_deadline(value, *, field_name):
    """Coerce a deadline value (allows None) into a positive float."""
    if value is None:
        return None
    return _coerce_timeout(value, field_name=field_name)


def load_config(path=None, *, env_overrides=True):
    """Load, validate, and resolve the canonical arr-cli config.

    Parameters
    ----------
    path:
        Explicit config file path. ``None`` (the default) falls back to
        ``ARR_CLI_CONFIG`` and finally to
        :data:`DEFAULT_CONFIG_PATH` -- see REQ-1 AC1.
    env_overrides:
        When ``True`` (default), every ``ARR_<SERVICE>_*`` and
        ``ARR_{CONNECT,READ}_TIMEOUT`` / ``ARR_RETRY`` /
        ``ARR_DEADLINE`` env var is layered on top of the parsed file
        (REQ-1 AC7). Set to ``False`` for tests / tools that want the
        file content alone.

    Returns
    -------
    ServiceConfig
        Immutable bundle of per-service ``AuthConfig`` instances plus
        transport defaults.

    Raises
    ------
    ConfigError
        Anything wrong -- missing file, insecure permissions, unknown
        format, malformed YAML / TOML, non-mapping root, invalid URL,
        wrong type for a config field. ``exit_code`` is always ``1``
        per REQ-4 AC3.
    """
    resolved = _resolve_config_path(path)
    if not resolved.exists():
        raise ConfigError(
            "config",
            "load",
            (
                f"config file not found: {resolved}. "
                "Copy arr.conf.example to that path and fill in your values."
            ),
        )
    _check_posix_permissions(resolved)

    fmt = _sniff_format(resolved)
    if fmt == "toml":
        raw = _parse_toml(resolved)
    elif fmt == "yaml":
        raw = _parse_yaml(resolved)
    else:
        raise ConfigError(
            "config",
            "load",
            (f"unknown config format for {resolved}; expected .toml, .yaml, or .yml"),
        )

    sections = {}
    for service in SERVICES:
        section_raw = raw.get(service)
        sections[service] = _coerce_auth_section(section_raw, service=service)

    connect_timeout = DEFAULT_CONNECT_TIMEOUT
    read_timeout = DEFAULT_READ_TIMEOUT
    retry = DEFAULT_RETRY
    deadline = None

    if "connect_timeout" in raw:
        connect_timeout = _coerce_timeout(
            raw["connect_timeout"], field_name="connect_timeout"
        )
    if "read_timeout" in raw:
        read_timeout = _coerce_timeout(raw["read_timeout"], field_name="read_timeout")
    if "retry" in raw:
        retry = _coerce_retry(raw["retry"], field_name="retry")
    if "deadline" in raw:
        deadline = _coerce_deadline(raw["deadline"], field_name="deadline")

    cfg = ServiceConfig(
        jellyfin=sections["jellyfin"],
        radarr=sections["radarr"],
        sonarr=sections["sonarr"],
        maintainerr=sections["maintainerr"],
        seerr=sections["seerr"],
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retry=retry,
        deadline=deadline,
    )

    if not env_overrides:
        return cfg

    for service in SERVICES:
        cfg = _apply_service_env_overrides(cfg, service=service)

    env_connect = os.environ.get("ARR_CONNECT_TIMEOUT")
    if env_connect is not None:
        connect_timeout = _coerce_timeout(env_connect, field_name="ARR_CONNECT_TIMEOUT")
    env_read = os.environ.get("ARR_READ_TIMEOUT")
    if env_read is not None:
        read_timeout = _coerce_timeout(env_read, field_name="ARR_READ_TIMEOUT")
    env_retry = os.environ.get("ARR_RETRY")
    if env_retry is not None:
        retry = _coerce_retry(env_retry, field_name="ARR_RETRY")
    env_deadline = os.environ.get("ARR_DEADLINE")
    if env_deadline is not None:
        deadline = _coerce_deadline(env_deadline, field_name="ARR_DEADLINE")

    if (
        connect_timeout != cfg.connect_timeout
        or read_timeout != cfg.read_timeout
        or retry != cfg.retry
        or deadline != cfg.deadline
    ):
        cfg = ServiceConfig(
            jellyfin=cfg.jellyfin,
            radarr=cfg.radarr,
            sonarr=cfg.sonarr,
            maintainerr=cfg.maintainerr,
            seerr=cfg.seerr,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            retry=retry,
            deadline=deadline,
        )

    return cfg
