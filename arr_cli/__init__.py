# arr_cli — read-only CLI wrappers for Jellyfin, Radarr, Sonarr, Maintainerr, Seerr.
#
# Subpackages:
#   arr_cli.facade  — shared config, HTTP, errors, output, CLI common helpers.
#   arr_cli.<service>  — one entry-point module per service (jellyfin, radarr,
#                     sonarr, maintainerr, seerr).
#
# Public re-exports live in the facade subpackage; this package init is
# deliberately empty of state — per REQ-5, the MVP is stateless per
# invocation and there is no module-level mutable state to import.

from __future__ import annotations

__version__ = "0.1.0"
__all__: list[str] = []
