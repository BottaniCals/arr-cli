"""Regression guard for the ``curated N-key`` claims in README §4.

The README's per-service command tables document the *curated summary
shape* each size-to-summary command emits: a key count plus the list of
key names. That documentation drifted away from the code more than once
(``radarr recent`` claimed 8 keys while the renderer emitted 9,
``seerr requests`` claimed 10 while the renderer emitted 11, and
``sonarr recent`` presented ``--human`` column tokens as if they were
summary keys), so this module pins the documented counts to the shapes
the renderers actually produce.

How the guard works
-------------------

* The **expected** key set comes from the renderer itself: every
  renderer is called with a representative payload and the *returned
  row* is inspected. No expected key list is hand-copied into this
  file -- a hand-copied list would drift in exactly the same way the
  README did.
* The **documented** side of the comparison is the integer
  ``curated N-key`` claim. That integer is the reliable signal: the
  README's parenthetical key lists carry nested annotations (e.g.
  ``runtime`` formatted as ``"<X>h <Y>m"``), so splitting them on
  commas would truncate the list and report spurious missing keys.
  The names are still read -- with a *balanced-parenthesis* splitter,
  never a naive comma split -- because the count alone cannot tell
  whether a row documents the top-level summary keys or the expanded
  leaf paths of a nested envelope.
* Rows that carry **no** key count (``sonarr recent`` before this
  change, ``maintainerr health``, ``jellyfin search``, ...) are
  skipped: there is nothing to compare.
* ``sonarr series`` (10 keys) and ``sonarr series <id>`` (9 keys) are
  two distinct commands with two distinct renderers. Conflating them
  would false-fail on whichever one does not match.

Counting convention
--------------------

Two renderers emit a nested object inside the summary row:

* ``jellyfin now`` documents the *expanded* leaf paths
  (``playing.type``, ``progress.position_ticks``, ...) -- 13 names.
* ``sonarr recent`` and ``radarr recent`` document the nested
  envelope as a *single* key (``series`` / ``episode`` / ``movie``).

The convention is therefore selected per row from the documented names
themselves: when every documented name is a top-level key of the
rendered row the row counts top-level keys, otherwise the row counts
the expanded leaf paths. Either way the number compared against the
README is derived from the renderer's output, never from a list typed
into this file.

The tests are hermetic: no HTTP, no live credentials, no new
dependencies.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

# Make the project importable regardless of the test runner's CWD.
_PROJ_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from arr_cli.facade.output import (  # noqa: E402 - sys.path tweak above
    _SUMMARY_RENDERERS,
)

#: Absolute path to the README the guard inspects.
README_PATH: Path = _PROJ_ROOT / "README.md"

#: Matches a command-table row's command cell, e.g. ``| `sonarr series <id>` |``.
_COMMAND_CELL_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|")

#: Matches the integer key-count claim, e.g. ``curated 11-key``.
_COUNT_CLAIM_RE = re.compile(r"curated (\d+)-key")

#: Matches the leading backticked key name of one parenthetical item.
_KEY_NAME_RE = re.compile(r"^\s*`([^`]+)`")


# ---------------------------------------------------------------------------
# README parsing
# ---------------------------------------------------------------------------


def _split_top_level(text: str) -> list[str]:
    """Split ``text`` on the commas that sit at parenthesis depth zero.

    A naive ``text.split(",")`` would cut inside the nested
    annotations the README uses (e.g. ``runtime`` formatted as
    ``"<X>h <Y>m"``), truncating the key list and reporting spurious
    missing keys. Tracking the parenthesis depth keeps every nested
    annotation attached to the item it belongs to.
    """
    items: list[str] = []
    current: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            items.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        items.append("".join(current))
    return items


def _first_balanced_parenthetical(text: str, start: int) -> str | None:
    """Return the content of the first balanced ``(...)`` at/after ``start``.

    Returns ``None`` when no balanced parenthetical follows ``start``.
    """
    try:
        open_at = text.index("(", start)
    except ValueError:
        return None
    depth = 0
    for index in range(open_at, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1 : index]
    return None


def _documented_key_names(notes: str, claim_end: int) -> list[str]:
    """Return the key names the README documents for a counted row.

    The names are the leading backticked token of each comma-separated
    item of the parenthetical that follows the ``curated N-key`` claim.
    An item that carries no backticked name (a bare annotation) is
    skipped rather than guessed at.
    """
    body = _first_balanced_parenthetical(notes, claim_end)
    if body is None:
        return []
    names: list[str] = []
    for item in _split_top_level(body):
        match = _KEY_NAME_RE.match(item)
        if match is not None:
            names.append(match.group(1))
    return names


def _readme_rows() -> list[tuple[str, int, list[str]]]:
    """Return ``(command, claimed_count, documented_names)`` per counted row.

    Only rows that carry an integer ``curated N-key`` claim are
    returned -- a row without a count has nothing to compare against
    the renderer.
    """
    rows: list[tuple[str, int, list[str]]] = []
    for line in README_PATH.read_text(encoding="utf-8").splitlines():
        cell = _COMMAND_CELL_RE.match(line)
        if cell is None:
            continue
        command = cell.group(1)
        if not re.match(r"^(jellyfin|radarr|sonarr|maintainerr|seerr)\b", command):
            continue
        claim = _COUNT_CLAIM_RE.search(line)
        if claim is None:
            continue
        names = _documented_key_names(line, claim.end())
        rows.append((command, int(claim.group(1)), names))
    return rows


# ---------------------------------------------------------------------------
# Renderer invocation
# ---------------------------------------------------------------------------

#: A representative payload per renderer dispatch key. The payloads are
#: deliberately minimal but must satisfy the renderers' own defensive
#: filters (e.g. ``jellyfin recent`` / ``jellyfin resume`` drop every
#: row whose ``Type`` is not ``Movie`` / ``Episode``), so the renderer
#: returns a non-empty row whose keys are the real summary shape.
_REPRESENTATIVE_PAYLOADS: dict[tuple[str, str], Any] = {
    ("jellyfin", "now"): [
        {
            "UserName": "operator",
            "DeviceName": "device",
            "Client": "client",
            "NowPlayingItem": {
                "Type": "Episode",
                "Name": "Episode",
                "SeriesName": "Series",
                "ParentIndexNumber": 1,
                "IndexNumber": 2,
            },
            "PlayState": {"PositionTicks": 100, "IsPaused": False},
            "LastActivityDate": "2026-01-01T00:00:00Z",
            "NowPlayingQueue": [{"Id": 1}],
            "TranscodingInfo": {"IsVideoDirect": True},
        }
    ],
    ("jellyfin", "recent"): [{"Type": "Movie"}],
    ("jellyfin", "resume"): [{"Type": "Episode"}],
    ("jellyfin", "latest"): [{"Name": "Item"}],
    ("jellyfin", "nextup"): [{"Name": "Episode"}],
    ("jellyfin", "search"): [{"Name": "Item"}],
    ("jellyfin", "item"): {"Name": "Item"},
    ("jellyfin", "favorites"): [{"Name": "Item"}],
    ("radarr", "calendar"): [{"title": "Movie"}],
    ("radarr", "wanted"): [{"title": "Movie"}],
    ("radarr", "queue"): [{"title": "Movie"}],
    ("radarr", "recent"): [{"movie": {"title": "Movie", "year": 2026}}],
    ("radarr", "lookup"): [{"title": "Movie"}],
    ("radarr", "movie"): [{"title": "Movie"}],
    ("radarr", "movie_id"): {"title": "Movie"},
    ("sonarr", "calendar"): [{"title": "Episode"}],
    ("sonarr", "wanted"): [{"title": "Episode"}],
    ("sonarr", "queue"): [{"title": "Episode"}],
    ("sonarr", "recent"): [
        {"series": {"title": "Series"}, "episode": {"title": "Episode"}}
    ],
    ("sonarr", "lookup"): [{"title": "Series"}],
    ("sonarr", "series"): [{"title": "Series"}],
    ("sonarr", "series_id"): {"title": "Series"},
    ("maintainerr", "pending"): [{"title": "Collection"}],
    ("maintainerr", "storage"): {
        "mounts": [{"path": "/data", "freeSpace": 1, "totalSpace": 2}],
        "totals": {"freeSpace": 1, "totalSpace": 2, "usedSpace": 1},
    },
    ("maintainerr", "health"): {"status": "ok"},
    ("seerr", "requests"): [{"media": {"id": 1}}],
    ("seerr", "request-count"): {"pending": 1},
    ("seerr", "search"): [{"id": 1}],
    ("seerr", "available"): [{"id": 1}],
    ("seerr", "user"): {"username": "operator"},
    ("seerr", "tv"): {"name": "Show"},
    ("seerr", "movie"): {"title": "Movie"},
    ("seerr", "trending"): [{"id": 1}],
    ("seerr", "upcoming-movies"): [{"id": 1}],
    ("seerr", "upcoming-tv"): [{"id": 1}],
    ("seerr", "discover-movies"): [{"id": 1}],
    ("seerr", "discover-tv"): [{"id": 1}],
    ("seerr", "genres"): [{"id": 1, "name": "Action"}],
}


def _leaf_paths(row: dict[str, Any], prefix: str = "") -> list[str]:
    """Return the dotted leaf paths of ``row``.

    A nested mapping is expanded into its dotted leaf paths; a scalar
    (including a list) is a leaf in its own right.
    """
    paths: list[str] = []
    for key, value in row.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            paths.extend(_leaf_paths(value, f"{path}."))
        else:
            paths.append(path)
    return paths


def _rendered_row(dispatch_key: tuple[str, str]) -> dict[str, Any]:
    """Call the renderer for ``dispatch_key`` and return its summary row.

    The renderer is the source of truth for the summary shape, so the
    guard calls it and reads the row it returns rather than trusting a
    key list typed into this file.
    """
    renderer = _SUMMARY_RENDERERS[dispatch_key]
    payload = _REPRESENTATIVE_PAYLOADS[dispatch_key]
    rendered = renderer(payload)
    if isinstance(rendered, list):
        assert rendered, (
            f"{dispatch_key} returned no row for the representative "
            "payload; the guard cannot derive the summary shape"
        )
        row = rendered[0]
    else:
        row = rendered
    assert isinstance(row, dict), (
        f"{dispatch_key} returned {type(row).__name__}, expected a "
        "mapping row"
    )
    return row


# ---------------------------------------------------------------------------
# README row -> renderer dispatch key
# ---------------------------------------------------------------------------

#: Maps a README command cell to the renderer dispatch key that
#: produces its curated summary. The mapping is explicit (rather than
#: derived from the command name) because two command names are
#: ambiguous: ``sonarr series`` and ``sonarr series <id>`` are two
#: different commands with two different renderers and two different
#: key counts (10 and 9).
_DISPATCH_BY_COMMAND: dict[str, tuple[str, str]] = {
    "jellyfin now": ("jellyfin", "now"),
    "jellyfin resume": ("jellyfin", "resume"),
    "jellyfin recent": ("jellyfin", "recent"),
    "jellyfin nextup": ("jellyfin", "nextup"),
    "jellyfin latest": ("jellyfin", "latest"),
    "jellyfin search <query>": ("jellyfin", "search"),
    "jellyfin item <id>": ("jellyfin", "item"),
    "jellyfin favorites": ("jellyfin", "favorites"),
    "radarr calendar": ("radarr", "calendar"),
    "radarr calendar <start> [end]": ("radarr", "calendar"),
    "radarr wanted": ("radarr", "wanted"),
    "radarr queue": ("radarr", "queue"),
    "radarr recent": ("radarr", "recent"),
    "radarr lookup <term>": ("radarr", "lookup"),
    "radarr movie": ("radarr", "movie"),
    "radarr movie <id>": ("radarr", "movie_id"),
    "sonarr calendar": ("sonarr", "calendar"),
    "sonarr calendar <start> [end]": ("sonarr", "calendar"),
    "sonarr wanted": ("sonarr", "wanted"),
    "sonarr queue": ("sonarr", "queue"),
    "sonarr recent": ("sonarr", "recent"),
    "sonarr lookup <term>": ("sonarr", "lookup"),
    "sonarr series": ("sonarr", "series"),
    "sonarr series <id>": ("sonarr", "series_id"),
    "maintainerr pending": ("maintainerr", "pending"),
    "maintainerr storage": ("maintainerr", "storage"),
    "maintainerr health": ("maintainerr", "health"),
    "seerr requests": ("seerr", "requests"),
    "seerr request-count": ("seerr", "request-count"),
    "seerr search <query>": ("seerr", "search"),
    "seerr available <query>": ("seerr", "available"),
    "seerr user": ("seerr", "user"),
    "seerr tv <id>": ("seerr", "tv"),
    "seerr movie <id>": ("seerr", "movie"),
    "seerr trending [MEDIA_TYPE] [TIME_WINDOW]": ("seerr", "trending"),
    "seerr upcoming-movies": ("seerr", "upcoming-movies"),
    "seerr upcoming-tv": ("seerr", "upcoming-tv"),
    "seerr discover-movies": ("seerr", "discover-movies"),
    "seerr discover-tv": ("seerr", "discover-tv"),
    "seerr genres [MEDIA_TYPE]": ("seerr", "genres"),
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_readme_counted_rows_are_all_mapped() -> None:
    """Every counted README row maps to a known renderer.

    A new command table row with a key count must be added to
    :data:`_DISPATCH_BY_COMMAND`; an unmapped row means the guard
    would silently skip a documented claim.
    """
    unmapped = [
        command
        for command, _count, _names in _readme_rows()
        if command not in _DISPATCH_BY_COMMAND
    ]
    assert not unmapped, (
        "README rows with a 'curated N-key' claim are not mapped to a "
        f"renderer: {unmapped}. Add them to _DISPATCH_BY_COMMAND."
    )


def test_documented_counts_match_the_renderers() -> None:
    """Each documented ``curated N-key`` count matches the renderer.

    The expected number is derived from the renderer's own output: the
    row's top-level key count, or -- for the rows that document the
    expanded leaf paths of a nested envelope (``jellyfin now``) -- the
    count of those leaf paths.
    """
    failures: list[str] = []
    for command, claimed, names in _readme_rows():
        dispatch_key = _DISPATCH_BY_COMMAND[command]
        row = _rendered_row(dispatch_key)
        top_level = set(row)
        leaves = set(_leaf_paths(row))

        if names and set(names) <= top_level:
            # The row documents the summary's top-level keys.
            expected = len(top_level)
        else:
            # The row documents the expanded leaf paths of a nested
            # envelope (``jellyfin now`` documents ``playing.type`` and
            # friends).
            expected = len(leaves)

        if claimed != expected:
            failures.append(
                f"{command}: README claims {claimed} keys, the renderer "
                f"emits {expected} "
                f"(top-level: {sorted(top_level)}, leaves: {sorted(leaves)})"
            )

    assert not failures, (
        "README key counts drifted from the renderers:\n"
        + "\n".join(failures)
    )


def test_documented_key_names_exist_in_the_renderer() -> None:
    """Every key name the README documents is emitted by the renderer.

    This is the name-level counterpart of the count check: the README
    must not document a key the renderer never emits (and the renderer
    must not emit a key the README never documents).
    """
    failures: list[str] = []
    for command, _claimed, names in _readme_rows():
        if not names:
            continue
        dispatch_key = _DISPATCH_BY_COMMAND[command]
        row = _rendered_row(dispatch_key)
        addressable = set(row) | set(_leaf_paths(row))
        unknown = [name for name in names if name not in addressable]
        if unknown:
            failures.append(
                f"{command}: the README documents {unknown}, which the "
                f"renderer does not emit (it emits {sorted(addressable)})"
            )
        documented_top_level = set(names) <= set(row)
        if documented_top_level:
            # The row documents the summary's top-level keys, so the
            # renderer's top-level key set must match it exactly. (A
            # row that documents the expanded leaf paths of a nested
            # envelope intentionally omits the intermediate envelope
            # names, so that direction is not asserted for it.)
            undocumented = sorted(set(row) - set(names))
            if undocumented:
                failures.append(
                    f"{command}: the renderer emits {undocumented}, which "
                    "the README does not document"
                )

    assert not failures, (
        "README key names drifted from the renderers:\n"
        + "\n".join(failures)
    )


def test_sonarr_series_rows_are_not_conflated() -> None:
    """``sonarr series`` and ``sonarr series <id>`` stay distinct.

    The two commands have two different renderers and two different
    key counts (10 and 9). A guard that treated them as one command
    would false-fail on whichever of the two does not match, so the
    distinction is pinned here explicitly.
    """
    library_row = _rendered_row(("sonarr", "series"))
    record_row = _rendered_row(("sonarr", "series_id"))
    assert len(library_row) == 10, (
        f"sonarr series should emit 10 keys, got {sorted(library_row)}"
    )
    assert len(record_row) == 9, (
        f"sonarr series <id> should emit 9 keys, got {sorted(record_row)}"
    )
    assert set(library_row) != set(record_row), (
        "sonarr series and sonarr series <id> must not share a key set"
    )


def test_radarr_recent_preserves_the_nested_movie_envelope() -> None:
    """``radarr recent`` emits the nested ``movie`` envelope as a key.

    The envelope is deliberately preserved so a row can be chained into
    ``radarr movie <id>``; the README documents it as one of the nine
    summary keys.
    """
    row = _rendered_row(("radarr", "recent"))
    assert len(row) == 9, (
        f"radarr recent should emit 9 keys, got {sorted(row)}"
    )
    assert "movie" in row, (
        "radarr recent must keep the nested movie envelope; it is what "
        "radarr movie <id> cross-references"
    )


def test_seerr_requests_summary_carries_the_external_service_slug() -> None:
    """``seerr requests`` publishes ``externalServiceSlug`` in the summary.

    The key is intentionally absent from the ``--human`` column list
    (its 18-char header would truncate), but it IS part of the JSON
    summary, so the README documents it as one of the eleven keys.
    """
    row = _rendered_row(("seerr", "requests"))
    assert len(row) == 11, (
        f"seerr requests should emit 11 keys, got {sorted(row)}"
    )
    assert "externalServiceSlug" in row, (
        "seerr requests must publish externalServiceSlug in the JSON "
        "summary even though the --human table drops the column"
    )


def test_sonarr_recent_keeps_the_nested_series_and_episode_objects() -> None:
    """``sonarr recent`` emits ``series`` / ``episode`` as nested objects.

    The README lists them as two of the eleven summary keys; the
    ``series.title`` / ``episode.title`` names are ``--human`` column
    tokens resolved against those nested objects, not flat summary keys.
    """
    row = _rendered_row(("sonarr", "recent"))
    assert len(row) == 11, (
        f"sonarr recent should emit 11 keys, got {sorted(row)}"
    )
    assert isinstance(row.get("series"), dict), (
        "sonarr recent must emit series as a nested object"
    )
    assert isinstance(row.get("episode"), dict), (
        "sonarr recent must emit episode as a nested object"
    )
