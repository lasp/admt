"""Context and Result dataclasses passed through the command pipeline.

Phase 0 scope: global flags, positional arguments, and the ``noninteractive``
marker derived from ``ADMT_NONINTERACTIVE``. The service fields
(``config_service``, ``output``, ``container_service``, ``path_mapper``) and
the ``resolve_container_path()`` method are added in Phase 1 when the
corresponding services exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class Context:
    """Everything a command needs to execute. Built by the CLI adapter.

    Attributes:
        verbose: Echo underlying docker/redo commands before executing them.
        quiet: Suppress output on success; only errors are printed.
        debug: Implies ``verbose``; prepends ``DEBUG=1`` to redo commands.
        yes: Auto-accept interactive prompts with their default value.
        force: Overwrite existing files without confirmation.
        noninteractive: Derived from the ``ADMT_NONINTERACTIVE`` env var.
        target: Optional redo target (e.g., ``build/obj/foo.o``).
        path: Optional host directory resolved into the container.
        run_all: ``--all``/``-a`` flag on commands that support it.
    """

    verbose: bool = False
    quiet: bool = False
    debug: bool = False
    yes: bool = False
    force: bool = False
    noninteractive: bool = False
    target: str | None = None
    path: Path | None = None
    run_all: bool = False


@dataclass
class Result:
    """Structured record of what a command did.

    Output from passthrough commands is streamed directly to the terminal;
    ``Result`` carries only the exit code and metadata about file operations.
    """

    exit_code: int = 0
    files_created: list[Path] = field(default_factory=list)
    files_modified: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
