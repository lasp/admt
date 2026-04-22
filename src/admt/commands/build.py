"""``admt build`` -- forward to ``redo all`` (or an explicit target)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from admt.commands.base import ContainerPassthroughCommand

if TYPE_CHECKING:
    from admt.context import Context


class BuildCommand(ContainerPassthroughCommand):
    """Build via redo; defaults to ``redo all`` when no target is supplied."""

    name: ClassVar[str] = "build"
    help: ClassVar[str] = "Build via redo (default target: all)."
    redo_target: ClassVar[str] = "all"
    status_verb: ClassVar[str | None] = "building"

    def resolve_target(self, context: Context) -> str:
        """Use ``context.target`` when the user supplied one, otherwise ``all``."""
        return context.target if context.target else self.redo_target
