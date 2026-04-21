"""Entry point for the admt CLI.

Installs a SIGINT handler before Click takes over so Ctrl+C during a
long-running ``docker compose`` subprocess:

1. Prints the PIDs of any in-flight subprocesses (so the user can send
   a harder signal with ``kill -9`` if needed).
2. Exits with code 130 (the conventional "terminated by SIGINT" code).

Best-effort per ARCHITECTURE.md §Signal Handling: the subprocess will
also receive SIGINT via the terminal's foreground process group, so in
most cases it exits cleanly on its own.
"""

from __future__ import annotations

import signal
import sys
from typing import TYPE_CHECKING

from admt.adapters.docker import iter_active_pids
from admt.cli import cli

if TYPE_CHECKING:
    from types import FrameType

_SIGINT_EXIT_CODE = 130


def _sigint_handler(_signum: int, _frame: FrameType | None) -> None:
    pids = iter_active_pids()
    if pids:
        sys.stderr.write(f"Interrupted. Active subprocess PIDs: {pids}\n")
    else:
        sys.stderr.write("Interrupted.\n")
    sys.exit(_SIGINT_EXIT_CODE)


def main() -> None:
    """Run the admt CLI entry point with a SIGINT handler installed."""
    signal.signal(signal.SIGINT, _sigint_handler)
    cli()


if __name__ == "__main__":
    main()
