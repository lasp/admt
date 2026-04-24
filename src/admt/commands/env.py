"""admt env subcommands: register projects and switch the active one.

Phase 1 surface: ``admt env init [path]`` and ``admt env use <name>``. The
remaining env subcommands (``start``, ``stop``, ``login``, etc.) land in
Phase 2 once the container service is available.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from admt.commands.base import Command
from admt.context import Result
from admt.exceptions import ArgumentError

if TYPE_CHECKING:
    from admt.context import Context


class EnvInitCommand(Command):
    """Register an Adamant project with admt and make it active.

    Honors the re-init policy from ARCHITECTURE.md §Re-running admt env init:
    ``--force`` replaces without prompting, ``--yes`` accepts the default
    (No) and declines, ``ADMT_NONINTERACTIVE`` errors with exit 3 unless
    ``--force`` is also set, and interactive mode asks with default ``No``.
    """

    name: ClassVar[str] = "env init"
    help: ClassVar[str] = "Register a project with admt."
    requires_project: ClassVar[bool] = False

    def __init__(self, project_root: Path | None = None) -> None:
        """Capture the explicit project-root path, or ``None`` to mean cwd."""
        self._project_root = project_root

    def execute(self, context: Context) -> Result:
        """Resolve the project root, apply re-init policy, and register."""
        root = self._resolve_root(context)
        existing_name = context.config_service.is_project_registered_at(root)
        force = self._decide_force(context, existing_name)
        if force is None:
            # Re-init was declined; nothing to do.
            return Result(exit_code=0)
        project = context.config_service.register_project(root, force=force)
        mount_count = len(project.volume_mounts)
        context.output.success(
            f"Registered project '{project.name}' with {mount_count} volume mount(s)."
        )
        context.output.info(f"Active project: {project.name}")
        return Result(exit_code=0)

    def _resolve_root(self, context: Context) -> Path:
        candidate = self._project_root if self._project_root is not None else context.path
        base = candidate if candidate is not None else Path.cwd()
        return base.resolve(strict=False)

    @staticmethod
    def _decide_force(context: Context, existing_name: str | None) -> bool | None:
        """Return ``True`` to overwrite, ``False`` for a fresh register, ``None`` to decline."""
        if existing_name is None:
            return False
        if context.force:
            return True
        if context.noninteractive:
            msg = (
                f"Project '{existing_name}' is already registered. "
                f"Re-run with --force to overwrite."
            )
            raise ArgumentError(msg)
        overwrite = context.output.prompt(
            f"Project '{existing_name}' is already registered. Overwrite?",
            default=False,
        )
        if not overwrite:
            context.output.info(
                "Project already registered; not overwriting. Re-run with --force to replace."
            )
            return None
        return True


class EnvUseCommand(Command):
    """Switch the active project to ``project_name``."""

    name: ClassVar[str] = "env use"
    help: ClassVar[str] = "Switch the active project."
    requires_project: ClassVar[bool] = False

    def __init__(self, project_name: str) -> None:
        """Capture the target project name."""
        self._project_name = project_name

    def execute(self, context: Context) -> Result:
        """Set the active project and report the new state."""
        context.config_service.set_active_project(self._project_name)
        context.output.info(f"Active project: {self._project_name}")
        return Result(exit_code=0)
