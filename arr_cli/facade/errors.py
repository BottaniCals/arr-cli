"""Exception hierarchy and exit-code map for the arr-cli facade.

Every error raised from the facade or any of the five service modules is
an instance of :class:`ArrError` (or one of its subclasses). The hierarchy
mirrors the canonical exit-code map documented in REQ-4:

* exit code 1 — :class:`ConfigError` (config missing, bad perms, bad
  format, malformed date input, missing required service section).
* exit code 2 — :class:`AuthError` (HTTP 401/403 from a service, or a
  required credential is absent from the loaded config).
* exit code 3 — :class:`NetworkError` (DNS failure, connection refused,
  TLS error, timeout, or any other ``requests``-level transport failure).
* exit code 4 — :class:`HttpError` (HTTP 4xx/5xx responses that are not
  401/403 and therefore not auth errors).
* exit code 5 — :class:`ParseError` (the body returned by the service
  was not valid JSON).

Each error carries a structured ``service=... op=... status=... message=...``
line that the CLI surfaces to stderr (REQ-4 AC3). The format mirrors
``key=value`` tokens so downstream log aggregators can parse it without
needing to regex strings.

Per REQ-4 AC5, the CLI never prints a Python traceback unless ``--debug``
is passed; this module only builds the structured message, the
traceback policy lives in :mod:`arr_cli.facade.cli_common`.
"""

from __future__ import annotations

__all__ = [
    "ArrError",
    "ConfigError",
    "AuthError",
    "NetworkError",
    "HttpError",
    "ParseError",
]


class ArrError(Exception):
    """Base exception for every arr-cli facade/service error.

    The base class is intentionally concrete: a bare :class:`ArrError`
    carries an exit code of ``1`` so ad-hoc raises degrade gracefully,
    but concrete error sites should use the most specific subclass so
    that the CLI's exit code and stderr message stay aligned with the
    documented map.

    Parameters
    ----------
    service:
        Lowercase service identifier (``"jellyfin"``, ``"radarr"``,
        ``"sonarr"``, ``"maintainerr"``, ``"seerr"``) or a non-service
        tag such as ``"config"`` for the loader. Used verbatim in the
        structured stderr line.
    op:
        Short operation name describing what the code was doing when
        the error happened, e.g. ``"load"``, ``"calendar"``, ``"item"``.
        Used verbatim in the structured stderr line.
    message:
        Human-readable detail string. The message is included verbatim
        in the structured stderr line and must not contain the literal
        ``status=`` token (the latter is reserved for HTTP status).
    exit_code:
        Process exit code to surface when the CLI catches this error.
        Subclasses default the value so callers normally don't need to
        pass it; it is exposed so test code can build ad-hoc errors
        with custom codes.
    """

    exit_code: int = 1

    def __init__(
        self,
        service: str,
        op: str,
        message: str,
        *,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.service = service
        self.op = op
        self.message = message
        if exit_code is not None:
            # Subclass default already set; only override when the caller
            # explicitly opts in (e.g. tests).
            self.exit_code = exit_code

    def __str__(self) -> str:
        """Return the structured ``service=... op=... status=... message=...`` line.

        Subclasses that carry an HTTP status (or equivalent) override
        :meth:`_format_status` to inject the ``status=`` token; the base
        class simply omits it so the line never carries an empty
        ``status=`` field.
        """
        parts: list[str] = [
            f"service={self.service}",
            f"op={self.op}",
        ]
        status = self._format_status()
        if status is not None:
            parts.append(status)
        parts.append(f"message={self.message}")
        return " ".join(parts)

    def _format_status(self) -> str | None:
        """Return the ``status=...`` token to embed, or ``None`` to omit it.

        Subclasses override this to surface HTTP status, byte offset, or
        other dimensional data. The base implementation returns ``None``
        so the default line never carries an empty ``status=`` field.
        """
        return None


class ConfigError(ArrError):
    """Configuration error: missing file, bad perms, bad format, bad input.

    Maps to exit code ``1`` (REQ-4, REQ-1 AC2, REQ-1 AC3, REQ-1 AC6).
    """

    exit_code = 1


class AuthError(ArrError):
    """Authentication/authorization error: HTTP 401/403 or missing credential.

    Maps to exit code ``2`` (REQ-2 AC5, REQ-2 AC6, REQ-4 AC1).
    """

    exit_code = 2


class NetworkError(ArrError):
    """Network-level failure: DNS, connection refused, TLS, timeout.

    Maps to exit code ``3`` (REQ-4 AC1). The original URL that was
    attempted is carried in :attr:`url` so the structured stderr line
    can include both the URL and the underlying exception class name.
    """

    exit_code = 3

    def __init__(
        self,
        service: str,
        op: str,
        message: str,
        *,
        url: str,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(service, op, message, exit_code=exit_code)
        self.url = url

    def _format_status(self) -> str | None:
        # Surface the URL as a status-line equivalent so operators can
        # grep "status=" + the URL out of stderr without re-parsing the
        # free-form message. "status=" is the canonical token across the
        # hierarchy; the value is a URL, not an HTTP code, so callers
        # that want to distinguish can switch on the exception class.
        return f"status={self.url}"


class HttpError(ArrError):
    """HTTP 4xx/5xx response that is not an auth error.

    Maps to exit code ``4`` (REQ-4 AC2). The HTTP status is carried in
    :attr:`status` and emitted as ``status=<code>`` in the structured
    stderr line.
    """

    exit_code = 4

    def __init__(
        self,
        service: str,
        op: str,
        message: str,
        *,
        status: int,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(service, op, message, exit_code=exit_code)
        self.status = status

    def _format_status(self) -> str | None:
        return f"status={self.status}"


class ParseError(ArrError):
    """The response body could not be decoded as JSON.

    Maps to exit code ``5`` (REQ-4 AC6). The :attr:`byte_offset` of the
    first non-JSON byte is emitted as ``status=byte_offset=...`` to keep
    the structured-line schema consistent with the rest of the hierarchy
    while still carrying the offset.
    """

    exit_code = 5

    def __init__(
        self,
        service: str,
        op: str,
        message: str,
        *,
        byte_offset: int,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(service, op, message, exit_code=exit_code)
        self.byte_offset = byte_offset

    def _format_status(self) -> str | None:
        # We re-use the "status=" prefix for consistency with the rest
        # of the hierarchy but qualify it with "byte_offset=" so a
        # parser can disambiguate from HTTP status codes.
        return f"status=byte_offset={self.byte_offset}"
