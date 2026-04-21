"""Context and Result dataclasses passed through the command pipeline.

Required fields -- ``config_service`` and ``output`` -- are always set by the
CLI adapter. ``container_service`` and ``path_mapper`` are lazily populated
(only for commands that opt in via ``requires_container``); commands that
need them should have been wired by ``cli._run_command`` before ``execute``
runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from admt.exceptions import ConfigError

if TYPE_CHECKING:
    from admt.services.config import ConfigService
    from admt.services.container import ContainerService
    from admt.services.output import OutputService
    from admt.services.path_mapper import PathMapperService


@dataclass
class Context:
    """Everything a command needs to execute. Built by the CLI adapter.

    Attributes:
        config_service: The active project registry -- always present.
        output: User-facing output + prompt service -- always present.
        container_service: Lazily wired for commands with ``requires_container``.
        path_mapper: Lazily wired alongside ``container_service``.
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
    path_mapper: PathMapperService | None = None
    verbose: bool = False
    quiet: bool = False
    debug: bool = False
    yes: bool = False
    force: bool = False
    noninteractive: bool = False
    target: str | None = None
    path: Path | None = None
    run_all: bool = False

    def resolve_container_path(self) -> Path:
        """Map ``self.path`` (or ``cwd``) through volume mounts to a container path.

        Returns the container-side path for the effective working directory.
        The caller's cwd is used when ``self.path`` is ``None``. The path is
        canonicalized (symlinks resolved, relative parts flattened) before
        lookup.

        Raises:
            ConfigError: When ``path_mapper`` is not wired (command needs a
                project but none is configured).
        """
        if self.path_mapper is None:
            msg = "This command requires an active project. Run 'admt env init' to set one up."
            raise ConfigError(msg)
        host_path = self.path if self.path is not None else Path.cwd()
        return self.path_mapper.host_to_container(host_path.resolve(strict=False))


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
