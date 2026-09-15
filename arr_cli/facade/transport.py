"""HTTP transport for the arr-cli facade.

This module owns the single HTTP entry point used by every service
CLI: :func:`get`. It is responsible for:

* Resolving the base URL for a service from a :class:`ServiceConfig`.
* Injecting the right auth header for each service
  (``Authorization: MediaBrowser ***`` envelope for Jellyfin,
  ``X-Api-Key`` for Radarr / Sonarr / Seerr, optional for
  Maintainerr).
* Percent-encoding every query parameter before it reaches
  :mod:`requests`.
* Applying per-call ``timeout=(connect, read)`` tuples.
* Translating raw :mod:`requests` exceptions and non-2xx HTTP
  responses into the :class:`ArrError` hierarchy defined in
  :mod:`arr_cli.facade.errors` so callers see a single, structured
  error surface.

The mapping mirrors the documented exit-code map:

* connection / DNS / TLS / timeout → :class:`NetworkError` (exit 3)
* HTTP 401 / 403 → :class:`AuthError` (exit 2)
* HTTP 4xx / 5xx (non-auth) → :class:`HttpError` (exit 4)
* JSON decode failure → :class:`ParseError` (exit 5)
* HTTP 2xx → returned payload (list, dict, or scalar)

The debug-mode redactor (:func:`_redact_debug_record`) lives in this
module too so the redaction policy stays next to the request/response
shape it operates on. Secrets are never logged even with ``--debug``
per the security NFR.

Per REQ-5 AC1, a fresh :class:`requests.Session` is created on every
call so the process releases sockets cleanly when the CLI exits; no
state survives across invocations.
"""

from __future__ import annotations

import json
import logging
import socket
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urljoin

from arr_cli.facade.config import (
    AK_LITERAL,
    AuthConfig,
    ServiceConfig,
    MAX_ITEMS_DEFAULT,
)
from arr_cli.facade.errors import (
    AuthError,
    HttpError,
    NetworkError,
    ParseError,
)
from arr_cli.facade.retry import with_retry

__all__ = [
    "get",
    "_inject_auth",
    "_redact_debug_record",
    "encode_path_segment",
    "_HEADER_NAMES",
    "_BODY_EXCERPT_LIMIT",
]


# Module-level logger so debug records surface through the standard
# ``logging`` configuration without a private handler.
_logger = logging.getLogger("arr_cli.facade.transport")
# WARNING+ emissions from this module must reach the operator's stderr
# verbatim so the documented large-payload truncation warning is
# captured by ``redirect_stderr`` (and any parent log handler attached
# by a host test runner). Disabling propagation routes the record
# through Python's ``lastResort`` handler (a ``_StderrHandler`` that
# always resolves the current ``sys.stderr``) instead of letting a
# parent handler swallow it.
_logger.propagate = False

#: Canonical auth header names (REQ-2 AC1-3). Centralised so the
#: redaction policy and any future header-name validation stay in sync.
#: ``jellyfin`` has no single header name: the full
#: ``Authorization: MediaBrowser ***`` envelope is constructed
#: inside :func:`_inject_auth` (Jellyfin 12.x deprecated the bare
#: ``X-Emby-Token`` header).
_HEADER_NAMES: dict[str, str] = {
    "jellyfin": "",
    "radarr": "X-Api-Key",
    "sonarr": "X-Api-Key",
    "seerr": "X-Api-Key",
    "maintainerr": "",  # maintainerr: optional via auth.enabled + extra
}

#: Identity embedded in the Jellyfin ``MediaBrowser`` authorization
#: envelope. Jellyfin uses these values to label the device in its
#: dashboard, so they must be stable across calls and informative for
#: operators triaging a session.
_MEDIABROWSER_CLIENT = "arr-cli"
_MEDIABROWSER_DEVICE = "arr-cli"
_MEDIABROWSER_VERSION = "0.1.0"


