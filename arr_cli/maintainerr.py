"""Maintainerr CLI (``arr_cli.maintainerr``) -- task 11.

This module is the Maintainerr entry point for the ``arr-cli`` MVP. It
exposes three read-only commands against a live Maintainerr instance:

* ``pending`` -- ``GET /api/collections/overlay-data`` (REQ-9 AC2)
* ``storage`` -- ``GET /api/storage-metrics``              (REQ-9 AC3)
* ``health``  -- ``GET /api/health/ready``                 (REQ-9 AC4)

Per the MVP design, every command is a thin wrapper that:

1. Builds the Maintainerr path
2. Calls :func:`arr_cli.facade.transport.get` to fetch the JSON
3. Renders the payload via :func:`arr_cli.facade.output.emit` (JSON
   pass-through by default, tabular ``--human`` rendering when
   requested)
4. Returns ``0`` on success or reraises the documented
   :class:`ArrError` subclass so :func:`main_wrapper` can translate
   the error into the structured ``service=...`` stderr line and the
   documented exit code.

Maintainerr-specific behaviour (REQ-9 AC1, REQ-9 AC6):

* Auth is OPTIONAL by default. The transport layer's
  :func:`arr_cli.facade.transport._inject_auth` only adds an
  ``Authorization``-style header when ``cfg.maintainerr.auth_enabled``
  is True; otherwise the request goes out with no auth header at all
  (the documented Maintainerr default).
* When ``auth_enabled`` is False, :func:`main` emits a one-line
  stderr warning via :func:`arr_cli.facade.cli_common.warn_once` --
  the warning is suppressed under ``--quiet`` (REQ-9 AC1).
* On a 401/403 from a Maintainerr endpoint the transport layer
  prepends the documented guidance
  ``maintainerr: 401/403 received -- set auth.enabled=true in
  arr.conf and restart`` to the :class:`AuthError` message (REQ-9
  AC6). This logic lives in the transport layer (per task 4) so
  individual command handlers do not need to special-case it.

The MVP intentionally does NOT implement a ``rules`` command. REQ-9
AC5 explicitly states "the system SHALL NOT silently assume the
endpoint exists"; the design reserves the slot for tier-2 work after
verifying the live ``/api/swagger`` against the operator's instance.

Following the design contract this module contains no hardcoded URL
or credential; it reads configuration via :class:`ServiceConfig` and
delegates transport / auth / output to the facade. The console-script
entry point declared in ``pyproject.toml`` (``maintainerr = \
"arr_cli.maintainerr:main"``) calls :func:`main` which builds the
argparse subparser tree and delegates to the universal
:func:`arr_cli.facade.cli_common.main_wrapper` for config loading and
error mapping.
"""

from __future__ import annotations

import argparse
from typing import Any, Sequence

from arr_cli.facade import output, transport
from arr_cli.facade.cli_common import (
    build_parser,
    main_wrapper,
    universal_parents,
    warn_once,
)
from arr_cli.facade.config import ServiceConfig
from arr_cli.facade.errors import ConfigError

__all__ = [
    "main",
    "build_maintainerr_parser",
    # Command handlers are exposed for tests; they are not part of the
    # public CLI surface but unit tests use them to verify routing in
    # isolation from the argparse layer.
    "cmd_pending",
    "cmd_storage",
    "cmd_health",
    # The auth-disabled warning text is exported so tests can assert
    # the exact line surfaced to stderr (and so a future tier-2
    # command can reuse it without copy-paste drift).
    "AUTH_DISABLED_WARNING",
]


#: Service identifier used by transport.get / output.emit.
#: Centralised so a future rename touches one constant.
SERVICE_NAME = "maintainerr"


#: Canonical auth-disabled warning text per REQ-9 AC1. The full line
#: surfaced to stderr is ``maintainerr: <AUTH_DISABLED_WARNING>``.
#: Exposed at module level so the test suite can compare against the
#: exact string and so a future tier-2 command (e.g. ``rules``) can
#: reuse the same wording without copy-paste drift.
AUTH_DISABLED_WARNING = (
    "auth disabled; ensure this CLI is reachable only on a "
    "trusted/private network"
)


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
    "CLI overrides config" policy applies uniformly (REQ-5 AC3).
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


