"""Context and Result dataclasses passed through the command pipeline.

Required fields -- ``config_service`` and ``output`` -- are always set by the
CLI adapter. ``container_service`` is lazily populated (only for commands
that opt in via ``requires_container``), so it is Optional; commands that
need it should have been wired by ``cli._run_command`` before ``execute``
runs. The path-mapper field follows the same lazy pattern in Phase 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from admt.services.config import ConfigService
    from admt.services.container import ContainerService
    from admt.services.output import OutputService


@dataclass
class Context:
    """Everything a command needs to execute. Built by the CLI adapter.

    Attributes:
        config_service: The active project registry -- always present.
        output: User-facing output + prompt service -- always present.
        container_service: Lazily wired for commands with ``requires_container``.
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

    config_service: ConfigService
    output: OutputService
    container_service: ContainerService | None = None
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
