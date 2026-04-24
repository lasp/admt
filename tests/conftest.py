"""Pytest root conftest.

Force-imports every module under ``admt.commands`` so that
``Command.__subclasses__()`` sees all concrete commands -- required by the
command-contract and CLI-parity tests in ``tests/unit/test_architecture.py``.
"""

import importlib
import pkgutil

import admt.commands

for _info in pkgutil.walk_packages(admt.commands.__path__, prefix="admt.commands."):
    importlib.import_module(_info.name)
