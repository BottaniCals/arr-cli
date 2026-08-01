"""Shared CLI parser and ``main_wrapper`` for the arr-cli facade (task 7).

This module is the entry-point surface every per-service CLI imports.
It owns three responsibilities:

* :func:`build_parser` -- construct a uniformly-configured
  :class:`argparse.ArgumentParser` with the documented universal flag
  set (``--config``, ``--debug``, ``--quiet``, ``--human``/``-h``,
  ``--connect-timeout``, ``--read-timeout``, ``--retry``,
  ``--deadline``, ``--limit``).
* :func:`main_wrapper` -- a closure factory that wraps a per-service
  handler, loads the config via :func:`arr_cli.facade.config.load_config`,
  runs the handler, and translates any
  :class:`arr_cli.facade.errors.ArrError` (or unexpected exception)
  into the documented structured stderr line and exit code.
* :func:`warn_once` -- a one-shot stderr helper for advisory messages
  such as the Maintainerr auth-disabled warning (REQ-9 AC1). The
  per-invocation reset is exposed via :func:`reset_warnings` so test
  code (and any future daemon-style usage) can clear the dedupe set
  between invocations.

The error-handling policy follows REQ-4 AC3 and REQ-4 AC5:

* Default stderr line: ``service=<svc> op=<op> status=<status>
  message=<message>`` (status is omitted when the exception class
  does not carry one).
* ``--debug`` additionally prints ``traceback.format_exc()`` to
  stderr so operators can see the full call chain without polluting
  the default operator experience.
* Unknown / unexpected exceptions produce a single
  ``unexpected error in <service>: <class>: <repr>`` line by default
  and the same plus a traceback under ``--debug``; the exit code is
  ``1`` (a generic "something went wrong" signal that does not
  collide with the documented 1..5 map).

The traceback policy is intentionally local to this module: the
underlying error classes do not need to know whether the CLI was
launched with ``--debug`` -- they only carry their structured
message and exit code.

The ``main_wrapper`` closure is deliberately small so each per-service
``main()`` only has to assemble an :mod:`argparse` subparser tree and
delegate to a handler. This is the seam the design document
identifies as the place where adding a sixth service or tier-2
mutations becomes a localized change (design.md "Components and
Interfaces / arr_facade.cli_common").
"""

from __future__ import annotations

import argparse
import sys
import traceback
from typing import Callable, Sequence

from arr_cli.facade.config import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    DEFAULT_RETRY,
    ServiceConfig,
    load_config,
)
from arr_cli.facade.errors import ArrError, ConfigError
from arr_cli.facade.output import DEFAULT_LIMIT

__all__ = [
    "build_parser",
    "main_wrapper",
    "warn_once",
    "reset_warnings",
    "universal_parents",
]


#: Module-level dedupe set for :func:`warn_once`. Tracked by
#: ``(service, message)`` tuples so the same warning printed from
#: two different services does not collapse into a single line.
#: Reset via :func:`reset_warnings` on every :func:`main_wrapper`
#: invocation so the per-process "once" guarantee holds across
#: process lifetimes (REQ-9 AC1).
_WARNED: set[tuple[str, str]] = set()


def reset_warnings() -> None:
    """Clear the dedupe set used by :func:`warn_once`.

    Called by :func:`main_wrapper` at the start of every invocation
    so the "fire once per process" guarantee holds across process
    lifetimes. Test code can call this directly to deterministically
    re-arm the warning path.
    """
    _WARNED.clear()


