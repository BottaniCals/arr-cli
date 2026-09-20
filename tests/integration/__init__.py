"""Integration test package for arr-cli (task 19).

This package hosts the opt-in integration tests that exercise the
arr-cli CLIs against a real instance of one or more of the supported
services (Jellyfin, Radarr, Sonarr, Maintainerr, Seerr). All tests in
this package are SKIPPED by default and only run when the test
runner is invoked with the ``--run-integration`` flag.

See :mod:`tests.integration.conftest` for the skip-helper and the
canonical documentation of the opt-in flag.
"""