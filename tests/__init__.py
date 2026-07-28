"""Test root package marker for arr-cli.

This file makes :mod:`tests` importable as a regular Python package,
which in turn lets the integration tests under
:mod:`tests.integration` be discovered both by ``pytest`` and by
``python -m unittest``. The unit tests under :mod:`tests.unit` keep
their existing top-level-module discovery path (no behavioural
change for the existing 516-test suite).
"""