def _mediabrowser_authorization(token: str) -> str:
    """Return the Jellyfin ``Authorization`` envelope value.

    Jellyfin 12.x deprecated the standalone ``X-Emby-Token`` header
    and now requires the full ``MediaBrowser ***`` envelope.
    Empirically the bare ``X-Emby-Token`` returns a 401 byte-identical
    to a request with no auth header at all; only the full envelope
    authenticates. ``DeviceId`` is anchored to the host's hostname so
    the Jellyfin dashboard groups this CLI's activity under one stable
    device.
    """
    device_id = f"{_MEDIABROWSER_CLIENT}-{socket.gethostname()}"
    return (
        f'MediaBrowser Client="{_MEDIABROWSER_CLIENT}", '
        f'Device="{_MEDIABROWSER_DEVICE}", '
        f'DeviceId="{device_id}", '
        f'Version="{_MEDIABROWSER_VERSION}", '
        f'Token="{token}"'
    )


#: Truncation limit for body excerpts in :class:`HttpError` messages
#: (REQ-4 AC2: 500 chars). Defined here so tests and the formatter
#: agree on the boundary.
_BODY_EXCERPT_LIMIT = 500


# ---------------------------------------------------------------------------
# Auth injection
# ---------------------------------------------------------------------------


def _inject_auth(
    headers: dict[str, str],
    service: str,
    auth: AuthConfig | None,
) -> None:
    """Inject the per-service auth header into ``headers`` in place.

    Behaviour (REQ-2 AC1-4, REQ-2 AC6):

    * ``jellyfin``   -- ``Authorization: MediaBrowser ***``
      envelope including the api_key. Raises :class:`AuthError`
      when ``api_key`` is missing. The standalone ``X-Emby-Token``
      header is no longer emitted: Jellyfin 12.x ignores it.
    * ``radarr``     -- ``X-Api-Key: <api_key>``. Raises
      :class:`AuthError` when ``api_key`` is missing.
    * ``sonarr``     -- ``X-Api-Key: <api_key>``. Raises
      :class:`AuthError` when ``api_key`` is missing.
    * ``seerr``      -- ``X-Api-Key: <api_key>``. Raises
      :class:`AuthError` when ``api_key`` is missing.
    * ``maintainerr`` -- when ``auth_enabled`` is true, every entry in
      ``auth.extra`` is added verbatim. When false (the documented
      default, REQ-9 AC1) the call is left without an
      Authorization-style header; missing credentials are NOT an error
      because Maintainerr ships with no auth.
    """
    if auth is None:
        raise AuthError(
            service,
            "auth",
            (
                f"{service}: api_key missing — set {service}.{AK_LITERAL} "
                "in arr.conf"
            ),
        )

    if service == "maintainerr":
        if auth.auth_enabled:
            for key, value in auth.extra.items():
                headers[key] = value
        # No header added when auth is disabled — documented default.
        return

    if service == "jellyfin":
        # Jellyfin 12.x dropped support for the standalone
        # ``X-Emby-Token`` header. Empirically the bare header returns a
        # 401 byte-identical to a request with no auth header; only the
        # full ``Authorization: MediaBrowser ***`` envelope
        # authenticates.
        if auth.ak is None:
            raise AuthError(
                service,
                "auth",
                (
                    f"{service}: {AK_LITERAL} missing — set "
                    f"{service}.{AK_LITERAL} in arr.conf"
                ),
            )
        headers["Authorization"] = _mediabrowser_authorization(auth.ak)
        return

    header_name = _HEADER_NAMES.get(service)
    if header_name is None:
        # Defensive: this branch is only reached when the caller passes
        # an unknown service name, which the type-checker / dispatch
        # table should prevent at the call site.
        raise AuthError(
            service,
            "auth",
            f"unknown service {service!r}; cannot inject auth header",
        )

    if auth.ak is None:
        raise AuthError(
            service,
            "auth",
            (
                f"{service}: {AK_LITERAL} missing — set "
                f"{service}.{AK_LITERAL} in arr.conf"
            ),
        )
    headers[header_name] = auth.ak


# ---------------------------------------------------------------------------
# Public transport
# ---------------------------------------------------------------------------


