# arr_cli.facade — cross-cutting logic shared by every service CLI.
#
# Modules:
#   config            — ServiceConfig / AuthConfig dataclasses + YAML/TOML loader
#   transport         — requests-based HTTP transport with auth injection
#   errors            — ArrError hierarchy + exit-code map (1..5)
#   output            — JSON pass-through + --human tabular rendering
#   cli_common        — shared argparse base + main_wrapper + warn_once
#   retry             — optional exponential-backoff layer for --retry N
#   example_validator — placeholder-only check for arr.conf.example (REQ-1 AC4)
#
# Per the project's import-path convention (see design.md "Naming
# Convention"), nothing is re-exported from this __init__.py. Consumers
# import module-qualified, e.g.:
#
#     from arr_cli.facade import config, transport
#     from arr_cli.facade.errors import ConfigError
#
# Keeping the facade a flat namespace makes it trivially auditable and
# keeps references in tests/diagnostics unambiguous.

from __future__ import annotations

__all__: list[str] = []