def warn_once(service: str, message: str, *, quiet: bool) -> None:
    """Print ``message`` to stderr once per (service, message) pair.

    Parameters
    ----------
    service:
        Short service identifier (``"maintainerr"``, ...) used as the
        dedupe key so two services sharing a message do not collide.
    message:
        The advisory line to write to stderr. Prefixed with
        ``"<service>: "`` for grep-ability.
    quiet:
        When True the warning is suppressed entirely (REQ-9 AC1:
        "the warning SHALL NOT appear if --quiet is passed").

    Notes
    -----
    The dedupe set is module-level so an invocation that calls
    :func:`warn_once` from multiple call sites (e.g. a subcommand
    plus its ``main``) does not double-print. :func:`reset_warnings`
    clears it for the next :func:`main_wrapper` invocation.
    """
    if quiet:
        return
    key = (service, message)
    if key in _WARNED:
        return
    _WARNED.add(key)
    prefixed = f"{service}: {message}" if service else message
    print(prefixed, file=sys.stderr)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_universal_parent() -> argparse.ArgumentParser:
    """Build an argparse parent carrying the universal flag set.

    The returned parser is intended to be passed to both the
    top-level ``argparse.ArgumentParser`` of every CLI (via
    ``parents=[universal_parents()]``) and to every per-subcommand
    ``subparsers.add_parser(..., parents=[universal_parents()])``
    call. This is how the documented invocation
    ``<cli> <subcommand> --config <path>`` is recognised by argparse
    -- the universal flag set is registered on the subparser too, so
    it appears in every subcommand's ``--help`` listing and accepts
    both arg orders.

    The parent uses ``add_help=False``; ``--help`` is registered
    separately on the top-level parser so the ``-h`` short alias is
    free for ``--human`` (REQ-3 AC2). Subparsers do NOT
    re-register ``--help`` (argparse would complain about a duplicate
    action) -- they inherit the long form through the parent if and
    only if the parent had ``add_help=True``, which we deliberately
    disable here.

    Notes
    -----
    Returning a fresh parser per call is intentional: argparse
    mutates internal state on each ``add_argument`` call, so sharing
    an instance across multiple parents would attach actions to
    the same backing stores and surface as duplicate-action errors.
    """
    parent = argparse.ArgumentParser(
        add_help=False,
        allow_abbrev=False,
    )

    # Register ``--help`` (long-only) on the parent so subparsers
    # that consume this parent via ``parents=`` and disable their
    # auto-help still surface a listing. The short ``-h`` is
    # deliberately not used here: it is reserved for ``--human``
    # (REQ-3 AC2). On the top-level parser the explicit ``--help``
    # action is registered separately by ``build_parser`` so the
    # ``-h`` alias is unambiguously ``--human`` everywhere.
    parent.add_argument(
        "--help",
        action="help",
        help="show this help message and exit",
    )

    # ---- Config override -----------------------------------------------
    # ``default=argparse.SUPPRESS`` so a value parsed by the top-level
    # parser is not overwritten by the subparser's own default-lookup
    # step (argparse quirk: when the same action is shared via
    # ``parents=``, the subparser's default re-runs ``_get_values``
    # and would clobber a top-level value).
    parent.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help=(
            "override the canonical config path for this invocation "
            "(default: ~/.config/arr/arr.conf)"
        ),
    )

    # ---- Diagnostic / advisory toggles ---------------------------------
    parent.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=argparse.SUPPRESS,
        help=(
            "emit the full Python traceback and the redacted "
            "request/response pair to stderr on errors"
        ),
    )
    parent.add_argument(
        "--quiet",
        action=argparse.BooleanOptionalAction,
        default=argparse.SUPPRESS,
        help="suppress advisory stderr warnings (e.g. maintainerr auth disabled)",
    )

    # ---- Output formatting ---------------------------------------------
    # ``-h`` aliases ``--human`` per the design contract. argparse's
    # default short flag is also ``-h`` (mapped to ``--help``); the
    # ``-h`` short option here shadows that for the documented
    # ``--human`` use case. The long ``--help`` form remains
    # available on the top-level parser (registered separately) so
    # whichever subcommand a user is in, ``--help`` still works.
    parent.add_argument(
        "--human",
        "-h",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "render a tabular human-readable view instead of JSON "
            "(REQ-3 AC2); pagination is controlled by --limit"
        ),
    )
    parent.add_argument(
        "--verbose",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "emit the verbatim service JSON payload instead of the "
            "curated summary (default for size-to-summary commands)"
        ),
    )

    # ---- Transport / reliability ---------------------------------------
    parent.add_argument(
        "--connect-timeout",
        type=float,
        metavar="SECONDS",
        default=argparse.SUPPRESS,
        help=(f"connect timeout in seconds (default: {DEFAULT_CONNECT_TIMEOUT})"),
    )
    parent.add_argument(
        "--read-timeout",
        type=float,
        metavar="SECONDS",
        default=argparse.SUPPRESS,
        help=(f"read timeout in seconds (default: {DEFAULT_READ_TIMEOUT})"),
    )
    parent.add_argument(
        "--retry",
        type=int,
        metavar="N",
        default=argparse.SUPPRESS,
        help=(f"retry attempts on network-class errors (default: {DEFAULT_RETRY})"),
    )
    parent.add_argument(
        "--deadline",
        type=float,
        metavar="SECONDS",
        default=argparse.SUPPRESS,
        help=(
            "absolute wall-clock cap (seconds) for the retry layer (default: unbounded)"
        ),
    )

    # ---- Human-mode pagination -----------------------------------------
    parent.add_argument(
        "--limit",
        type=int,
        metavar="N",
        default=argparse.SUPPRESS,
        help=(f"row-count cap for --human pagination (default: {DEFAULT_LIMIT})"),
    )

    return parent


