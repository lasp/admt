"""``admt style`` -- forward to ``redo style`` (``--all`` -> ``style_all``)."""

from __future__ import annotations

from typing import ClassVar

from admt.commands.base import ContainerPassthroughCommand


class StyleCommand(ContainerPassthroughCommand):
    """Check code style via ``redo style``."""

    name: ClassVar[str] = "style"
    help: ClassVar[str] = "Check code style (redo style; --all for style_all)."
    redo_target: ClassVar[str] = "style"
    supports_all: ClassVar[bool] = True
    status_verb: ClassVar[str | None] = "checking style"
