"""``admt coverage`` -- forward to ``redo coverage`` (``--all`` -> ``coverage_all``)."""

from __future__ import annotations

from typing import ClassVar

from admt.commands.base import ContainerPassthroughCommand


class CoverageCommand(ContainerPassthroughCommand):
    """Generate coverage reports via ``redo coverage``."""

    name: ClassVar[str] = "coverage"
    help: ClassVar[str] = "Generate coverage reports (redo coverage; --all for coverage_all)."
    redo_target: ClassVar[str] = "coverage"
    supports_all: ClassVar[bool] = True
    status_verb: ClassVar[str | None] = "generating coverage"
