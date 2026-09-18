"""Seerr CLI (``arr_cli.seerr``) -- task 12.

This module is the Seer entry point for the ``arr-cli`` MVP.
Seer is the unified fork of Overseerr and Jellyseerr; the CLI
talks to whatever Seer instance the operator points it at via
``arr.conf``. It exposes thirteen read-only commands against a live
Seer instance:

* ``requests``                       -- ``GET /api/v1/request``                 (REQ-10 AC1)
* ``request-count``                  -- ``GET /api/v1/request/count``           (REQ-10 AC2)
* ``search <query>``                 -- ``GET /api/v1/search?query=...``       (REQ-10 AC3)
* ``available <query>``              -- ``GET /api/v1/media?filter=available&take=1000`` (REQ-10 AC4)
* ``user``                           -- auth self-check (REQ-10 AC6); single
                                        ``GET /api/v1/auth/me`` probe (the
                                        OpenAPI spec lists ``/auth/me`` relative
                                        to its ``/api/v1`` base, so the full
                                        path is ``/api/v1/auth/me`` -- AGENTS.md
                                        §1 "Seer note"; the bare ``/auth/me``
                                        resolves to the Next.js frontend SPA).
* ``tv <id>``                        -- single-show detail fetch
                                        ``GET /api/v1/tv/{tvId}?language=...``;
                                        with ``--ratings``, also ``GET
                                        /api/v1/tv/{tvId}/ratings`` merged in
                                        for Rotten Tomatoes critic + audience
                                        scores (REQ-10 AC5).
* ``movie <id>``                     -- single-movie detail fetch
                                        ``GET /api/v1/movie/{movieId}?language=...``;
                                        with ``--ratings``, also ``GET
                                        /api/v1/movie/{movieId}/ratings`` merged
                                        in for Rotten Tomatoes critic + audience
                                        scores. Structural twin of ``tv <id>``
                                        so future drift between the two
                                        commands fails the unit suite
                                        immediately.
* ``trending [MEDIA_TYPE] [TIME_WINDOW]``
                                    -- ``GET /api/v1/discover/trending?timeWindow=week[&mediaType=...][&language=...]``;
                                        optional positional ``MEDIA_TYPE``
                                        (``movie`` / ``tv``; omit = all media
                                        types) and ``TIME_WINDOW``
                                        (``day`` / ``week``; default ``week``).
                                        Shares the paginated
                                        ``{page, results, totalPages, totalResults}``
                                        envelope shape with ``search`` so the
                                        renderer mirrors
                                        :func:`_summary_seerr_search` exactly.
* ``upcoming-movies``                  -- ``GET /api/v1/discover/movies/upcoming?page=...&language=...``;
                                        upcoming movie releases from Seer.
                                        Shares the paginated
                                        ``{page, results, totalPages, totalResults}``
                                        envelope shape with ``trending`` and
                                        ``search`` so the renderer mirrors
                                        :func:`_summary_seerr_trending` exactly.
                                        Optional filters: ``--page`` and
                                        ``--language`` (no ``timeWindow`` /
                                        ``mediaType`` knobs -- those would be
                                        additional API surface the documented
                                        CLI does not expose).
* ``upcoming-tv``                      -- ``GET /api/v1/discover/tv/upcoming?page=...&language=...``;
                                        upcoming TV premieres from Seer. Same
                                        envelope shape and renderer as
                                        ``upcoming-movies``; the media type is
                                        encoded in the path so no positional
                                        ``MEDIA_TYPE`` is accepted. Optional
                                        filters: ``--page`` and ``--language``.
* ``discover-movies``                  -- ``GET /api/v1/discover/movies?genre=<id>&sortBy=<sortBy>&language=<LANG>&page=<N>``;
                                        filterable movie browse against the
                                        general discover endpoint. Shares the
                                        paginated ``{page, results,
                                        totalPages, totalResults}`` envelope
                                        shape with ``upcoming-movies`` /
                                        ``trending`` so the renderer mirrors
                                        :func:`_summary_seerr_upcoming_movies`
                                        exactly. Filters: ``--genre <int>``
                                        (TMDB genre id), ``--sort <sortBy>``
                                        (default ``popularity.desc``),
                                        ``--language <LANG>`` (default
                                        ``en-US``), plus the universal
                                        ``--page`` / ``--limit``. The
                                        upstream ``limit`` query parameter is
                                        intentionally NOT forwarded; the
                                        discover endpoints page instead, and
                                        the universal ``--limit`` caps
                                        client-side row output only.
* ``discover-tv``                      -- ``GET /api/v1/discover/tv?genre=<id>&sortBy=<sortBy>&language=<LANG>&page=<N>``;
                                        filterable TV browse against the
                                        general discover endpoint. Same
                                        envelope, renderer, and filter set as
                                        ``discover-movies``. Structural twin
                                        of ``discover-movies`` so future drift
                                        between the two discover commands
                                        fails the unit suite immediately.
* ``genres [MEDIA_TYPE]``               -- ``GET /api/v1/genres/<movie|tv>``;
                                        returns the TMDB genre list as
                                        ``[{id, name}, ...]`` so the operator
                                        can map a friendly genre name (e.g.
                                        ``Sci-Fi``) to a TMDB integer id
                                        (e.g. ``878``) before passing it to
                                        ``discover-movies --genre`` /
                                        ``discover-tv --genre``. Optional
                                        positional ``MEDIA_TYPE`` (``movie``
                                        / ``tv``; default ``movie``) mirrors
                                        ``trending``'s positional-with-default
                                        pattern. Path / method / response
                                        shape documented per AGENTS.md §1
                                        "Seer note"; the canonical TMDB
                                        genre endpoint is stable across
                                        Overseerr → Jellyseerr → Seer, so
                                        the implementation matches the
                                        upstream spec by construction.

Per the MVP design, every command is a thin wrapper that:

1. Builds the Seerr path (with percent-encoded path segments)
2. Calls :func:`arr_cli.facade.transport.get` to fetch the JSON
3. Renders the payload via :func:`arr_cli.facade.output.emit` (JSON
   pass-through by default, tabular ``--human`` rendering when
   requested)
4. Returns ``0`` on success or reraises the documented
   :class:`ArrError` subclass so :func:`main_wrapper` can translate
   the error into the structured ``service=...`` stderr line and the
   documented exit code.

The auth header is ``X-Api-Key`` (injected transparently by
:func:`arr_cli.facade.transport._inject_auth` per REQ-2 AC3); this
module never references the credential itself.

Following the design contract this module contains no hardcoded URL
or credential; it reads configuration via :class:`ServiceConfig` and
delegates transport / auth / output to the facade. The console-script
entry point declared in ``pyproject.toml`` (``seerr = \
"arr_cli.seerr:main"``) calls :func:`main` which builds the argparse
subparser tree and delegates to the universal
:func:`arr_cli.facade.cli_common.main_wrapper` for config loading and
error mapping.

Seerr-specific behaviour (REQ-10 AC6, AC7):

* The ``user`` command performs a single auth probe against
  ``GET /api/v1/auth/me`` (REQ-10 AC6). The OpenAPI spec is
  mounted at ``/api-docs`` with ``url: {server}/api/v1``, so the
  spec's ``/auth/me`` is relative to that prefix and the full URL
  is ``/api/v1/auth/me``. Seer dropped the historical
  ``/api/v1/user/me`` primary path; the previous two-step
  ``/api/v1/user/me`` -> ``/auth/me`` fallback probe (with the
  narrow ``status == 404`` trigger) was removed because the
  OpenAPI validator's response for an unknown path is ``400``
  rather than ``404``, which masked the path divergence instead
  of resolving it. The single ``/api/v1/auth/me`` probe raises
  :class:`HttpError(exit_code=4)` on any non-2xx response, which
  ``main_wrapper`` surfaces as a structured
  ``service=seerr op=/api/v1/auth/me status=...`` stderr line.

* ``create-request`` (``POST /api/v1/request``) is **NOT** in MVP and
  MUST NOT appear in ``--help`` (REQ-10 AC7). The dispatch table
  reserves the slot for tier-2 work, where it will be guarded by
  ``--confirm`` per the requirements.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Mapping, Sequence

from arr_cli.facade import output, transport
from arr_cli.facade.cli_common import build_parser, main_wrapper, universal_parents
from arr_cli.facade.config import ServiceConfig
from arr_cli.facade.errors import ConfigError

__all__ = [
    "main",
    "build_seerr_parser",
    # Command handlers are exposed for tests; they are not part of the
    # public CLI surface but unit tests use them to verify routing in
    # isolation from the argparse layer.
    "cmd_requests",
    "cmd_request_count",
    "cmd_search",
    "cmd_available",
    "cmd_user",
    "cmd_tv",
    "cmd_movie",
    "cmd_trending",
    "cmd_upcoming_movies",
    "cmd_upcoming_tv",
    "cmd_discover_movies",
    "cmd_discover_tv",
    "cmd_genres",
    "seerr_genres",
    # Path constants exposed so tests can assert against the exact
    # strings for each endpoint.
    "USER_ME_PATH",
    "TRENDING_PATH",
    "UPCOMING_MOVIES_PATH",
    "UPCOMING_TV_PATH",
    "DISCOVER_MOVIES_PATH",
    "DISCOVER_TV_PATH",
    "GENRES_MOVIE_PATH",
    "GENRES_TV_PATH",
]


#: Service identifier used by transport.get / output.emit.
#: Centralised so a future rename touches one constant.
SERVICE_NAME = "seerr"


#: Path for the auth self-check per REQ-10 AC6.
#: ``GET /api/v1/auth/me`` -- the canonical path on Seer (the unified
#: Overseerr + Jellyseerr fork); the bare ``/auth/me`` path resolves
#: to the Next.js frontend SPA, not the API. The OpenAPI spec is
#: mounted at ``/api-docs`` with ``url: {server}/api/v1``, so the
#: spec's ``/auth/me`` is relative to that prefix.
USER_ME_PATH = "/api/v1/auth/me"


#: Path for the trending discover endpoint.
#: ``GET /api/v1/discover/trending`` -- the canonical Seer discover
#: endpoint; accepts ``timeWindow`` (``day`` / ``week``), optional
#: ``mediaType`` (``movie`` / ``tv``; omit = all media types) and
#: optional ``language`` (``ISO 639-1``) query parameters. The live
#: OpenAPI spec is the source of truth per AGENTS.md §1 "Seer note".
TRENDING_PATH = "/api/v1/discover/trending"


#: Path for the upcoming movies discover endpoint.
#: ``GET /api/v1/discover/movies/upcoming`` -- the canonical Seer
#: discover endpoint for upcoming movie releases. Accepts optional
#: ``page`` and ``language`` (``ISO 639-1``) query parameters; the
#: media type is encoded in the path so no ``mediaType`` filter is
#: exposed (the documented CLI surface keeps it simple -- only
#: ``--page`` and ``--language`` ride on the wire). Returns the
#: same ``{page, results, totalPages, totalResults}`` envelope as
#: :data:`TRENDING_PATH`.
UPCOMING_MOVIES_PATH = "/api/v1/discover/movies/upcoming"


#: Path for the upcoming TV discover endpoint.
#: ``GET /api/v1/discover/tv/upcoming`` -- the canonical Seer
#: discover endpoint for upcoming TV premieres. Accepts optional
#: ``page`` and ``language`` (``ISO 639-1``) query parameters; the
#: media type is encoded in the path so no ``mediaType`` filter is
#: exposed. Returns the same ``{page, results, totalPages,
#: totalResults}`` envelope as :data:`TRENDING_PATH`.
UPCOMING_TV_PATH = "/api/v1/discover/tv/upcoming"


#: Path for the general movies discover endpoint.
#: ``GET /api/v1/discover/movies`` -- the canonical Seer discover
#: endpoint for browsing movies by genre / sort order / language.
#: Accepts ``genre`` (TMDB genre id), ``sortBy`` (default
#: ``popularity.desc``), ``language`` (``ISO 639-1``, default
#: ``en-US``), and ``page`` query parameters. Returns the same
#: ``{page, results, totalPages, totalResults}`` envelope as
#: :data:`UPCOMING_MOVIES_PATH`. The ``limit`` query parameter is
#: intentionally NOT forwarded to the upstream endpoint -- the
#: discover endpoints page instead, and the universal ``--limit``
#: flag caps client-side row output only.
DISCOVER_MOVIES_PATH = "/api/v1/discover/movies"


#: Path for the general TV discover endpoint.
#: ``GET /api/v1/discover/tv`` -- the canonical Seer discover
#: endpoint for browsing TV series by genre / sort order / language.
#: Accepts the same ``genre`` / ``sortBy`` / ``language`` / ``page``
#: query parameters as :data:`DISCOVER_MOVIES_PATH`. Returns the same
#: ``{page, results, totalPages, totalResults}`` envelope.
DISCOVER_TV_PATH = "/api/v1/discover/tv"


#: Path for the movie genres endpoint.
#: ``GET /api/v1/genres/movie`` -- the canonical TMDB-backed genre
#: list for movies. Stable across Overseerr → Jellyseerr → Seer;
#: the live ``/api-docs/swagger-ui-init.js`` OpenAPI spec on the
#: operator's instance is the source of truth per AGENTS.md §1
#: "Seer note". Returns ``[{id: int, name: str}, ...]`` (no
#: envelope wrapping).
GENRES_MOVIE_PATH = "/api/v1/genres/movie"


#: Path for the TV genres endpoint.
#: ``GET /api/v1/genres/tv`` -- the canonical TMDB-backed genre
#: list for TV. Stable across Overseerr → Jellyseerr → Seer;
#: the live ``/api-docs/swagger-ui-init.js`` OpenAPI spec on the
#: operator's instance is the source of truth per AGENTS.md §1
#: "Seer note". Returns ``[{id: int, name: str}, ...]`` (no
#: envelope wrapping).
GENRES_TV_PATH = "/api/v1/genres/tv"


# Module-level logger so the documented DEBUG probe records
# (design.md "Pre-locking Verifications -- Seerr") surface through
# the standard ``logging`` configuration without a private handler.
_logger = logging.getLogger("arr_cli.seerr")


# ---------------------------------------------------------------------------
# Module-level HTTP helpers
# ---------------------------------------------------------------------------


def seerr_genres(
    media_type: str,
    args: argparse.Namespace,
    cfg: ServiceConfig,
) -> list[Any]:
    """Return the TMDB genre list for ``media_type`` as ``[{id, name}, ...]``.

    Thin module-level HTTP helper that lives next to the other
    module-level ``seerr_*`` helpers (e.g. the ``cmd_*`` handlers
    in this module) and follows the same per-call ``_get`` pattern.
    Dispatches to :data:`GENRES_MOVIE_PATH` or
    :data:`GENRES_TV_PATH` based on ``media_type`` and lets the
    facade errors propagate unchanged so
    :func:`arr_cli.facade.cli_common.main_wrapper` can translate
    them into the documented ``service=seerr op=... status=...``
    stderr line + exit code.

    Parameters
    ----------
    media_type:
        One of ``"movie"`` or ``"tv"``. The argparse ``choices=`` on
        the ``genres`` subparser already rejects anything else with
        ``SystemExit(2)`` at parse time, but the function keeps a
        defensive guard so it can be reused safely from non-CLI
        entry points (tests, future library consumers).
    args:
        The parsed argparse namespace; forwarded to :func:`_get`
        so the documented ``--connect-timeout`` / ``--read-timeout``
        / ``--debug`` flags are honored consistently with the
        other seerr handlers.
    cfg:
        The loaded :class:`ServiceConfig`; forwarded to
        :func:`_get` so auth (``X-Api-Key``) is injected by the
        facade per REQ-2 AC3.

    Returns
    -------
    list[Any]
        The verbatim JSON list returned by the upstream endpoint,
        i.e. ``[{id: int, name: str}, ...]``. The renderer in
        :func:`arr_cli.facade.output._summary_seerr_genres` projects
        this list to the curated ``id`` / ``name`` summary shape
        so the default stdout stays small.

    Raises
    ------
    ConfigError
        When ``media_type`` is not ``"movie"`` or ``"tv"`` -- the
        facade's :class:`ConfigError` (exit code 1) is the
        documented shape for client-side validation failures, so
        the guard raises the same class the other seerr helpers
        would for a malformed request.
    """
    if media_type == "movie":
        path = GENRES_MOVIE_PATH
    elif media_type == "tv":
        path = GENRES_TV_PATH
    else:
        raise ConfigError(
            SERVICE_NAME,
            "genres",
            f"{SERVICE_NAME}: media_type must be 'movie' or 'tv'; "
            f"got {media_type!r}",
        )
    # The path-constant format is ``/api/v1/...`` -- absolute, no
    # concatenation required. If a future variant needs a trailing
    # path fragment, the facade's percent-encoding policy keeps
    # user-supplied tokens safe; static literal concatenation is
    # safe today.
    # ``op`` carries the documented ``op=`` value surfaced via
    # :class:`ArrError` so the stderr line reads
    # ``service=seerr op=genres/<media_type> status=...`` on
    # failure. Match the convention used by the other seerr
    # handlers (e.g. ``search`` / ``requests``).
    payload = _get(
        path,
        args,
        cfg,
        op=f"genres/{media_type}",
    )
    # Defensive unwrap: ``GET /api/v1/genres/<mediaType>`` returns a
    # bare list (no envelope), but a future Seer version that wraps
    # the response in ``{results: [...]}`` would still surface as
    # a list-like payload here. Keep the renderer contract flat
    # (a list of ``{id, name}``) so the renderer does not have to
    # know about either shape.
    if isinstance(payload, Mapping):
        inner = payload.get("results")
        if isinstance(inner, list):
            return inner
        # Non-``results``-keyed mapping -- fall back to a list of
        # single-value iterations so the renderer prints the
        # document rather than swallowing it.
        return list(payload.values()) if payload else []
    if isinstance(payload, list):
        return payload
    # Bare scalar / ``None`` -- surface as an empty list so the
    # renderer prints its "no rows" footer rather than crashing.
    return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit(
    payload: Any,
    args: argparse.Namespace,
    *,
    columns: Sequence[str] | None = None,
) -> int:
    """Render ``payload`` via :func:`output.emit` and return exit code 0.

    Centralised so every command handler has the same JSON-vs-tabular
    decision point; the per-command handler only decides which columns
    to surface when ``--human`` is requested.
    """
    output.emit(
        payload,
        human_mode=bool(getattr(args, "human", False)),
        verbose_mode=bool(getattr(args, "verbose", False)),
        service=SERVICE_NAME,
        command=str(getattr(args, "command", "") or ""),
        columns=columns,
        limit=int(getattr(args, "limit", 20) or 20),
    )
    return 0


def _get(
    path: str,
    args: argparse.Namespace,
    cfg: ServiceConfig,
    *,
    params: dict[str, Any] | None = None,
    op: str | None = None,
) -> Any:
    """Thin wrapper around :func:`transport.get` that honors per-call flags.

    The ``connect_timeout`` / ``read_timeout`` flags are forwarded from
    the parsed argparse namespace so :func:`main_wrapper`'s
    "CLI overrides config" policy applies uniformly (REQ-5 AC3). The
    ``op`` parameter is currently informational -- the path argument
    already serves as the documented ``op=`` value surfaced via
    :class:`ArrError` -- but is accepted for symmetry with the other
    CLIs so a future logging hook (e.g. per-call metrics) does not
    require a signature change.
    """
    return transport.get(
        SERVICE_NAME,
        path,
        params=params,
        cfg=cfg,
        connect_timeout=getattr(args, "connect_timeout", None),
        read_timeout=getattr(args, "read_timeout", None),
        debug=bool(getattr(args, "debug", False)),
    )


def cmd_user(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``user`` -- auth self-check via ``GET /api/v1/auth/me`` (REQ-10 AC6).

    Seer (the unified Overseerr + Jellyseerr fork) exposes the
    canonical auth-self-check endpoint at :data:`USER_ME_PATH`
    (``/api/v1/auth/me``). The OpenAPI spec is mounted at
    ``/api-docs`` with ``url: {server}/api/v1``, so the spec's
    ``/auth/me`` is relative to that prefix; the bare ``/auth/me``
    resolves to the Next.js frontend SPA. The historical
    ``/api/v1/user/me`` primary path was removed from Seer's live
    OpenAPI spec; the previous two-step probe that fell back to
    ``/auth/me`` on a 404 from the primary was therefore collapsed
    to a single ``/api/v1/auth/me`` call so the operator gets the
    authenticated user object on stdout and exit ``0`` instead of an
    opaque ``400`` (OpenAPI validator's "path unknown") response
    that bubbled up as exit ``4``.

    Non-2xx responses raise :class:`HttpError(exit_code=4)` via
    :func:`transport.get`, which :func:`main_wrapper` surfaces as a
    structured ``service=seerr op=/api/v1/auth/me status=<code>``
    stderr line; the operator's diagnostic tools keep working
    unchanged.

    Authentication is handled transparently by the transport layer
    (``X-Api-Key`` header per REQ-2 AC3); this handler does not
    inspect or echo the credential.
    """
    payload = _get(USER_ME_PATH, args, cfg, op="user")
    return _emit(payload, args, columns=None)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_requests(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``requests`` -- the household request queue (REQ-10 AC1).

    Returns the full list of media requests; ``--human`` renders the
    most common columns (``title``, ``type``, ``status``, ``createdAt``).

    The ``take`` query parameter caps the response at the documented
    Seer limit (``1000``) so a single round trip covers the household
    queue instead of the default first page of ten.
    """
    payload = _get(
        "/api/v1/request",
        args,
        cfg,
        params={"take": 1000},
        op="requests",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_seerr_requests``: nested ``requestedBy.displayName``
    # is resolved via dot-path traversal in ``_row_from_mapping``.
    columns = [
        "title",
        "type",
        "status",
        "createdAt",
        "requestedBy.displayName",
    ]
    return _emit(payload, args, columns=columns)


def cmd_request_count(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``request-count`` -- summary counts (REQ-10 AC2).

    Returns a small object whose keys are aggregate counts (e.g.
    ``pending``, ``approved``, ``available``). The ``--human``
    renderer prints the object as ``key: value`` pairs because no
    tabular columns apply to a scalar-ish summary.
    """
    payload = _get(
        "/api/v1/request/count",
        args,
        cfg,
        op="request-count",
    )
    return _emit(payload, args, columns=None)


def cmd_search(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``search <query>`` -- multi-source search (REQ-10 AC3).

    The ``query`` parameter is forwarded as a query string; the
    transport layer percent-encodes the value so special characters
    (slashes, spaces, ``?``, ``&``) cannot break the URL.

    Targets Seer's consolidated ``/api/v1/search`` endpoint. The
    historical Overseerr path ``/api/v1/search/multi`` is NOT
    exposed by Seer; the live ``/api-docs/swagger-ui-init.js``
    OpenAPI spec is the source of truth (AGENTS.md §1 "Seer note").
    """
    query = getattr(args, "query", "") or ""
    payload = _get(
        "/api/v1/search",
        args,
        cfg,
        params={"query": query},
        op="search",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_seerr_search``: nested ``mediaInfo.tmdbId`` is
    # resolved via dot-path traversal in ``_row_from_mapping``.
    columns = [
        "title",
        "mediaType",
        "releaseDate",
        "mediaInfo.tmdbId",
    ]
    return _emit(payload, args, columns=columns)


def cmd_available(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``available <query>`` -- what's already in the library (REQ-10 AC4).

    Returns the subset of media Seer considers "available" (i.e.
    already requested and present in the user's library). Seer's
    general list endpoint ``GET /api/v1/media`` accepts a
    ``filter`` parameter for the "in library" subset
    (``filter=available`` is the leading hypothesis; verify the
    accepted token against the live
    ``/api-docs/swagger-ui-init.js`` OpenAPI spec on the operator's
    instance per AGENTS.md §1 "Seer note"); the ``take=1000`` cap
    mirrors :func:`cmd_requests` so a single response covers the
    household library rather than the default first page of ten.

    Title-substring matching is applied client-side after the
    fetch: Seer's ``/api/v1/media`` does not document a
    title-search query parameter, so any non-empty ``query`` is
    matched case-insensitively against each item's ``title``
    field. Items missing a ``title`` are dropped.
    """
    query = getattr(args, "query", "") or ""
    payload = _get(
        "/api/v1/media",
        args,
        cfg,
        params={"take": 1000, "filter": "available"},
        op="available",
    )
    # Client-side title-substring post-filter: extract the items
    # out of the paginated envelope (when present), then keep only
    # the rows whose ``title`` contains the query as a
    # case-insensitive substring. Empty query passes the payload
    # through unchanged so the renderer can unwrap the envelope
    # itself.
    if query:
        if isinstance(payload, dict):
            items = payload.get("results")
        else:
            items = payload
        if isinstance(items, list):
            needle = query.casefold()
            filtered = [
                item
                for item in items
                if isinstance(item, dict)
                and isinstance(item.get("title"), str)
                and needle in item["title"].casefold()
            ]
            payload = filtered
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_seerr_available``: nested ``mediaInfo.status`` is
    # resolved via dot-path traversal in ``_row_from_mapping``.
    columns = [
        "title",
        "mediaType",
        "releaseDate",
        "mediaInfo.status",
    ]
    return _emit(payload, args, columns=columns)


def cmd_tv(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``tv <id>`` -- TV show details + optional Rotten Tomatoes ratings (REQ-10 AC5).

    Single-show detail fetch: ``GET /api/v1/tv/{tvId}?language=...``
    returns the full TV metadata (name, originalName, firstAirDate,
    genres[], networks[], seasons[], numberOfSeasons, status,
    createdBy, episodeRunTime, ...). The Doctor Who payload (id=57243)
    is 228KB on a live Seer instance; the renderer maps the relevant
    fields into a curated summary shape via
    :func:`arr_cli.facade.output._summary_seerr_tv` so the default
    stdout stays chat-agent-sized instead of dumping 228KB of JSON.

    ``--ratings`` additionally calls
    ``GET /api/v1/tv/{tvId}/ratings?language=...`` and merges the small
    Rotten Tomatoes critic + audience JSON under
    ``payload["ratings"]``. The renderer surfaces both scores via the
    same path; a single-fetch invocation leaves ``ratings`` unset so
    callers who don't ask for RT data don't pay the extra round trip.

    The ``language`` query parameter is forwarded to both endpoints so
    localized titles / overviews / RT data line up across the merged
    payload.

    Non-2xx responses raise :class:`HttpError(exit_code=4)` via
    :func:`transport.get`, which :func:`main_wrapper` surfaces as a
    structured ``service=seerr op=/api/v1/tv/<id> status=<code>``
    (or ``op=/api/v1/tv/<id>/ratings status=<code>``) stderr line.

    Authentication is handled transparently by the transport layer
    (``X-Api-Key`` header per REQ-2 AC3); this handler does not inspect
    or echo the credential.
    """
    tv_id = getattr(args, "id", "") or ""
    language = getattr(args, "language", None)
    params: dict[str, Any] = {}
    if language:
        params["language"] = language
    payload = _get(
        f"/api/v1/tv/{tv_id}",
        args,
        cfg,
        params=params or None,
        op=f"tv/{tv_id}",
    )
    if getattr(args, "ratings", False):
        ratings = _get(
            f"/api/v1/tv/{tv_id}/ratings",
            args,
            cfg,
            params=params or None,
            op=f"tv/{tv_id}/ratings",
        )
        if isinstance(payload, Mapping) and isinstance(ratings, Mapping):
            # Merge under a dedicated ``ratings`` key so the renderer
            # can detect the optional block and surface ``criticsScore``
            # / ``audienceScore`` rather than silently dropping them.
            payload = {**payload, "ratings": ratings}
        else:
            # Defensive: an unexpected shape on either side (e.g. an
            # HTTP-error JSON object rather than the detail/ratings
            # document) shouldn't crash the whole command. Log and
            # continue with the detail payload as-is so the operator
            # at least sees what the detail endpoint returned.
            _logger.warning(
                "seerr.tv: ratings payload was not a dict; "
                "skipping merge"
            )
    # Top-level columns mirror the keys emitted by
    # ``_summary_seerr_tv``; ``--human`` on a single-object payload
    # uses ``_render_object`` which ignores columns, but pinning the
    # literal here is what lets
    # ``tests/unit/test_output.py::_columns_for`` assert the
    # columns / summary alignment (REQ-18 AC1). Plain assignment
    # (no ``: list[str]`` annotation) because the AST helper only
    # recognises ``ast.Assign`` targets -- matches every other
    # ``columns`` literal in this module.
    columns = [
        "name",
        "originalName",
        "firstAirDate",
        "genres",
        "networks",
        "numberOfSeasons",
        "status",
    ]
    return _emit(payload, args, columns=columns)


def cmd_movie(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``movie <id>`` -- per-movie details + optional Rotten Tomatoes ratings.

    Single-movie detail fetch:
    ``GET /api/v1/movie/{movieId}?language=...`` returns the full
    movie metadata (name, originalTitle, releaseDate, runtime,
    genres[], tagline, overview, cast, ...). The Matrix payload
    (id=603) is ~110KB on a live Seer instance; the renderer maps
    the relevant fields into a curated summary shape via
    :func:`arr_cli.facade.output._summary_seerr_movie` so the
    default stdout stays chat-agent-sized instead of dumping
    110KB of JSON. The raw ``runtime`` integer (minutes) is
    formatted as ``"<X>h <Y>m"`` by the renderer -- the spec'd
    display format which matters because raw minutes is not
    human-readable.

    ``--ratings`` additionally calls
    ``GET /api/v1/movie/{movieId}/ratings?language=...`` and
    merges the small Rotten Tomatoes critic + audience JSON
    under ``payload["ratings"]``. The renderer surfaces both
    scores via the same path; a single-fetch invocation leaves
    ``ratings`` unset so callers who don't ask for RT data
    don't pay the extra round trip.

    The ``language`` query parameter is forwarded to both
    endpoints so localized titles / overviews / RT data line up
    across the merged payload.

    Structural twin of :func:`cmd_tv` so future drift between
    the two commands fails the unit suite immediately.

    Non-2xx responses raise :class:`HttpError(exit_code=4)` via
    :func:`transport.get`, which :func:`main_wrapper` surfaces
    as a structured
    ``service=seerr op=/api/v1/movie/<id> status=<code>``
    (or ``op=/api/v1/movie/<id>/ratings status=<code>``) stderr
    line.

    Authentication is handled transparently by the transport
    layer (``X-Api-Key`` header per REQ-2 AC3); this handler
    does not inspect or echo the credential.
    """
    movie_id = getattr(args, "id", "") or ""
    language = getattr(args, "language", None)
    params: dict[str, Any] = {}
    if language:
        params["language"] = language
    payload = _get(
        f"/api/v1/movie/{movie_id}",
        args,
        cfg,
        params=params or None,
        op=f"movie/{movie_id}",
    )
    if getattr(args, "ratings", False):
        ratings = _get(
            f"/api/v1/movie/{movie_id}/ratings",
            args,
            cfg,
            params=params or None,
            op=f"movie/{movie_id}/ratings",
        )
        if isinstance(payload, Mapping) and isinstance(ratings, Mapping):
            # Merge under a dedicated ``ratings`` key so the renderer
            # can detect the optional block and surface
            # ``criticsScore`` / ``audienceScore`` rather than
            # silently dropping them. Mirrors the same defensive
            # merge guard ``cmd_tv`` uses so a non-Mapping ratings
            # response never crashes the command.
            payload = {**payload, "ratings": ratings}
        else:
            # Defensive: an unexpected shape on either side (e.g. an
            # HTTP-error JSON object rather than the detail/ratings
            # document) shouldn't crash the whole command. Log and
            # continue with the detail payload as-is so the operator
            # at least sees what the detail endpoint returned.
            _logger.warning(
                "seerr.movie: ratings payload was not a dict; "
                "skipping merge"
            )
    # Top-level columns mirror the keys emitted by
    # ``_summary_seerr_movie``; ``--human`` on a single-object
    # payload uses ``_render_object`` which ignores columns, but
    # pinning the literal here is what lets
    # ``tests/unit/test_output.py::_columns_for`` assert the
    # columns / summary alignment (REQ-18 AC1). Plain
    # assignment (no ``: list[str]`` annotation) because the
    # AST helper only recognises ``ast.Assign`` targets --
    # matches every other ``columns`` literal in this module.
    columns = [
        "name",
        "originalTitle",
        "releaseDate",
        "runtime",
        "genres",
        "tagline",
    ]
    return _emit(payload, args, columns=columns)


def cmd_trending(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``trending [MEDIA_TYPE] [TIME_WINDOW]`` -- what's trending on Seer right now.

    ``GET /api/v1/discover/trending`` returns the paginated envelope
    ``{page, totalPages, totalResults, results: [...]}`` -- the same
    shape :func:`cmd_search` consumes -- so the renderer is a
    near-verbatim copy of :func:`_summary_seerr_search`. Each item
    carries at minimum ``title``, ``mediaType``, ``releaseDate`` and
    ``mediaInfo.tmdbId`` (same projection as the ``search`` command).

    Three optional filters ride on the query string:

    * ``timeWindow`` (``day`` / ``week``) -- always sent (default
      ``week`` from the subparser); the documented default IS the
      value the operator wants, so there is no
      ``omit-when-default`` rule for this flag.
    * ``mediaType`` (``movie`` / ``tv``) -- only forwarded when the
      operator passed a positional ``MEDIA_TYPE``; omitting it asks
      Seer for "all media types".
    * ``language`` (``ISO 639-1``) -- only forwarded when
      ``--language`` is set; no empty ``?language=`` rides the wire.

    Non-2xx responses raise :class:`HttpError(exit_code=4)` via
    :func:`transport.get`, which :func:`main_wrapper` surfaces as a
    structured ``service=seerr op=/api/v1/discover/trending status=<code>``
    stderr line; the operator's diagnostic tools keep working unchanged.

    Authentication is handled transparently by the transport layer
    (``X-Api-Key`` header per REQ-2 AC3); this handler does not inspect
    or echo the credential.
    """
    params: dict[str, Any] = {}
    # ``timeWindow`` always rides on the query string because the
    # documented default (``week``) IS the value the operator wants
    # -- there's no omit-when-default rule for this flag (US-3 AC6).
    params["timeWindow"] = getattr(args, "time_window", "week") or "week"
    media_type = getattr(args, "media_type", None)
    if media_type:
        params["mediaType"] = media_type
    language = getattr(args, "language", None)
    if language:
        params["language"] = language
    payload = _get(
        TRENDING_PATH,
        args,
        cfg,
        params=params,
        op="trending",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_seerr_trending``: nested ``mediaInfo.tmdbId`` is
    # resolved via dot-path traversal in ``_row_from_mapping``.
    # Sibling literal of the ``cmd_search`` ``columns`` so the two
    # commands share the same per-row projection.
    columns = [
        "title",
        "mediaType",
        "releaseDate",
        "mediaInfo.tmdbId",
    ]
    return _emit(payload, args, columns=columns)


def cmd_upcoming_movies(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``upcoming-movies`` -- upcoming movie releases from Seer.

    ``GET /api/v1/discover/movies/upcoming`` returns the paginated
    envelope ``{page, totalPages, totalResults, results: [...]}`` --
    the same shape :func:`cmd_trending` consumes -- so the renderer
    is a near-verbatim copy of :func:`_summary_seerr_trending`. Each
    item carries at minimum ``title``, ``mediaType``, ``releaseDate``
    and ``mediaInfo.tmdbId`` (same projection as the ``trending``
    command).

    Two optional filters ride on the query string:

    * ``page`` -- only forwarded when ``--page`` is set; no empty
      ``?page=`` rides the wire.
    * ``language`` (``ISO 639-1``) -- only forwarded when
      ``--language`` is set; no empty ``?language=`` rides the wire.

    The CLI surface deliberately does NOT expose ``timeWindow``,
    ``mediaType`` or any other API knob beyond ``--page`` and
    ``--language`` -- the documented "keep it simple" surface for
    this endpoint.

    Non-2xx responses raise :class:`HttpError(exit_code=4)` via
    :func:`transport.get`, which :func:`main_wrapper` surfaces as a
    structured ``service=seerr op=/api/v1/discover/movies/upcoming
    status=<code>`` stderr line; the operator's diagnostic tools
    keep working unchanged.

    Authentication is handled transparently by the transport layer
    (``X-Api-Key`` header per REQ-2 AC3); this handler does not
    inspect or echo the credential.
    """
    params: dict[str, Any] = {}
    page = getattr(args, "page", None)
    if page is not None:
        params["page"] = page
    language = getattr(args, "language", None)
    if language:
        params["language"] = language
    payload = _get(
        UPCOMING_MOVIES_PATH,
        args,
        cfg,
        params=params or None,
        op="upcoming-movies",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_seerr_upcoming_movies``: nested ``mediaInfo.tmdbId``
    # is resolved via dot-path traversal in ``_row_from_mapping``.
    # Byte-identical literal to ``cmd_trending`` because the per-row
    # projection is the same (same envelope, same item shape).
    columns = [
        "title",
        "mediaType",
        "releaseDate",
        "mediaInfo.tmdbId",
    ]
    return _emit(payload, args, columns=columns)


def cmd_upcoming_tv(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``upcoming-tv`` -- upcoming TV premieres from Seer.

    ``GET /api/v1/discover/tv/upcoming`` returns the paginated
    envelope ``{page, totalPages, totalResults, results: [...]}`` --
    the same shape :func:`cmd_trending` / :func:`cmd_upcoming_movies`
    consume -- so the renderer is a near-verbatim copy of
    :func:`_summary_seerr_trending`. The media type is encoded in the
    path so no positional ``MEDIA_TYPE`` is accepted (matches the
    documented "keep it simple" surface for this endpoint).

    Two optional filters ride on the query string:

    * ``page`` -- only forwarded when ``--page`` is set; no empty
      ``?page=`` rides the wire.
    * ``language`` (``ISO 639-1``) -- only forwarded when
      ``--language`` is set; no empty ``?language=`` rides the wire.

    Non-2xx responses raise :class:`HttpError(exit_code=4)` via
    :func:`transport.get`, which :func:`main_wrapper` surfaces as a
    structured ``service=seerr op=/api/v1/discover/tv/upcoming
    status=<code>`` stderr line; the operator's diagnostic tools
    keep working unchanged.

    Authentication is handled transparently by the transport layer
    (``X-Api-Key`` header per REQ-2 AC3); this handler does not
    inspect or echo the credential.

    Structural twin of :func:`cmd_upcoming_movies` so future drift
    between the two upcoming commands fails the unit suite
    immediately.
    """
    params: dict[str, Any] = {}
    page = getattr(args, "page", None)
    if page is not None:
        params["page"] = page
    language = getattr(args, "language", None)
    if language:
        params["language"] = language
    payload = _get(
        UPCOMING_TV_PATH,
        args,
        cfg,
        params=params or None,
        op="upcoming-tv",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_seerr_upcoming_tv``: nested ``mediaInfo.tmdbId`` is
    # resolved via dot-path traversal in ``_row_from_mapping``.
    # Byte-identical literal to ``cmd_upcoming_movies`` because the
    # per-row projection is the same (same envelope, same item shape).
    columns = [
        "title",
        "mediaType",
        "releaseDate",
        "mediaInfo.tmdbId",
    ]
    return _emit(payload, args, columns=columns)


def cmd_discover_movies(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``discover-movies`` -- filterable movie browse against the general discover endpoint.

    ``GET /api/v1/discover/movies`` returns the paginated envelope
    ``{page, totalPages, totalResults, results: [...]}`` -- the same
    shape :func:`cmd_upcoming_movies` consumes -- so the renderer is
    a near-verbatim copy of :func:`_summary_seerr_upcoming_movies`.
    Each item carries at minimum ``title``, ``mediaType``,
    ``releaseDate`` and ``mediaInfo.tmdbId`` (same projection as the
    ``upcoming-movies`` / ``trending`` / ``search`` commands).

    Four optional filters ride on the query string:

    * ``genre`` (TMDB genre id) -- only forwarded when ``--genre`` is
      set; ``int`` type so an unparseable value exits ``1`` at parse
      time before any HTTP request is issued.
    * ``sortBy`` -- always forwarded; defaults to ``popularity.desc``
      (the upstream default the operator wants, so no
      ``omit-when-default`` rule for this flag).
    * ``language`` (``ISO 639-1``) -- only forwarded when the
      operator overrides the default ``en-US``; otherwise the default
      rides the wire.
    * ``page`` -- only forwarded when ``--page`` is set; no empty
      ``?page=`` rides the wire.

    The upstream ``limit`` query parameter is intentionally NOT
    forwarded -- the discover endpoints page instead, and the
    universal ``--limit`` caps client-side row output only.

    Non-2xx responses raise :class:`HttpError(exit_code=4)` via
    :func:`transport.get`, which :func:`main_wrapper` surfaces as a
    structured ``service=seerr op=discover-movies status=<code>``
    stderr line; the operator's diagnostic tools keep working
    unchanged.

    Authentication is handled transparently by the transport layer
    (``X-Api-Key`` header per REQ-2 AC3); this handler does not inspect
    or echo the credential.
    """
    params: dict[str, Any] = {}
    # ``sortBy`` defaults to ``popularity.desc`` because that is the
    # documented default value the operator wants; we forward it
    # unconditionally rather than gating on an
    # ``omit-when-default`` rule (US-3 AC6: the documented default
    # IS the value the operator wants).
    params["sortBy"] = getattr(args, "sort", None) or "popularity.desc"
    genre = getattr(args, "genre", None)
    if genre is not None:
        params["genre"] = genre
    # ``--language`` defaults to ``en-US`` (the documented default);
    # forward unconditionally for the same reason ``sortBy`` is
    # unconditional. Operators who want a different locale pass
    # ``--language`` explicitly.
    params["language"] = (
        getattr(args, "language", None) or "en-US"
    )
    page = getattr(args, "page", None)
    if page is not None:
        params["page"] = page
    payload = _get(
        DISCOVER_MOVIES_PATH,
        args,
        cfg,
        params=params,
        op="discover-movies",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_seerr_discover_movies``: nested ``mediaInfo.tmdbId``
    # is resolved via dot-path traversal in ``_row_from_mapping``.
    # Byte-identical literal to ``cmd_upcoming_movies`` /
    # ``cmd_trending`` because the per-row projection is the same
    # (same envelope, same item shape).
    columns = [
        "title",
        "mediaType",
        "releaseDate",
        "mediaInfo.tmdbId",
    ]
    return _emit(payload, args, columns=columns)


def cmd_discover_tv(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``discover-tv`` -- filterable TV browse against the general discover endpoint.

    ``GET /api/v1/discover/tv`` returns the paginated envelope
    ``{page, totalPages, totalResults, results: [...]}`` -- the same
    shape :func:`cmd_discover_movies` / :func:`cmd_upcoming_tv`
    consume -- so the renderer is a near-verbatim copy of
    :func:`_summary_seerr_discover_tv``. The media type is encoded in
    the path so no positional ``MEDIA_TYPE`` is accepted (matches the
    documented "keep it simple" surface for this endpoint).

    Four optional filters ride on the query string -- same shape and
    defaults as :func:`cmd_discover_movies`:

    * ``genre`` (TMDB genre id) -- only forwarded when ``--genre`` is
      set; ``int`` type so an unparseable value exits ``1`` at parse
      time before any HTTP request is issued.
    * ``sortBy`` -- always forwarded; defaults to ``popularity.desc``.
    * ``language`` (``ISO 639-1``) -- always forwarded; defaults to
      ``en-US``.
    * ``page`` -- only forwarded when ``--page`` is set.

    The upstream ``limit`` query parameter is intentionally NOT
    forwarded -- the discover endpoints page instead, and the
    universal ``--limit`` caps client-side row output only.

    Non-2xx responses raise :class:`HttpError(exit_code=4)` via
    :func:`transport.get`, which :func:`main_wrapper` surfaces as a
    structured ``service=seerr op=discover-tv status=<code>`` stderr
    line; the operator's diagnostic tools keep working unchanged.

    Authentication is handled transparently by the transport layer
    (``X-Api-Key`` header per REQ-2 AC3); this handler does not inspect
    or echo the credential.

    Structural twin of :func:`cmd_discover_movies` so future drift
    between the two discover commands fails the unit suite
    immediately.
    """
    params: dict[str, Any] = {}
    params["sortBy"] = getattr(args, "sort", None) or "popularity.desc"
    genre = getattr(args, "genre", None)
    if genre is not None:
        params["genre"] = genre
    params["language"] = (
        getattr(args, "language", None) or "en-US"
    )
    page = getattr(args, "page", None)
    if page is not None:
        params["page"] = page
    payload = _get(
        DISCOVER_TV_PATH,
        args,
        cfg,
        params=params,
        op="discover-tv",
    )
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_seerr_discover_tv``: nested ``mediaInfo.tmdbId`` is
    # resolved via dot-path traversal in ``_row_from_mapping``.
    # Byte-identical literal to ``cmd_discover_movies`` because the
    # per-row projection is the same (same envelope, same item shape).
    columns = [
        "title",
        "mediaType",
        "releaseDate",
        "mediaInfo.tmdbId",
    ]
    return _emit(payload, args, columns=columns)


def cmd_genres(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Seerr ``genres [MEDIA_TYPE]`` -- TMDB genre list as ``[{id, name}, ...]``.

    ``GET /api/v1/genres/<movie|tv>`` returns a bare list of genre
    documents ``[{id: int, name: str}, ...]`` (no envelope wrapping
    -- the canonical TMDB genre shape). The renderer is a
    near-verbatim projection via
    :func:`arr_cli.facade.output._summary_seerr_genres`; the
    per-row shape collapses to ``{id, name}`` so the default
    ``--human`` table is the documented ``Id | Name`` (US-4).

    No filters ride on the query string -- the endpoint is
    parameter-free on Seer (matching the historical Overseerr /
    Jellyseerr shape). The CLI surface therefore only exposes:

    * A positional ``MEDIA_TYPE`` with ``choices=("movie", "tv")``
      and ``default="movie"`` (matches :func:`cmd_trending`'s
      positional-with-default pattern).
    * The universal ``--verbose`` / ``--human`` flags from
      :func:`arr_cli.facade.cli_common.universal_parents`.

    Defensive ``media_type`` validation lives in
    :func:`seerr_genres` so the helper is reusable from non-CLI
    callers; this handler lets the facade :class:`ConfigError`
    propagate unchanged (exit code 1).

    Non-2xx responses raise :class:`HttpError(exit_code=4)` via
    :func:`transport.get`, which :func:`main_wrapper` surfaces as
    a structured
    ``service=seerr op=genres/<media_type> status=<code>`` stderr
    line; the operator's diagnostic tools keep working unchanged.

    Authentication is handled transparently by the transport
    layer (``X-Api-Key`` header per REQ-2 AC3); this handler
    does not inspect or echo the credential.
    """
    payload = seerr_genres(args.genres_type, args, cfg)
    # Tabular columns match the summary-shape keys emitted by
    # ``_summary_seerr_genres``: ``id`` and ``name`` are
    # top-level keys on every item, so no dot-path traversal is
    # needed. Mirrors the ``cmd_requests`` column-list pattern
    # (no nested objects in the summary shape).
    columns = ["id", "name"]
    return _emit(payload, args, columns=columns)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


#: Mapping of subcommand name -> handler. Used by the post-parser
#: dispatch step in :func:`main` so every command has a single,
#: auditable registration point. The names are the exact strings the
#: ``argparse`` subparser registers.
#:
#: Note: ``create-request`` is intentionally NOT in this table.
#: REQ-10 AC7 states "IF a future ``seerr create-request``
#: (``POST /api/v1/request``) is added in tier-2 THEN it SHALL be
#: guarded by a ``--confirm`` flag and SHALL NOT appear in MVP help
#: output". The MVP therefore omits the subcommand and the subparser
#: tree has no entry for it; the test suite asserts the absence.
_DISPATCH = {
    "requests": cmd_requests,
    "request-count": cmd_request_count,
    "search": cmd_search,
    "available": cmd_available,
    "user": cmd_user,
    "tv": cmd_tv,
    "movie": cmd_movie,
    "trending": cmd_trending,
    "upcoming-movies": cmd_upcoming_movies,
    "upcoming-tv": cmd_upcoming_tv,
    "discover-movies": cmd_discover_movies,
    "discover-tv": cmd_discover_tv,
    "genres": cmd_genres,
}


def _dispatch(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Invoke the handler selected by ``args.command`` and return its exit code."""
    handler = _DISPATCH.get(args.command)
    if handler is None:
        # Defensive: argparse normally rejects unknown subcommands at
        # parse time; this branch covers a future bug where the
        # dispatch table falls out of sync with the subparser list.
        raise ConfigError(
            SERVICE_NAME,
            "dispatch",
            f"{SERVICE_NAME}: unknown command {args.command!r}",
        )
    return handler(args, cfg)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_seerr_parser() -> argparse.ArgumentParser:
    """Build the full argparse parser for the ``seerr`` CLI.

    The returned parser already includes the universal flag set
    (registered by :func:`arr_cli.facade.cli_common.build_parser`) and
    the six Seerr subcommands. Exposed for tests so they can parse
    arguments without going through the console-script entry point.
    """
    parser = build_parser(
        prog=SERVICE_NAME,
        description=(
            "Read-only CLI for Seer (the unified Overseerr + "
            "Jellyseerr fork). Thirteen commands expose the "
            "household request queue, request summary counts, "
            "multi-source search, what's already available in "
            "the library, the current authenticated user, "
            "per-show TV details, per-movie details "
            "(optionally with Rotten Tomatoes ratings), the "
            "live trending-discover feed, upcoming movie "
            "releases / TV premieres, the general "
            "discover-by-genre / sort / language browse, "
            "and the TMDB genre list (for mapping a "
            "friendly genre name to the integer id the "
            "discover filters expect)."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
        help="Seerr subcommand (see below)",
    )

    subparsers.add_parser(
        "requests",
        help="list media requests (GET /api/v1/request)",
        parents=universal_parents(),
        add_help=False,
    )

    subparsers.add_parser(
        "request-count",
        help="summary counts (GET /api/v1/request/count)",
        parents=universal_parents(),
        add_help=False,
    )

    search = subparsers.add_parser(
        "search",
        help=(
            "multi-source search by query "
            "(GET /api/v1/search?query=...)"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    search.add_argument(
        "query",
        nargs=argparse.OPTIONAL,
        default="",
        metavar="QUERY",
        help="search query (percent-encoded before being sent)",
    )

    available = subparsers.add_parser(
        "available",
        help=(
            "list media already available in the library, "
            "optionally filtered by title-substring query "
            "(GET /api/v1/media?filter=available&take=1000)"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    available.add_argument(
        "query",
        nargs=argparse.OPTIONAL,
        default="",
        metavar="QUERY",
        help=(
            "optional title-substring filter "
            "(applied client-side after the fetch)"
        ),
    )

    subparsers.add_parser(
        "user",
        help=(
            "fetch the current authenticated user "
            "(auth self-check; GET /api/v1/auth/me)"
        ),
        parents=universal_parents(),
        add_help=False,
    )

    tv = subparsers.add_parser(
        "tv",
        help=(
            "fetch TV show details by id, optionally with Rotten "
            "Tomatoes ratings (GET /api/v1/tv/<id>?language=...; "
            "--ratings adds /api/v1/tv/<id>/ratings)"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    tv.add_argument(
        "id",
        metavar="ID",
        help=(
            "TMDB/TVDB TV show id (e.g. 57243 for Doctor Who)"
        ),
    )
    tv.add_argument(
        "--ratings",
        action="store_true",
        help=(
            "also fetch Rotten Tomatoes critic + audience scores "
            "(GET /api/v1/tv/<id>/ratings) and merge them into the "
            "default summary"
        ),
    )
    tv.add_argument(
        "--language",
        default=None,
        metavar="LANG",
        help=(
            "ISO 639-1 language code forwarded as the "
            "?language=<LANG> query parameter on both endpoints"
        ),
    )

    movie = subparsers.add_parser(
        "movie",
        help=(
            "fetch movie details by id, optionally with Rotten "
            "Tomatoes ratings (GET /api/v1/movie/<id>?language=...; "
            "--ratings adds /api/v1/movie/<id>/ratings)"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    movie.add_argument(
        "id",
        metavar="ID",
        help=(
            "TMDB movie id (e.g. 603 for The Matrix)"
        ),
    )
    movie.add_argument(
        "--ratings",
        action="store_true",
        help=(
            "also fetch Rotten Tomatoes critic + audience scores "
            "(GET /api/v1/movie/<id>/ratings) and merge them into "
            "the default summary"
        ),
    )
    movie.add_argument(
        "--language",
        default=None,
        metavar="LANG",
        help=(
            "ISO 639-1 language code forwarded as the "
            "?language=<LANG> query parameter on both endpoints"
        ),
    )

    trending = subparsers.add_parser(
        "trending",
        help=(
            "list trending movies/TV "
            "(GET /api/v1/discover/trending?timeWindow=week"
            "[&mediaType=...][&language=...])"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    trending.add_argument(
        "media_type",
        nargs=argparse.OPTIONAL,
        default=None,
        choices=("movie", "tv"),
        metavar="MEDIA_TYPE",
        help=(
            "optional media-type filter "
            "(movie or tv; omit = all media types)"
        ),
    )
    trending.add_argument(
        "time_window",
        nargs=argparse.OPTIONAL,
        default="week",
        choices=("day", "week"),
        metavar="TIME_WINDOW",
        help=(
            "optional time-window filter "
            "(day or week; default week)"
        ),
    )
    trending.add_argument(
        "--language",
        default=None,
        metavar="LANG",
        help=(
            "ISO 639-1 language code forwarded as the "
            "?language=<LANG> query parameter"
        ),
    )

    upcoming_movies = subparsers.add_parser(
        "upcoming-movies",
        help=(
            "list upcoming movie releases "
            "(GET /api/v1/discover/movies/upcoming"
            "[?page=<N>][&language=<LANG>])"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    upcoming_movies.add_argument(
        "--page",
        type=int,
        default=None,
        metavar="N",
        help=(
            "page number forwarded as the ?page=<N> query "
            "parameter (omit = first page)"
        ),
    )
    upcoming_movies.add_argument(
        "--language",
        default=None,
        metavar="LANG",
        help=(
            "ISO 639-1 language code forwarded as the "
            "?language=<LANG> query parameter"
        ),
    )

    upcoming_tv = subparsers.add_parser(
        "upcoming-tv",
        help=(
            "list upcoming TV premieres "
            "(GET /api/v1/discover/tv/upcoming"
            "[?page=<N>][&language=<LANG>])"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    upcoming_tv.add_argument(
        "--page",
        type=int,
        default=None,
        metavar="N",
        help=(
            "page number forwarded as the ?page=<N> query "
            "parameter (omit = first page)"
        ),
    )
    upcoming_tv.add_argument(
        "--language",
        default=None,
        metavar="LANG",
        help=(
            "ISO 639-1 language code forwarded as the "
            "?language=<LANG> query parameter"
        ),
    )

    discover_movies = subparsers.add_parser(
        "discover-movies",
        help=(
            "filterable movie browse against the general "
            "discover endpoint "
            "(GET /api/v1/discover/movies"
            "?genre=<id>&sortBy=<sortBy>&language=<LANG>&page=<N>)"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    discover_movies.add_argument(
        "--genre",
        type=int,
        default=None,
        metavar="ID",
        help=(
            "TMDB genre id forwarded as the ?genre=<ID> query "
            "parameter (omit = no genre filter)"
        ),
    )
    discover_movies.add_argument(
        "--sort",
        default="popularity.desc",
        metavar="SORT_BY",
        help=(
            "sort key forwarded as the ?sortBy=<SORT_BY> query "
            "parameter (default popularity.desc)"
        ),
    )
    discover_movies.add_argument(
        "--language",
        default="en-US",
        metavar="LANG",
        help=(
            "ISO 639-1 language code forwarded as the "
            "?language=<LANG> query parameter (default en-US)"
        ),
    )
    discover_movies.add_argument(
        "--page",
        type=int,
        default=None,
        metavar="N",
        help=(
            "page number forwarded as the ?page=<N> query "
            "parameter (omit = first page)"
        ),
    )

    discover_tv = subparsers.add_parser(
        "discover-tv",
        help=(
            "filterable TV browse against the general "
            "discover endpoint "
            "(GET /api/v1/discover/tv"
            "?genre=<id>&sortBy=<sortBy>&language=<LANG>&page=<N>)"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    discover_tv.add_argument(
        "--genre",
        type=int,
        default=None,
        metavar="ID",
        help=(
            "TMDB genre id forwarded as the ?genre=<ID> query "
            "parameter (omit = no genre filter)"
        ),
    )
    discover_tv.add_argument(
        "--sort",
        default="popularity.desc",
        metavar="SORT_BY",
        help=(
            "sort key forwarded as the ?sortBy=<SORT_BY> query "
            "parameter (default popularity.desc)"
        ),
    )
    discover_tv.add_argument(
        "--language",
        default="en-US",
        metavar="LANG",
        help=(
            "ISO 639-1 language code forwarded as the "
            "?language=<LANG> query parameter (default en-US)"
        ),
    )
    discover_tv.add_argument(
        "--page",
        type=int,
        default=None,
        metavar="N",
        help=(
            "page number forwarded as the ?page=<N> query "
            "parameter (omit = first page)"
        ),
    )

    genres = subparsers.add_parser(
        "genres",
        help=(
            "list TMDB genres as [{id, name}, ...] "
            "(GET /api/v1/genres/<movie|tv>) so the operator "
            "can look up the integer id expected by "
            "discover-movies --genre / discover-tv --genre"
        ),
        parents=universal_parents(),
        add_help=False,
    )
    genres.add_argument(
        "genres_type",
        nargs=argparse.OPTIONAL,
        default="movie",
        choices=("movie", "tv"),
        metavar="MEDIA_TYPE",
        help=(
            "optional media-type filter "
            "(movie or tv; default movie). "
            "Argparse rejects anything else with exit code 2."
        ),
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point for the ``seerr`` executable.

    Wires the per-service subparser tree to :func:`main_wrapper` so
    config loading, debug/quiet flag handling, and ArrError -> stderr
    + exit-code mapping all happen in one place.
    """
    parser = build_seerr_parser()
    return main_wrapper(
        SERVICE_NAME,
        _dispatch,
        parser=parser,
        argv=argv,
    )