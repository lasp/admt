"""Pytest root conftest.

Force-imports every module under ``admt.commands`` so that
``Command.__subclasses__()`` sees all concrete commands -- required by the
command-contract and CLI-parity tests in ``tests/unit/test_architecture.py``.

Also exposes a ``make_context`` fixture that builds a ``Context`` with stub
services so unit tests that don't care about service behavior don't have to
wire them up themselves.
"""

import importlib
import pkgutil
from unittest.mock import MagicMock

import pytest

import admt.commands
from admt.context import Context
from admt.services.config import ConfigService
from admt.services.output import OutputService

for _info in pkgutil.walk_packages(admt.commands.__path__, prefix="admt.commands."):
    importlib.import_module(_info.name)


@pytest.fixture
def make_context():
    """Return a factory that builds a Context with MagicMock services."""

    def _factory(**overrides):
        # ``output.admt()`` is a passthrough by default so tests that assert
        # on the literal text of ``info(...)`` calls don't have to configure
        # the mock per-test. Tests that care about the gold coloring
        # behavior assert directly against OutputService (unit) or set a
        # different side_effect on the mock. The ``bold`` kwarg is
        # accepted to mirror the real signature -- bold=False is how
        # admt-relayed redo status messages route through.
        output_mock = MagicMock(spec=OutputService)
        output_mock.admt.side_effect = lambda message, *, bold=True: message
        defaults = {
            "config_service": MagicMock(spec=ConfigService),
            "output": output_mock,
        }
        defaults.update(overrides)
        return Context(**defaults)

    return _factory