def _resolve_auth(cfg: ServiceConfig, service: str) -> AuthConfig:
    """Return the :class:`AuthConfig` for ``service`` or raise :class:`AuthError`.

    Centralises the "missing service section" check so every code path
    surfaces a uniform message (REQ-1 AC6, REQ-2 AC6).
    """
    auth = getattr(cfg, service)
    if auth is None:
        raise AuthError(
            service,
            "auth",
            (
                f"{service}: section missing in arr.conf — add a "
                f"{service}: block with url and {AK_LITERAL}"
            ),
        )
    return auth


def _encode_params(params: Mapping[str, Any] | None) -> dict[str, str] | None:
    """Percent-encode every key and value in ``params``.

    Per the security NFR, the facade never interpolates user input raw
    into a URL — :func:`urllib.parse.quote` is applied to both keys and
    values with ``safe=""`` so a stray ``/`` or ``?`` in a search term
    cannot inject a new path segment or query string.
    """
    if not params:
        return None
    encoded: dict[str, str] = {}
    for key, value in params.items():
        encoded[str(key)] = quote(str(value), safe="")
    return encoded


def _build_url(base_url: str, path: str) -> str:
    """Join ``base_url`` and ``path`` with exactly one ``/`` separator.

    ``urljoin`` already collapses duplicate separators and absolute
    paths, but ``base_url`` without a trailing slash would otherwise
    drop the first path segment; we normalise both sides so the output
    is predictable for tests.

    Path segments are assumed to be already percent-encoded by the
    caller; use :func:`encode_path_segment` to encode user-supplied
    values (ids, dates) before they flow into the URL.
    """
    base = base_url.rstrip("/") + "/"
    tail = path.lstrip("/")
    return urljoin(base, tail)


def encode_path_segment(value: Any) -> str:
    """Percent-encode a single value for safe inclusion in a URL path.

    The security NFR forbids raw interpolation of user-supplied input
    into a URL. CLIs should call this helper for every positional
    argument that flows into a path (ids, tmdb ids, dates).
    """
    return quote(str(value), safe="")


def _record_debug(
    *,
    service: str,
    op: str,
    url: str,
    headers: Mapping[str, str],
    response: Any,
) -> None:
    """Write a redacted request/response record to the debug logger.

    The record includes URL, headers (with every secret value replaced
    by ``***<len(original)>``), status, and the response body. The
    function is a no-op when ``debug`` is not requested by the caller
    — :func:`get` simply skips this call in that path.
    """
    redacted_headers = _redact_debug_record(headers)
    body = getattr(response, "content", b"")
    if isinstance(body, bytes):
        try:
            body_text = body.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - defensive
            body_text = repr(body)
    else:
        body_text = str(body)
    if len(body_text) > _BODY_EXCERPT_LIMIT:
        body_text = body_text[:_BODY_EXCERPT_LIMIT] + "…"
    status = getattr(response, "status_code", None)
    _logger.debug(
        "arr-cli request service=%s op=%s url=%s headers=%s status=%s body=%s",
        service,
        op,
        url,
        redacted_headers,
        status,
        body_text,
    )


def _redact_debug_record(
    headers: Mapping[str, str],
) -> dict[str, str]:
    """Return a copy of ``headers`` with secret values redacted.

    Any header whose name is a known auth header (``X-Emby-Token``,
    ``X-Api-Key``, ``Authorization``, ``Cookie``, ``Set-Cookie``) has
    its value replaced by ``***<length>``. All other headers pass
    through verbatim so debugging non-secret headers stays useful.

    The redaction is unconditional (it runs whenever debug records are
    emitted at all), matching the security NFR.
    """
    sensitive = {
        "X-Emby-Token",
        "X-Api-Key",
        "Authorization",
        "Proxy-Authorization",
        "Cookie",
        "Set-Cookie",
    }
    redacted: dict[str, str] = {}
    for name, value in headers.items():
        if name in sensitive or name.lower() in {h.lower() for h in sensitive}:
            redacted[name] = f"***{len(value)}"
        else:
            redacted[name] = value
    return redacted


