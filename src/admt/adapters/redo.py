"""Redo command builder -- pure string construction, no subprocess.

DockerAdapter drives the actual invocation through the env-snapshot proxy
script. This module just assembles the ``cd <cwd> && [DEBUG=1 ]redo
<target...>`` string that ends up as the ``bash -c`` argument.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class RedoAdapter:
    """Builds redo command strings for the container exec proxy."""

    @staticmethod
    def build_command(targets: Sequence[str], *, cwd: Path, debug: bool = False) -> str:
        """Construct ``cd <cwd> && [DEBUG=1 ]redo <target...>``.

        Every interpolated token is shell-quoted: the string runs via
        ``bash -c`` inside the container, and CODING_RULES §Subprocess
        Handling requires escaping at exactly this seam. Quoting is a
        no-op for the plain names redo targets normally are (``all``,
        ``build/obj/foo.o``), so their output is byte-identical.

        Args:
            targets: Redo target names, built in order by one invocation
                (redo stops at the first failing target).
            cwd: Container-side working directory to ``cd`` into first.
            debug: When ``True``, prepend ``DEBUG=1 `` so Adamant's redo
                emits its debug trace. ``--debug`` on the CLI toggles this.

        Returns:
            A single shell-command string suitable for ``bash -c``.
        """
        debug_prefix = "DEBUG=1 " if debug else ""
        joined = " ".join(shlex.quote(target) for target in targets)
        return f"cd {shlex.quote(str(cwd))} && {debug_prefix}redo {joined}"
