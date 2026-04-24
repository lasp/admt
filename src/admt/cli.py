"""Click CLI adapter -- thin, no business logic.

Wires services into a ``Context`` at the top-level group, registers the
``admt env`` subgroup, and dispatches each subcommand to its ``Command``
class. All logic lives in commands/services/adapters; this file is pure
Click plumbing.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from admt.bootstrap import build_container_service, build_context
from admt.commands.env import (
    EnvBuildCommand,
    EnvExecCommand,
    EnvInitCommand,
    EnvListCommand,
    EnvLoginCommand,
    EnvPullCommand,
    EnvPushCommand,
    EnvRefreshCommand,
    EnvRestartCommand,
    EnvRmCommand,
    EnvStartCommand,
    EnvStatusCommand,
    EnvStopCommand,
    EnvUseCommand,
)
from admt.exceptions import AdmtError

if TYPE_CHECKING:
    from typing import Any

    from admt.commands.base import Command
    from admt.context import Context


class AliasedGroup(click.Group):
    """Click group that resolves command aliases (e.g., ``admt b`` -> ``admt build``)."""

    # click.Group.__init__ accepts **attrs: Any; we pass them through verbatim.
    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        """Initialize the group with an empty alias map."""
        super().__init__(*args, **kwargs)
        self._aliases: dict[str, str] = {}

    def add_alias(self, alias: str, command_name: str) -> None:
        """Register an alias that resolves to an existing command name."""
        self._aliases[alias] = command_name

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        """Resolve aliases before dispatching to the underlying command."""
        resolved = self._aliases.get(cmd_name, cmd_name)
        return super().get_command(ctx, resolved)


@click.group(cls=AliasedGroup)
@click.version_option()
@click.option("--verbose", "-v", is_flag=True, help="Show underlying commands")
@click.option("--quiet", "-q", is_flag=True, help="Minimal output")
@click.option("--debug", "-d", is_flag=True, help="Verbose + redo DEBUG=1")
@click.option("--yes", "-y", is_flag=True, help="Auto-accept prompts with defaults")
@click.option("--force", "-f", is_flag=True, help="Overwrite existing files")
@click.pass_context
def cli(  # noqa: PLR0913  -- click callbacks take one parameter per option
    ctx: click.Context,
    *,
    verbose: bool,
    quiet: bool,
    debug: bool,
    yes: bool,
    force: bool,
) -> None:
    """Top-level admt CLI group -- The Adamant Multitool."""
    verbose = verbose or debug
    ctx.obj = build_context(verbose=verbose, quiet=quiet, debug=debug, yes=yes, force=force)


@cli.group(name="env", cls=AliasedGroup)
def env_group() -> None:
    """Manage the Adamant development environment."""


cli.add_alias("e", "env")


@env_group.command(name="init")
@click.argument(
    "path",
    required=False,
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
)
@click.pass_obj
def env_init(admt_ctx: Context, path: str | None) -> None:
    """Register a project with admt (run from the project root, or pass PATH)."""
    root = Path(path) if path else None
    _run_command(EnvInitCommand(root), admt_ctx)


@env_group.command(name="use")
@click.argument("project_name")
@click.pass_obj
def env_use(admt_ctx: Context, project_name: str) -> None:
    """Switch the active project to PROJECT_NAME."""
    _run_command(EnvUseCommand(project_name), admt_ctx)


@env_group.command(name="start")
@click.pass_obj
def env_start(admt_ctx: Context) -> None:
    """Start the project container."""
    _run_command(EnvStartCommand(), admt_ctx)


@env_group.command(name="stop")
@click.pass_obj
def env_stop(admt_ctx: Context) -> None:
    """Stop the project container."""
    _run_command(EnvStopCommand(), admt_ctx)


@env_group.command(name="restart")
@click.pass_obj
def env_restart(admt_ctx: Context) -> None:
    """Restart the project container (stop + start)."""
    _run_command(EnvRestartCommand(), admt_ctx)


@env_group.command(name="login")
@click.pass_obj
def env_login(admt_ctx: Context) -> None:
    """Open an interactive shell in the container."""
    _run_command(EnvLoginCommand(), admt_ctx)


@env_group.command(name="status")
@click.pass_obj
def env_status(admt_ctx: Context) -> None:
    """Show container status."""
    _run_command(EnvStatusCommand(), admt_ctx)


@env_group.command(name="build")
@click.pass_obj
def env_build(admt_ctx: Context) -> None:
    """Build the Docker image."""
    _run_command(EnvBuildCommand(), admt_ctx)


@env_group.command(name="push")
@click.pass_obj
def env_push(admt_ctx: Context) -> None:
    """Push the Docker image."""
    _run_command(EnvPushCommand(), admt_ctx)


@env_group.command(name="pull")
@click.pass_obj
def env_pull(admt_ctx: Context) -> None:
    """Pull the Docker image."""
    _run_command(EnvPullCommand(), admt_ctx)


@env_group.command(name="exec")
@click.argument("command", required=True)
@click.pass_obj
def env_exec(admt_ctx: Context, command: str) -> None:
    """Run COMMAND inside the container."""
    _run_command(EnvExecCommand(command), admt_ctx)


@env_group.command(name="refresh")
@click.pass_obj
def env_refresh(admt_ctx: Context) -> None:
    """Re-run env/activate and rebuild the admt env snapshot."""
    _run_command(EnvRefreshCommand(), admt_ctx)


@env_group.command(name="rm")
@click.option("--volumes", is_flag=True, help="Also remove volumes")
@click.option("--image", is_flag=True, help="Also remove the Docker image")
@click.option("--remove-all", is_flag=True, help="Remove container + volumes + image")
@click.pass_obj
def env_rm(admt_ctx: Context, *, volumes: bool, image: bool, remove_all: bool) -> None:
    """Remove the project container."""
    cmd = EnvRmCommand(remove_volumes=volumes, remove_image=image, remove_all=remove_all)
    _run_command(cmd, admt_ctx)


@env_group.command(name="list")
@click.pass_obj
def env_list(admt_ctx: Context) -> None:
    """List registered projects."""
    _run_command(EnvListCommand(), admt_ctx)


def _run_command(cmd: Command, admt_ctx: Context) -> None:
    """Execute a Command, wiring ContainerService on demand, converting errors to exits."""
    try:
        if cmd.requires_container:
            admt_ctx.container_service = build_container_service(admt_ctx)
        result = cmd.execute(admt_ctx)
    except AdmtError as exc:
        admt_ctx.output.error(str(exc))
        raise click.exceptions.Exit(exc.exit_code) from exc
    if result.exit_code:
        raise click.exceptions.Exit(result.exit_code)
