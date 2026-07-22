"""Optional retry layer for the arr-cli facade (task 5).

The retry policy is intentionally opt-in (REQ NFR-Reliability): the
default ``ServiceConfig.retry`` is ``0`` so a single, fast, deterministic
HTTP attempt is the norm. Operators flip it on per-invocation via
``--retry N`` and combine it with ``--deadline`` (or
``LILY_DEADLINE`` / ``cfg.deadline``) when the service is flaky.

Only network-class errors are retried (REQ NFR-Reliability). Auth,
HTTP-status, and parse failures bubble up immediately:

* :class:`arr_cli.facade.errors.NetworkError` -- DNS failure, connection
  refused, TLS error, timeout. These can succeed on the next attempt
  when the failure is transient.
* :class:`arr_cli.facade.errors.AuthError`,
  :class:`arr_cli.facade.errors.HttpError`,
  :class:`arr_cli.facade.errors.ParseError` -- the server has
  authoritatively answered (or our request was malformed); retrying
  without changing input would burn budget for no signal.

The backoff schedule is exponential with jitter:

* ``base = 0.5s``
* ``delay = base * (2 ** (attempt - 1))`` -- so attempt 1 sleeps 0s (no
  wait before the first try), attempt 2 sleeps up to ``base`` seconds,
  attempt 3 sleeps up to ``2 * base``, and so on.
* Jitter adds ``random.uniform(0, 0.25)`` seconds on top of the
  exponential delay to spread retries across instances.

The wall-clock cap is absolute: if ``time.monotonic() - start > deadline``
after a failure, the loop exits without further retries. ``deadline=None``
disables the cap so the policy is bounded only by ``attempts``.
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable

from arr_cli.facade.errors import (
    AuthError,
    HttpError,
    NetworkError,
    ParseError,
)

__all__ = [
    "with_retry",
    "DEFAULT_BASE_DELAY",
    "DEFAULT_JITTER_RANGE",
]


#: Base delay (seconds) for the exponential backoff. Attempt N waits
#: up to ``DEFAULT_BASE_DELAY * 2 ** (N - 2)`` (attempt 2 -> 0.5s,
#: attempt 3 -> 1.0s, attempt 4 -> 2.0s, ...).
DEFAULT_BASE_DELAY: float = 0.5

#: Uniform jitter window (seconds) added on top of the exponential
#: delay. ``random.uniform(0, DEFAULT_JITTER_RANGE)`` is sampled on
#: each retry so a fleet of CLIs hitting the same flaky service
#: doesn't synchronise its retries.
DEFAULT_JITTER_RANGE: float = 0.25


def _is_retryable(exc: BaseException) -> bool:
    """Return True iff ``exc`` is a network-class failure worth retrying.

    Mirrors REQ NFR-Reliability: only :class:`NetworkError` retries;
    auth, HTTP-status, and parse failures are surfaced verbatim so the
    caller can act on a stable signal.
    """
    return isinstance(exc, NetworkError) and not isinstance(
        exc, (AuthError, HttpError, ParseError)
    )


def _compute_delay(attempt: int, *, base: float, jitter: float) -> float:
    """Return the sleep duration before retry attempt ``attempt``.

    ``attempt`` is 1-indexed in the user-facing sense ("attempt 1" is
    the first try, "attempt 2" is the first retry). The schedule is:

    * ``attempt == 1`` -> ``0`` (no wait before the very first call).
    * ``attempt >= 2`` -> ``base * 2 ** (attempt - 2)`` plus uniform
      jitter in ``[0, jitter)``.

    Exposed for tests; not part of the public runtime contract.
    """
    if attempt <= 1:
        return 0.0
    return base * (2 ** (attempt - 2)) + random.uniform(0, jitter)


def with_retry(
    fn: Callable[[], Any],
    *,
    attempts: int,
    deadline: float | None,
) -> Any:
    """Run ``fn`` with optional retry on transient network failures.

    Parameters
    ----------
    fn:
        Zero-argument callable that performs one HTTP request and
        returns the parsed payload, or raises an
        :class:`ArrError` subclass.
    attempts:
        Maximum number of total attempts (including the first one).
        ``attempts == 0`` is rejected as nonsensical; ``attempts == 1``
        means "single try, no retries" (the default behaviour, also
        equivalent to skipping the retry layer entirely). ``attempts ==
        3`` means up to two retries after the initial failure.
    deadline:
        Absolute wall-clock cap (seconds). ``None`` disables the cap
        and the loop is bounded only by ``attempts``. Once
        ``time.monotonic() - start`` exceeds ``deadline`` after a
        retryable failure, the most recent
        :class:`NetworkError` is re-raised with the attempt count
        added to its message.

    Returns
    -------
    Any
        The value returned by the first successful ``fn()`` invocation.

    Raises
    ------
    NetworkError
        Re-raised with the attempt count appended to ``message`` when
        all attempts fail. ``__cause__`` is preserved as the last
        underlying exception.
    AuthError, HttpError, ParseError, ValueError
        Any non-network exception propagates immediately; these
        failures are deterministic and a retry would only burn
        budget.
    """
    if not isinstance(attempts, int):
        raise TypeError(
            f"attempts must be an int; got {type(attempts).__name__}"
        )
    if attempts < 1:
        raise ValueError(
            f"attempts must be >= 1 (got {attempts}); use 1 for a "
            "single try with no retries"
        )

    start = time.monotonic()
    last_network_error: NetworkError | None = None
    # Track the actual attempt count so the post-loop message
    # reports "after N attempts" rather than the original budget
    # (which can be misleading when the deadline cap aborts early).
    attempts_made = 0

    for attempt in range(1, attempts + 1):
        attempts_made = attempt
        try:
            return fn()
        except Exception as exc:
            if not _is_retryable(exc):
                # Non-retryable: bubble up verbatim. Auth / HTTP /
                # parse failures are deterministic signals that a
                # retry would only delay.
                raise

            last_network_error = exc

            if attempt >= attempts:
                # Out of attempts; break out of the loop so the
                # post-loop re-raise path runs exactly once with the
                # final exception.
                break

            if deadline is not None and (
                time.monotonic() - start > deadline
            ):
                # Deadline blown: stop retrying. The structured
                # message will carry the attempt count so operators
                # can see why we gave up.
                break

            delay = _compute_delay(
                attempt + 1,
                base=DEFAULT_BASE_DELAY,
                jitter=DEFAULT_JITTER_RANGE,
            )
            if delay > 0:
                time.sleep(delay)

    # All attempts exhausted (or deadline blown mid-loop). Re-raise
    # the last network error with the attempt count appended so the
    # stderr line names how many tries we burned.
    assert last_network_error is not None  # guarded by the for loop
    final_exc = last_network_error
    # Defensive: ``NetworkError`` always carries a URL; if a future
    # subclass forgets, surface a useful message rather than crashing
    # the CLI on a missing attribute.
    try:
        url_hint = f"url={final_exc.url} "
    except AttributeError:
        url_hint = ""
    new_message = (
        f"{final_exc.message} (after {attempts_made} attempts, "
        f"deadline={'unset' if deadline is None else f'{deadline:.2f}s'} "
        f"{url_hint.strip()})"
    ).rstrip()
    # Strip trailing whitespace when there is no URL hint.
    new_message = " ".join(new_message.split())
    raise NetworkError(
        final_exc.service,
        final_exc.op,
        new_message,
        url=getattr(final_exc, "url", ""),
    ) from final_exc