"""``admt what`` -- list buildable targets as admt commands.

Runs ``redo what`` in the container, captures the output, and prints each
listed target rewritten to its admt-equivalent command via
``redo_output.rewrite_line``. The redo top-level status header is dropped;
unknown targets fall back to ``admt build <target>`` because BuildCommand
forwards arbitrary targets through to redo.

``--all`` variants (e.g. ``admt test --all``) are filtered out because
they're the same commands with a flag, not independent targets -- the
user already has them via ``admt <cmd> --help`` and listing both forms
clutters the output.

This command uses capture mode (not streaming) because the listing is
short, self-contained, and benefits from the extra post-processing pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from admt.adapters.redo import RedoAdapter
from admt.adapters.redo_output import rewrite_line, split_verb
from admt.commands.base import ContainerPassthroughCommand
from admt.context import Result
from admt.exceptions import ContainerError

if TYPE_CHECKING:
    from admt.context import Context


# Admt-side mappings of redo's ``<verb>_all`` targets. These are dropped
# from ``admt what`` output -- they duplicate the non-``--all`` verb with
# just a flag difference, which ``admt <cmd> --help`` already documents.
_IMPLICIT_ALL_VARIANTS: frozenset[str] = frozenset(
    f"admt {verb} --all" for verb in ("test", "style", "analyze", "clean", "coverage", "publish")
)


class WhatCommand(ContainerPassthroughCommand):
    """List buildable targets, translated from redo names to admt commands."""

    name: ClassVar[str] = "what"
    help: ClassVar[str] = "List buildable targets as admt commands."
    redo_target: ClassVar[str] = "what"

    def execute(self, context: Context) -> Result:
        """Run ``redo what`` (captured), transform the listing, emit it."""
        if context.container_service is None:
            msg = "ContainerService was not wired for this command (CLI bug)."
            raise ContainerError(msg)
        container_path = context.resolve_container_path()
        redo_cmd = RedoAdapter.build_command("what", cwd=container_path, debug=context.debug)
        result = context.container_service.exec_captured(redo_cmd, merge_stderr=True)
        if result.returncode != 0:
            if result.stdout:
                context.output.emit_captured(result.stdout)
            return Result(exit_code=result.returncode)
        for line in self._transform(result.stdout):
            verb, rest = split_verb(line)
            context.output.info(context.output.admt(verb) + rest if verb else line)
        return Result(exit_code=0)

    @staticmethod
    def _transform(output: str) -> list[str]:
        """Split ``redo what`` output into lines, rewrite, drop ``--all`` variants.

        Blank lines (``redo what`` doesn't emit any in practice, but ANSI-
        wrapped or extra-newline output shouldn't produce blank entries)
        and ``admt <verb> --all`` duplicates are filtered out.
        """
        lines: list[str] = []
        for raw in output.splitlines():
            result = rewrite_line(raw)
            if result is None:
                continue
            stripped = result.rstrip()
            if not stripped:
                continue
            if stripped in _IMPLICIT_ALL_VARIANTS:
                continue
            lines.append(stripped)
        return lines