def _body_excerpt(body: bytes | str | None) -> str:
    """Render a 500-char body excerpt suitable for stderr."""
    if body is None:
        return ""
    if isinstance(body, bytes):
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - defensive
            text = repr(body)
    else:
        text = str(body)
    if len(text) > _BODY_EXCERPT_LIMIT:
        return text[:_BODY_EXCERPT_LIMIT] + "…"
    return text


def _find_first_non_json_byte(raw: bytes) -> int:
    """Return the byte offset where the JSON parser first failed.

    Used to surface a meaningful :class:`ParseError` message (REQ-4
    AC6). The implementation uses :class:`json.JSONDecodeError.pos`,
    which is the standard library's authoritative byte offset for the
    first decoding failure. When the body decodes successfully (which
    can happen if the failure is upstream of ``json.loads``), we
    return the body length so callers still get a deterministic value.
    """
    if not raw:
        return 0
    # ``json.loads`` accepts str; pass the bytes decoded as utf-8 so
    # ``pos`` matches the byte offset of the failing character rather
    # than the Unicode codepoint offset.
    try:
        json.loads(raw.decode("utf-8", errors="replace"))
        return len(raw)
    except json.JSONDecodeError as exc:
        return exc.pos


def _ensure_session() -> Any:
    """Return a fresh :class:`requests.Session` for this call.

    Indirected through a function so unit tests can patch the
    ``requests`` module without touching this module's source.
    """
    import requests  # local import to keep top-of-module cost zero

    return requests.Session()