def _maybe_warn_auth_disabled(
    args: argparse.Namespace, cfg: ServiceConfig
) -> None:
    """Emit the REQ-9 AC1 auth-disabled warning when appropriate.

    Per REQ-9 AC1, the warning fires exactly once per invocation when:

    * ``cfg.maintainerr.auth_enabled`` is False (the documented default)
    * ``args.quiet`` is False (the operator did not opt out)

    The dedupe is delegated to :func:`cli_common.warn_once` so
    multiple call sites within a single invocation do not double-print
    the line. The set is reset by :func:`main_wrapper` at the start
    of every invocation so a new CLI run can fire the warning again
    (REQ-9 AC1: "the warn-once set must reset each invocation").
    """
    auth = cfg.maintainerr
    if auth is None:
        # Section absent: cli_common.main_wrapper already raises a
        # structured ConfigError before any handler runs, so this
        # branch is defensive only.
        return
    if not auth.auth_enabled:
        warn_once(
            SERVICE_NAME,
            AUTH_DISABLED_WARNING,
            quiet=bool(getattr(args, "quiet", False)),
        )


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_pending(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Maintainerr ``pending`` -- collection overlay data (REQ-9 AC2).

    Returns the per-collection overlay information Maintainerr uses
    to decide what is about to be cleaned up. The endpoint returns a
    JSON object whose shape is service-defined; the ``--human``
    renderer picks collection title, media count, and deletion date.
    """
    payload = _get(
        "/api/collections/overlay-data",
        args,
        cfg,
        op="pending",
    )
    columns = [
        "title",
        "mediaCount",
        "deleteAfterDays",
        "isOnHold",
    ]
    return _emit(payload, args, columns=columns)


def cmd_storage(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Maintainerr ``storage`` -- storage metrics (REQ-9 AC3).

    Returns the per-volume / per-library storage metrics Maintainerr
    reports. Like ``pending`` the response shape is service-defined;
    we surface the most common keys and let ``--human`` truncate the
    rest.
    """
    payload = _get(
        "/api/storage-metrics",
        args,
        cfg,
        op="storage",
    )
    columns = [
        "name",
        "total",
        "used",
        "free",
        "percentUsed",
    ]
    return _emit(payload, args, columns=columns)


def cmd_health(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Maintainerr ``health`` -- readiness probe (REQ-9 AC4).

    The upstream endpoint returns an object payload
    ``{"status": "ok"|..., "uptimeSeconds": <int>,
    "database": "ok"|..., "timestamp": "<ISO-8601>"}`` rather than
    a bare boolean; the facade passes it through verbatim in
    default, ``--verbose``, and ``--human`` modes (exit ``0`` when
    ``status == "ok"``, otherwise still exit ``0`` -- the operator
    reads the value). REQ-3 AC3 requires that JSON-only endpoints
    still render cleanly under ``--human`` (with indentation rather
    than a crash); :func:`output.emit` handles that case in the
    output module.
    """
    payload = _get(
        "/api/health/ready",
        args,
        cfg,
        op="health",
    )
    # The health payload is a scalar; ``--human`` will render it via
    # the output module's scalar branch (REQ-3 AC3). No tabular
    # columns apply so we pass ``None``.
    return _emit(payload, args, columns=None)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


#: Mapping of subcommand name -> handler. Used by the post-parser
#: dispatch step in :func:`main` so every command has a single,
#: auditable registration point. The names are the exact strings the
#: ``argparse`` subparser registers.
#:
#: Note: ``rules`` is intentionally NOT in this table. REQ-9 AC5
#: states the system SHALL NOT silently assume the ``/api/rules``
#: endpoint exists; the design reserves the slot for tier-2 work
#: after verifying the live ``/api/swagger`` against the operator's
#: instance.
_DISPATCH = {
    "pending": cmd_pending,
    "storage": cmd_storage,
    "health": cmd_health,
}


def _dispatch(args: argparse.Namespace, cfg: ServiceConfig) -> int:
    """Invoke the handler selected by ``args.command`` and return its exit code.

    Also fires the REQ-9 AC1 auth-disabled warning before delegating
    to the handler so the warning is always the first line an
    operator sees when running with ``auth_enabled = False``.
    """
    _maybe_warn_auth_disabled(args, cfg)

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


def build_maintainerr_parser() -> argparse.ArgumentParser:
    """Build the full argparse parser for the ``maintainerr`` CLI.

    The returned parser already includes the universal flag set
    (registered by :func:`arr_cli.facade.cli_common.build_parser`) and
    the three Maintainerr subcommands. Exposed for tests so they can
    parse arguments without going through the console-script entry
    point.
    """
    parser = build_parser(
        prog=SERVICE_NAME,
        description=(
            "Read-only CLI for Maintainerr. Three commands expose "
            "pending collection overlays, storage metrics, and the "
            "service's readiness probe. Auth is disabled by default; "
            "a one-line stderr warning is emitted on each invocation "
            "unless --quiet is passed."
        ),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND",
        help="Maintainerr subcommand (see below)",
    )

    subparsers.add_parser(
        "pending",
        help=(
            "list pending collection overlays "
            "(GET /api/collections/overlay-data)"
        ),
        parents=universal_parents(),
        add_help=False,
    )

    subparsers.add_parser(
        "storage",
        help="list per-volume storage metrics (GET /api/storage-metrics)",
        parents=universal_parents(),
        add_help=False,
    )

    subparsers.add_parser(
        "health",
        help="readiness probe (GET /api/health/ready)",
        parents=universal_parents(),
        add_help=False,
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point for the ``maintainerr`` executable.

    Wires the per-service subparser tree to :func:`main_wrapper` so
    config loading, debug/quiet flag handling, and ArrError -> stderr
    + exit-code mapping all happen in one place. The auth-disabled
    warning is fired inside :func:`_dispatch` so it runs only after
    the config has loaded successfully (otherwise the operator
    would see the warning even when the config is broken).
    """
    parser = build_maintainerr_parser()
    return main_wrapper(
        SERVICE_NAME,
        _dispatch,
        parser=parser,
        argv=argv,
    )
