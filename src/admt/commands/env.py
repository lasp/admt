"""admt env subcommands -- project registry + container lifecycle.

Phase 1 landed ``init``/``use``. Phase 2 adds ``start``/``stop``/``restart``/
``login``/``status``/``build``/``push``/``pull``/``exec``/``refresh``/``rm``/
``list``. Most require a live ``ContainerService`` (wired lazily by the CLI
adapter when ``requires_container`` is ``True``); ``list`` and the Phase 1
commands only need the config registry.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from admt.commands.base import Command
from admt.context import Result
from admt.exceptions import ArgumentError, ContainerError

if TYPE_CHECKING:
    from admt.context import Context
    from admt.services.container import ContainerService


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
        # ADMT_ENV outranks both the session pin and the global default, so an
        # `env use` in a shell that exports it would silently not take effect.
        override = os.environ.get("ADMT_ENV")
        if override and override != self._project_name:
            context.output.warning(
                f"ADMT_ENV={override} is set in this shell and overrides the active "
                f"project for every command. Unset it for this change to take effect here."
            )
        return Result(exit_code=0)


def _require_container(context: Context) -> ContainerService:
    """Return ``context.container_service`` or raise a clear error."""
    if context.container_service is None:
        msg = "ContainerService was not wired for this command (CLI bug)."
        raise ContainerError(msg)
    return context.container_service


class EnvStartCommand(Command):
    """Start the project container (auto-pulls image when missing)."""

    name: ClassVar[str] = "env start"
    help: ClassVar[str] = "Start the project container."
    requires_project: ClassVar[bool] = True
    requires_container: ClassVar[bool] = True

    def execute(self, context: Context) -> Result:
        """Delegate to the ContainerService."""
        _require_container(context).start()
        return Result(exit_code=0)


class EnvStopCommand(Command):
    """Stop the project container."""

    name: ClassVar[str] = "env stop"
    help: ClassVar[str] = "Stop the project container."
    requires_project: ClassVar[bool] = True
    requires_container: ClassVar[bool] = True

    def execute(self, context: Context) -> Result:
        """Delegate to the ContainerService."""
        _require_container(context).stop()
        return Result(exit_code=0)


class EnvRestartCommand(Command):
    """Restart the project container (stop + start)."""

    name: ClassVar[str] = "env restart"
    help: ClassVar[str] = "Restart the project container (stop + start)."
    requires_project: ClassVar[bool] = True
    requires_container: ClassVar[bool] = True

    def execute(self, context: Context) -> Result:
        """Delegate to the ContainerService."""
        _require_container(context).restart()
        return Result(exit_code=0)


class EnvLoginCommand(Command):
    """Open an interactive bash shell in the container."""

    name: ClassVar[str] = "env login"
    help: ClassVar[str] = "Open an interactive shell in the project container."
    requires_project: ClassVar[bool] = True
    requires_container: ClassVar[bool] = True

    def execute(self, context: Context) -> Result:
        """Hand control to the interactive shell; propagate its exit code."""
        return Result(exit_code=_require_container(context).login())


class EnvStatusCommand(Command):
    """Report the project container's status."""

    name: ClassVar[str] = "env status"
    help: ClassVar[str] = "Show container status."
    requires_project: ClassVar[bool] = True
    requires_container: ClassVar[bool] = True

    def execute(self, context: Context) -> Result:
        """Print project name, container name, and status."""
        container = _require_container(context)
        project = context.config_service.get_active_project()
        source = context.config_service.get_active_source()
        context.output.info(f"Project: {project.name}")
        context.output.info(f"Active via: {source}")
        context.output.info(f"Container: {project.container_name}")
        context.output.info(f"Status: {container.status().value}")
        return Result(exit_code=0)


class EnvBuildCommand(Command):
    """Build the Docker image for the active project."""

    name: ClassVar[str] = "env build"
    help: ClassVar[str] = "Build the Docker image."
    requires_project: ClassVar[bool] = True
    requires_container: ClassVar[bool] = True

    def __init__(self, *, no_cache: bool = False) -> None:
        """Capture whether to bypass the layer cache."""
        self._no_cache = no_cache

    def execute(self, context: Context) -> Result:
        """Delegate to the ContainerService."""
        _require_container(context).build_image(no_cache=self._no_cache)
        return Result(exit_code=0)


class EnvPushCommand(Command):
    """Push the Docker image to its registry."""

    name: ClassVar[str] = "env push"
    help: ClassVar[str] = "Push the Docker image."
    requires_project: ClassVar[bool] = True
    requires_container: ClassVar[bool] = True

    def execute(self, context: Context) -> Result:
        """Delegate to the ContainerService."""
        _require_container(context).push_image()
        return Result(exit_code=0)


