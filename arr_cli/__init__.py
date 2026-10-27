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
#
# ``__version__`` below is the single definition site of the release
# version: the package metadata (``pyproject.toml``), the ``--version``
# flag, the Jellyfin ``MediaBrowser`` envelope, and the outbound
# ``User-Agent`` header all derive from it. The module therefore stays
# free of runtime imports so setuptools can resolve the attribute
# statically at build time (the build environment carries neither
# ``requests`` nor ``PyYAML``).

__version__ = "0.1.1"
__all__: list[str] = []
