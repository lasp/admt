"""Redo command builder -- pure string construction, no subprocess.

DockerAdapter drives the actual invocation through the env-snapshot proxy
script. This module just assembles the ``cd <cwd> && [DEBUG=1 ]redo <target>``
string that ends up as the ``bash -c`` argument.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class RedoAdapter:
    """Builds redo command strings for the container exec proxy."""

    @staticmethod
    def build_command(target: str, *, cwd: Path, debug: bool = False) -> str:
        """Construct ``cd <cwd> && [DEBUG=1 ]redo <target>``.

        Args:
            target: The redo target name (``all``, ``test``, ``what``, a
                specific file like ``build/obj/foo.o``, etc.).
            cwd: Container-side working directory to ``cd`` into first.
            debug: When ``True``, prepend ``DEBUG=1 `` so Adamant's redo
                emits its debug trace. ``--debug`` on the CLI toggles this.

        Returns:
            A single shell-command string suitable for ``bash -c``.
        """
        debug_prefix = "DEBUG=1 " if debug else ""
        return f"cd {cwd} && {debug_prefix}redo {target}"