def get(
    service: str,
    path: str,
    params: Mapping[str, Any] | None = None,
    *,
    cfg: ServiceConfig,
    connect_timeout: float | None = None,
    read_timeout: float | None = None,
    max_items: int | None = MAX_ITEMS_DEFAULT,
    debug: bool = False,
) -> Any:
    """Perform a single ``GET`` against ``service`` and return the parsed JSON.

    Parameters
    ----------
    service:
        One of ``"jellyfin"``, ``"radarr"``, ``"sonarr"``,
        ``"maintainerr"``, ``"seerr"``.
    path:
        Path appended to the service's base URL. Must start with
        ``/``; user-supplied fragments (search terms, ids, dates)
        must be passed via ``params`` rather than concatenated.
    params:
        Mapping of query parameters. Every key and value is
        percent-encoded with ``safe=""`` before being passed to
        :mod:`requests`.
    cfg:
        The :class:`ServiceConfig` produced by :func:`load_config`.
    connect_timeout:
        Optional override for the per-call connect timeout. ``None``
        falls back to ``cfg.connect_timeout`` (default 5s).
    read_timeout:
        Optional override for the per-call read timeout. ``None``
        falls back to ``cfg.read_timeout`` (default 30s).
    debug:
        When True, a redacted request/response record is emitted to
        the module logger at DEBUG level.

    Returns
    -------
    Any
        The parsed JSON payload (list, dict, or scalar) on a 2xx
        response.

    Raises
    ------
    AuthError
        Missing service section, missing credential, or HTTP 401/403.
    NetworkError
        DNS / connection refused / TLS / timeout / generic request
        failure.
    HttpError
        HTTP 4xx / 5xx that is not 401/403.
    ParseError
        Body could not be decoded as JSON.
    """
    auth = _resolve_auth(cfg, service)
    url = _build_url(auth.url, path)
    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "arr-cli/0.1.0",
    }
    _inject_auth(headers, service, auth)

    encoded_params = _encode_params(params)
    connect = cfg.connect_timeout if connect_timeout is None else connect_timeout
    read = cfg.read_timeout if read_timeout is None else read_timeout

    # When ``cfg.retry`` is non-zero we wrap the HTTP call in the
    # retry layer so transient network failures are absorbed (REQ
    # NFR-Reliability). ``cfg.deadline`` provides the wall-clock cap.
    # When ``cfg.retry == 0`` the layer is skipped entirely so the
    # default CLI cold-start stays under the 2-second budget
    # (NFR-Performance cold-start budget). The retry policy only
    # re-attempts :class:`NetworkError`; auth, HTTP-status, and
    # parse failures bubble up unchanged so operators see a stable
    # signal without burning budget on deterministic errors.
    attempts = (cfg.retry or 0) + 1
    deadline = cfg.deadline

    def _do_request() -> Any:
        session = _ensure_session()
        try:
            try:
                response = session.get(
                    url,
                    params=encoded_params,
                    headers=headers,
                    timeout=(connect, read),
                )
            except ImportError as exc:  # pragma: no cover - safety net
                raise AuthError(
                    service,
                    "transport",
                    f"requests is required for HTTP transport: {exc}",
                ) from exc
            except Exception as exc:
                # Catch-all covers every requests exception plus any
                # unexpected socket-level failure. The structured message
                # names the underlying class so operators can see exactly
                # which transport layer failed (REQ-4 AC1).
                raise NetworkError(
                    service,
                    "request",
                    (
                        f"{service}: transport failure ({exc.__class__.__name__}): "
                        f"{exc}"
                    ),
                    url=url,
                ) from exc
        finally:
            # Session is per-call (REQ-5 AC1): close it eagerly so sockets
            # don't linger until the interpreter's GC runs.
            try:
                session.close()
            except Exception:  # pragma: no cover - defensive
                pass

        if debug:
            _record_debug(
                service=service,
                op=path,
                url=url,
                headers=headers,
                response=response,
            )

        status = getattr(response, "status_code", 0)
        if status in {401, 403}:
            guidance = ""
            if service == "maintainerr":
                guidance = (
                    "maintainerr: 401/403 received — set auth.enabled=true "
                    "in arr.conf and restart"
                )
            excerpt = _body_excerpt(getattr(response, "content", b""))
            message = (
                f"{service}: {status} {getattr(response, 'reason', '')} "
                f"for {path}; check {service}.{AK_LITERAL} in arr.conf"
            )
            if guidance:
                message = f"{guidance} — {message}"
            if excerpt:
                message = f"{message}; body={excerpt!r}"
            raise AuthError(service, path, message)

        if not (200 <= status < 300):
            excerpt = _body_excerpt(getattr(response, "content", b""))
            message = (
                f"{service}: HTTP {status} for {path}; body={excerpt!r}"
            )
            raise HttpError(service, path, message, status=status)

        raw_body = getattr(response, "content", b"")
        if isinstance(raw_body, bytes):
            body_bytes = raw_body
        else:
            body_bytes = str(raw_body).encode("utf-8", errors="replace")

        if not body_bytes:
            # Empty body — treat as a successful empty payload. The
            # ``/api/health/ready`` endpoint on Maintainerr returns a bare
            # boolean which arrives here already-decoded, but a 204-style
            # empty body would also reach this branch.
            return None

        try:
            payload = json.loads(body_bytes)
        except json.JSONDecodeError as exc:
            offset = _find_first_non_json_byte(body_bytes)
            raise ParseError(
                service,
                path,
                (
                    f"{service}: invalid JSON at byte offset "
                    f"{offset}: {exc.msg}"
                ),
                byte_offset=offset,
            ) from exc

        effective_cap = max_items if max_items is not None else MAX_ITEMS_DEFAULT
        if isinstance(payload, list) and len(payload) > effective_cap:
            upstream_count = len(payload)
            payload = payload[:effective_cap]
            _logger.warning(
                "arr_cli.facade.transport: truncated payload from %d items to %d (max_items cap)",
                upstream_count,
                effective_cap,
            )

        return payload

    if attempts <= 1:
        # Default cold-start path: no retry layer, no extra closure.
        # Preserves the <= 2 s cold-start budget (NFR-Performance).
        return _do_request()

    return with_retry(
        _do_request,
        attempts=attempts,
        deadline=deadline,
    )


def iter_auth_header_names(services: Iterable[str]) -> dict[str, str]:
    """Return a mapping of service name → header name for inspection.

    Exposed for tests and diagnostics; not part of the runtime
    critical path.
    """
    return {service: _HEADER_NAMES.get(service, "") for service in services}