class EnvPullCommand(Command):
    """Pull the Docker image from its registry."""

    name: ClassVar[str] = "env pull"
    help: ClassVar[str] = "Pull the Docker image."
    requires_project: ClassVar[bool] = True
    requires_container: ClassVar[bool] = True

    def execute(self, context: Context) -> Result:
        """Delegate to the ContainerService."""
        _require_container(context).pull_image()
        return Result(exit_code=0)


class EnvExecCommand(Command):
    """Run an arbitrary command in the container via the admt env proxy."""

    name: ClassVar[str] = "env exec"
    help: ClassVar[str] = "Run a command inside the container."
    requires_project: ClassVar[bool] = True
    requires_container: ClassVar[bool] = True

    def __init__(self, command: str) -> None:
        """Capture the shell command to run in the container."""
        self._command = command

    def execute(self, context: Context) -> Result:
        """Run the command in the mapped working directory via the proxy script.

        The host cwd (or the ``-C`` directory in ``context.path``) resolves
        through the volume mounts exactly as for the passthrough commands;
        an unmapped directory is a path error, not a silent run at ``/``.
        """
        container_path = context.resolve_container_path()
        command = f"cd {shlex.quote(str(container_path))} && {self._command}"
        interactive = os.isatty(0) and not context.noninteractive
        exit_code = _require_container(context).exec(command, interactive=interactive)
        return Result(exit_code=exit_code)


class EnvRefreshCommand(Command):
    """Regenerate the admt environment snapshot inside the container."""

    name: ClassVar[str] = "env refresh"
    help: ClassVar[str] = "Re-run env/activate and rebuild the admt env snapshot."
    requires_project: ClassVar[bool] = True
    requires_container: ClassVar[bool] = True

    def execute(self, context: Context) -> Result:
        """Delegate to the ContainerService."""
        _require_container(context).refresh()
        return Result(exit_code=0)


class EnvRmCommand(Command):
    """Remove the project container (optionally volumes and/or image)."""

    name: ClassVar[str] = "env rm"
    help: ClassVar[str] = "Remove the project container."
    requires_project: ClassVar[bool] = True
    requires_container: ClassVar[bool] = True

    def __init__(
        self,
        *,
        remove_volumes: bool = False,
        remove_image: bool = False,
        remove_all: bool = False,
    ) -> None:
        """Capture the scope flags."""
        self._remove_volumes = remove_volumes
        self._remove_image = remove_image
        self._remove_all = remove_all

    def execute(self, context: Context) -> Result:
        """Prompt for confirmation unless ``--force``, then delegate removal.

        Note on ``--yes`` vs ``--force``: the prompt's default is ``No``
        (destructive op), and ``--yes`` only auto-accepts the default. So
        ``--yes`` declines the removal -- the user must either type ``y`` at
        the prompt or pass ``--force`` to skip the prompt entirely.
        """
        if not context.force:
            project = context.config_service.get_active_project()
            scope = self._describe_scope()
            if not context.output.prompt(
                f"Remove container '{project.container_name}' ({scope})?", default=False
            ):
                context.output.info("Aborted.")
                return Result(exit_code=0)
        _require_container(context).rm(
            remove_volumes=self._remove_volumes,
            remove_image=self._remove_image,
            remove_all=self._remove_all,
        )
        return Result(exit_code=0)

    def _describe_scope(self) -> str:
        if self._remove_all:
            return "container + volumes + image"
        parts = ["container"]
        if self._remove_volumes:
            parts.append("volumes")
        if self._remove_image:
            parts.append("image")
        return " + ".join(parts)


class EnvListCommand(Command):
    """List registered projects, marking the active one."""

    name: ClassVar[str] = "env list"
    help: ClassVar[str] = "List registered projects."
    requires_project: ClassVar[bool] = False

    def execute(self, context: Context) -> Result:
        """Render ``name  compose_file`` lines; prefix the active project with ``*``.

        The ``*`` marks what THIS session resolves to (ADMT_ENV / its session
        pin / the global default) -- not the bare global -- so the listing
        matches what ``env status`` and build commands actually target here.
        """
        projects = context.config_service.list_projects()
        if not projects:
            context.output.info("No projects registered. Run 'admt env init' to set one up.")
            return Result(exit_code=0)
        active = context.config_service.resolved_active_name()
        width = max(len(name) for name in projects)
        for name in sorted(projects):
            marker = "*" if name == active else " "
            context.output.info(f"{marker} {name:<{width}}  {projects[name].compose_file}")
        return Result(exit_code=0)
