"""``admt clean`` -- forward to ``redo clean`` (``--all`` -> ``clean_all``)."""

from __future__ import annotations

from typing import ClassVar

from admt.commands.base import ContainerPassthroughCommand


class CleanCommand(ContainerPassthroughCommand):
    """Remove build artifacts via ``redo clean``."""

    name: ClassVar[str] = "clean"
    help: ClassVar[str] = "Remove build artifacts (redo clean; --all for clean_all)."
    redo_target: ClassVar[str] = "clean"
    supports_all: ClassVar[bool] = True
    status_verb: ClassVar[str | None] = "cleaning"