def universal_parents() -> list[argparse.ArgumentParser]:
    """Return a single-element ``parents=`` list for the universal flag set.

    Public wrapper so per-service modules can write
    ``subparsers.add_parser("now", ..., parents=universal_parents())``
    without importing the private ``_build_universal_parent``
    symbol. Called from cli_common's own ``build_parser`` too -- the
    one-call-shape keeps the two registration paths in sync.
    """
    return [_build_universal_parent()]


def build_parser(
    prog: str,
    description: str,
    epilog: str | None = None,
) -> argparse.ArgumentParser:
    """Build the universal argparse base for every arr-cli service CLI.

    The returned parser exposes the documented universal flags and is
    intended to be the parent of a per-service ``add_subparsers`` tree
    (each per-service ``main`` adds its own subcommands and then
    calls :func:`main_wrapper` for the actual config-load / dispatch
    step).

    Universal flag set (REQ-11 AC3, REQ-11 AC4, NFR-Usability):

    * ``--config <path>`` -- override the canonical config path for
      this invocation only (REQ-1 AC1).
    * ``--debug`` / ``--no-debug`` -- toggle the traceback path
      (REQ-4 AC5). Default ``--no-debug``.
    * ``--quiet`` / ``--no-quiet`` -- suppress advisory warnings
      (REQ-9 AC1). Default ``--no-quiet``.
    * ``--human`` / ``-h`` -- render a tabular readable view instead
      of JSON (REQ-3 AC2). The ``-h`` alias deliberately shadows
      argparse's default ``-h``/``--help`` short flag, so the long
      ``--help`` form remains available. This matches the design
      contract; users wanting the standard ``-h`` help can still use
      ``--help``.
    * ``--verbose`` -- emit the verbatim service JSON payload instead
      of the curated summary (default for size-to-summary commands).
      No short alias. The renderer priority chain is
      ``--human`` > ``--verbose`` > default summary (REQ-3 AC1-AC4,
      REQ-6 AC2).
    * ``--connect-timeout <float>`` -- per-call connect timeout
      (seconds). Default :data:`DEFAULT_CONNECT_TIMEOUT` (5.0).
    * ``--read-timeout <float>`` -- per-call read timeout (seconds).
      Default :data:`DEFAULT_READ_TIMEOUT` (30.0).
    * ``--retry <int>`` -- number of additional attempts on
      network-class errors (REQ NFR-Reliability). Default
      :data:`DEFAULT_RETRY` (0).
    * ``--deadline <float>`` -- absolute wall-clock cap (seconds) for
      the retry layer. Default ``None`` (unbounded).
    * ``--limit <int>`` -- row-count cap for ``--human`` pagination
      (REQ-3 AC2). Default :data:`DEFAULT_LIMIT` (20).

    Parameters
    ----------
    prog:
        The executable name to display in ``--help`` (typically the
        console-script name, e.g. ``"jellyfin"``).
    description:
        One-paragraph summary surfaced by ``--help``.
    epilog:
        Optional footer text rendered after the flag listing.

    Returns
    -------
    argparse.ArgumentParser
        A parser pre-loaded with the universal flag set but no
        subcommands -- the caller adds those.
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
        epilog=epilog,
        # ``add_help=False`` so we can register our own ``--help``
        # flag below; this frees the ``-h`` short alias for
        # ``--human`` (REQ-3 AC2). The long ``--help`` form is
        # inherited from the universal parent so it is registered
        # exactly once across the top-level parser and every
        # subparser (the subparsers additionally pass
        # ``add_help=False`` to suppress the default ``-h``/``--help``
        # action that would otherwise conflict with the parent's
        # ``--human`` short alias).
        add_help=False,
        parents=universal_parents(),
    )

    return parser


# ---------------------------------------------------------------------------
# main_wrapper
# ---------------------------------------------------------------------------


#: Signature of a per-service handler. Receives the parsed argparse
#: namespace and the loaded :class:`ServiceConfig`; returns the exit
#: code (an int) or raises an :class:`ArrError` (which
#: :func:`main_wrapper` translates into the documented stderr line
#: and exit code).
Handler = Callable[[argparse.Namespace, ServiceConfig], int]


def _emit_error_line(message: str) -> None:
    """Write ``message`` to stderr with a trailing newline.

    Centralised so every error path uses the same writer and a future
    change (e.g. switching to ``logging``) only has to touch one
    location.
    """
    print(message, file=sys.stderr)


def _handle_arr_error(exc: ArrError, *, debug: bool) -> int:
    """Format ``exc`` as the structured stderr line and return its exit code.

    When ``debug`` is True and ``exc.__cause__`` is set, the full
    traceback is appended below the structured line so operators can
    see the underlying call chain (REQ-4 AC5). The traceback is
    emitted with ``traceback.format_exc()`` which already includes
    the chained exception when ``raise X from Y`` was used.
    """
    _emit_error_line(str(exc))
    if debug and exc.__cause__ is not None:
        _emit_error_line(traceback.format_exc())
    return exc.exit_code


def _handle_unexpected_error(exc: Exception, *, service: str, debug: bool) -> int:
    """Translate an unexpected :class:`Exception` into a stderr line + exit code.

    The default path emits a single line so a consumer piping stderr
    into a log aggregator still gets something parseable. With
    ``--debug`` the full traceback follows the line.

    The exit code is ``1`` -- a generic "something went wrong"
    signal that does not collide with the documented 1..5 map (the
    only legitimate way to leave that map is via an
    :class:`ArrError` subclass).
    """
    _emit_error_line(f"unexpected error in {service}: {exc.__class__.__name__}: {exc}")
    if debug:
        _emit_error_line(traceback.format_exc())
    return 1


def main_wrapper(
    service: str,
    handler: Handler,
    *,
    parser: argparse.ArgumentParser | None = None,
    argv: Sequence[str] | None = None,
) -> int:
    """Wrap a per-service handler as a console-script-ready closure body.

    The returned ``int`` is the process exit code. The wrapper:

    1. Resets the :func:`warn_once` dedupe set so the per-invocation
       "once" guarantee holds across process lifetimes (REQ-9 AC1).
    2. Parses ``argv`` (defaults to ``sys.argv[1:]``) with
       :func:`build_parser` (or a caller-supplied parser).
    3. Calls :func:`load_config` with ``args.config`` and applies
       the universal timeout / retry / deadline flags by mutating
       a copy of the returned :class:`ServiceConfig`.
    4. Invokes ``handler(args, cfg)`` and returns its int exit code
       on success.
    5. On :class:`ArrError`: writes the structured
       ``service=... op=... status=... message=...`` line to stderr
       (REQ-4 AC3) and returns ``exc.exit_code``; under ``--debug``
       the full traceback follows.
    6. On any other :class:`Exception`: writes a one-line summary to
       stderr and returns ``1``; under ``--debug`` the full
       traceback follows.

    Parameters
    ----------
    service:
        Service identifier surfaced in the unexpected-error message
        and used as the namespace for :func:`warn_once` keys. Also
        used as the value of the per-service warning's ``service:``
        prefix.
    handler:
        Callable ``(args, cfg) -> int`` that does the per-service
        dispatch. The handler may itself raise an :class:`ArrError`
        which the wrapper translates.
    parser:
        Optional caller-built :class:`argparse.ArgumentParser` -- use
        this when the per-service module adds its own subparsers
        before delegating to the wrapper. ``None`` (the default)
        constructs a fresh parser via :func:`build_parser`.
    argv:
        Optional argument list. ``None`` (the default) reads
        ``sys.argv[1:]``. Tests pass an explicit list.

    Returns
    -------
    int
        Exit code suitable for ``sys.exit``.

    Notes
    -----
    The wrapper does NOT call ``sys.exit`` -- it returns the exit
    code so console-script entry points and unit tests can inspect
    it. ``pyproject.toml``'s console scripts wrap this return value
    with ``sys.exit(wrapper(...))`` (or equivalent) at the entry
    point.
    """
    reset_warnings()

    if parser is None:
        parser = build_parser(
            prog=service,
            description=f"{service} CLI (arr-cli facade)",
        )

    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        # ``argparse.parse_args`` calls ``sys.exit`` on a parse
        # failure (usage error). ``SystemExit.code`` is the exit
        # status argparse chose (typically 2 for usage errors);
        # we surface that *through the documented ArrError map*
        # instead of returning the raw exit code, so a malformed
        # invocation cannot collide with the documented
        # ``AuthError`` exit code (2). The usage hint was written
        # to stderr by argparse already; we follow up with the
        # structured ``service=config op=parse message=...`` line
        # so downstream consumers see the same shape as every
        # other error path (REQ-4 AC3, AGENTS.md §6).
        argparse_code = exc.code if isinstance(exc.code, int) else 2
        # Argparse normally uses 2 for usage errors; ``--help`` is
        # 0. We only translate the failure path (exit 2) so a
        # successful ``--help`` call still exits 0.
        if argparse_code == 0:
            return 0
        return _handle_arr_error(
            ConfigError(
                "config",
                "parse",
                f"argument parse error (argparse exit {argparse_code})",
            ),
            debug=False,
        )

    try:
        cfg = load_config(getattr(args, "config", None), env_overrides=True)
    except ArrError as exc:
        return _handle_arr_error(exc, debug=getattr(args, "debug", False))

    # Apply the CLI-side timeout / retry / deadline overrides on top
    # of the parsed config. ``ServiceConfig`` is frozen so we build a
    # new instance with the per-call values; the handler still
    # receives an immutable bundle so the "stateless per invocation"
    # invariant holds (REQ-5 AC1). The universal flag defaults are
    # re-applied here because the universal parent registers every
    # action with ``default=argparse.SUPPRESS`` (so a top-level
    # ``--retry 3`` is not clobbered by the subparser's default-lookup
    # step). When the operator omits the flag we fall back to the
    # module-level default constant so the behaviour matches the
    # pre-fix surface.
    connect_timeout = getattr(args, "connect_timeout", DEFAULT_CONNECT_TIMEOUT)
    read_timeout = getattr(args, "read_timeout", DEFAULT_READ_TIMEOUT)
    retry = getattr(args, "retry", DEFAULT_RETRY)
    deadline = getattr(args, "deadline", None)

    overrides_applied = (
        connect_timeout != cfg.connect_timeout
        or read_timeout != cfg.read_timeout
        or retry != cfg.retry
        or deadline != cfg.deadline
    )
    if overrides_applied:
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

    debug = getattr(args, "debug", False)

    try:
        result = handler(args, cfg)
    except ArrError as exc:
        return _handle_arr_error(exc, debug=debug)
    except Exception as exc:  # noqa: BLE001 - intentional catch-all
        return _handle_unexpected_error(exc, service=service, debug=debug)

    # ``handler`` may return None on success (some per-service
    # implementations just print and exit cleanly). Treat None as
    # exit code 0 -- the documented "successful exit" -- so callers
    # do not need to remember to return an int.
    if result is None:
        return 0
    try:
        return int(result)
    except (TypeError, ValueError):
        # Defensive: a handler that returns something non-int should
        # not crash the wrapper. Coerce to 0 on a non-numeric value
        # (anything else would be confusing).
        return 0
