"""``admt test`` -- forward to ``redo test`` (``--all`` switches to ``test_all``).

Module is named ``test_cmd`` to avoid shadowing pytest when test runners
import ``admt.commands.*`` modules during collection.
"""

from __future__ import annotations

from typing import ClassVar

from admt.commands.base import ContainerPassthroughCommand


class TestCommand(ContainerPassthroughCommand):
    """Run tests via ``redo test``; ``--all`` switches to ``redo test_all``."""

    # pytest tries to collect any class whose name starts with ``Test`` and
    # whose metaclass cooperates. The Q4 ``__new__`` on
    # ``ContainerPassthroughCommand`` makes pytest emit a collection warning
    # ("cannot collect ... because it has a __new__ constructor"). Opting out
    # of pytest collection silences the warning -- this is a CLI command
    # class, not a test class.
    __test__ = False

    name: ClassVar[str] = "test"
    help: ClassVar[str] = "Run tests (redo test; --all for test_all)."
    redo_target: ClassVar[str] = "test"
    supports_all: ClassVar[bool] = True
    status_verb: ClassVar[str | None] = "testing"
