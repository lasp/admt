"""``admt build`` -- forward to ``redo all`` (or an explicit target)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from admt.commands.base import ContainerPassthroughCommand

if TYPE_CHECKING:
    from admt.context import Context, Result


class BuildCommand(ContainerPassthroughCommand):
    """Build via redo; defaults to ``redo all`` when no target is supplied."""

    name: ClassVar[str] = "build"
    help: ClassVar[str] = "Build via redo (default target: all)."
    redo_target: ClassVar[str] = "all"
    status_verb: ClassVar[str | None] = "building"

    def resolve_target(self, context: Context) -> str:
        """Use ``context.target`` when the user supplied one, otherwise ``all``."""
        return context.target if context.target else self.redo_target

    def execute(self, context: Context) -> Result:
        """Run the passthrough build; on failure, hint at generated-source targets.

        Adamant generates sources into each directory's ``build/src/``, so
        a source-relative name (``src/types/foo.ads``) has no redo rule and
        redo's error does not redirect. When the failed target is an Ada
        source outside ``build/`` and its ``build/src/`` twin exists on the
        host, a hint points at it. Advisory only: the exit code stays
        redo's, and no hint fires when the candidate is absent.
        """
        result = super().execute(context)
        if result.exit_code != 0:
            hint = self._generated_source_hint(context)
            if hint is not None:
                context.output.info(hint)
        return result

    @staticmethod
    def _generated_source_hint(context: Context) -> str | None:
        """Return the ``build/src/`` suggestion for ``context.target``, or ``None``."""
        target = context.target
        if not target or not target.endswith((".ads", ".adb")):
            return None
        rel = Path(target)
        if rel.is_absolute() or "build" in rel.parts:
            return None
        candidate = rel.parent / "build" / "src" / rel.name
        host_cwd = (context.path if context.path is not None else Path.cwd()).resolve(strict=False)
        if not (host_cwd / candidate).exists():
            return None
        return f"Generated sources are built under build/src/; try 'admt build {candidate}'."
