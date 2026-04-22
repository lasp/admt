"""``admt analyze`` -- forward to ``redo analyze`` (``--all`` -> ``analyze_all``)."""

from __future__ import annotations

from typing import ClassVar

from admt.commands.base import ContainerPassthroughCommand


class AnalyzeCommand(ContainerPassthroughCommand):
    """Run static analysis via ``redo analyze``."""

    name: ClassVar[str] = "analyze"
    help: ClassVar[str] = "Run static analysis (redo analyze; --all for analyze_all)."
    redo_target: ClassVar[str] = "analyze"
    supports_all: ClassVar[bool] = True
    status_verb: ClassVar[str | None] = "analyzing"
