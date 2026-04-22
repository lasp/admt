"""``admt what`` -- list buildable targets as admt commands.

Runs ``redo what`` in the container, then translates the output:

- Redo's header line (``redo  <target>`` with double-space, status
  indicator) is dropped.
- Each listed target is rewritten to its ``admt`` equivalent (``all`` ->
  ``admt build``, ``test_all`` -> ``admt test --all``, ...). Unknown
  targets fall back to ``admt build <target>``, which works because
  ``BuildCommand`` forwards an explicit target through to redo.

The translation is purely cosmetic -- redo still ran once in the
container; admt reformats the listing.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from admt.adapters.redo import RedoAdapter
from admt.commands.base import ContainerPassthroughCommand
from admt.context import Result
from admt.exceptions import ContainerError

if TYPE_CHECKING:
    from admt.context import Context


# Named redo targets -> admt equivalents. Anything not in this map falls
# back to ``admt build <target>`` (BuildCommand passes arbitrary targets
# through to redo).
_TARGET_MAP: dict[str, str] = {
    "all": "admt build",
    "test": "admt test",
    "test_all": "admt test --all",
    "style": "admt style",
    "style_all": "admt style --all",
    "analyze": "admt analyze",
    "analyze_all": "admt analyze --all",
    "clean": "admt clean",
    "clean_all": "admt clean --all",
    "coverage": "admt coverage",
    "coverage_all": "admt coverage --all",
    "publish": "admt publish",
    "publish_all": "admt publish --all",
    "prove": "admt prove",
    "templates": "admt templates",
    "what": "admt what",
}

# ANSI escape codes that redo may emit for its colored progress line.
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

# Redo's "currently processing" header uses at least two spaces after
# "redo". The listed targets themselves use a single space.
_REDO_HEADER = re.compile(r"^redo\s\s+\S+")
_REDO_LIST_ENTRY = re.compile(r"^redo\s(?P<target>\S.*?)$")


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
            context.output.info(line)
        return Result(exit_code=0)

    @staticmethod
    def _transform(output: str) -> list[str]:
        """Translate raw ``redo what`` output into admt-command lines."""
        lines: list[str] = []
        for raw in output.splitlines():
            clean = _ANSI_ESCAPE.sub("", raw).rstrip()
            if not clean:
                continue
            if _REDO_HEADER.match(clean):
                # Drop redo's "currently running <target>" status line.
                continue
            match = _REDO_LIST_ENTRY.match(clean)
            if match is None:
                # Not a recognizable redo line (unusual in practice); pass through.
                lines.append(clean)
                continue
            target = match.group("target").strip()
            if target in _TARGET_MAP:
                lines.append(_TARGET_MAP[target])
            else:
                lines.append(f"admt build {target}")
        return lines
