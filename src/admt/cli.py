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
from admt.cli_utils import AliasedGroup
from admt.commands.analyze import AnalyzeCommand
from admt.commands.build import BuildCommand
from admt.commands.clean import CleanCommand
from admt.commands.coverage import CoverageCommand
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
from admt.commands.prove import ProveCommand
from admt.commands.publish import PublishCommand
from admt.commands.style import StyleCommand
from admt.commands.templates import TemplatesCommand
from admt.commands.test_cmd import TestCommand
from admt.commands.what import WhatCommand
from admt.exceptions import AdmtError

if TYPE_CHECKING:
    from admt.commands.base import Command
    from admt.context import Context


_SHELL_COMPLETION_EPILOG = """\
Enable shell completion by sourcing the generator for your shell:

\b
  bash:  eval "$(_ADMT_COMPLETE=bash_source admt)"
  zsh:   eval "$(_ADMT_COMPLETE=zsh_source admt)"
  fish:  eval (env _ADMT_COMPLETE=fish_source admt)

Add the appropriate line to your shell's rc file to persist.
"""


@click.group(cls=AliasedGroup, epilog=_SHELL_COMPLETION_EPILOG)
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


# ---------------------------------------------------------------------------
# Phase 3 redo passthrough commands.
# ---------------------------------------------------------------------------


def _parse_positional(admt_ctx: Context, arg: str | None) -> None:
    """Populate ``context.path`` (when ``arg`` is a directory on disk) or ``context.target``."""
    if not arg:
        return
    resolved = (Path.cwd() / arg).resolve(strict=False)
    if resolved.is_dir():
        admt_ctx.path = resolved
    else:
        admt_ctx.target = arg


@cli.command(name="build")
@click.argument("path_or_target", required=False)
@click.pass_obj
def build(admt_ctx: Context, path_or_target: str | None) -> None:
    """Build via redo (default: redo all). PATH_OR_TARGET cd's or names a target."""
    _parse_positional(admt_ctx, path_or_target)
    _run_command(BuildCommand(), admt_ctx)


@cli.command(name="what")
@click.argument("path_or_target", required=False)
@click.pass_obj
def what(admt_ctx: Context, path_or_target: str | None) -> None:
    """List buildable targets (redo what)."""
    _parse_positional(admt_ctx, path_or_target)
    _run_command(WhatCommand(), admt_ctx)


@cli.command(name="test")
@click.argument("path_or_target", required=False)
@click.option("--all", "-a", "run_all", is_flag=True, help="Switch to redo test_all")
@click.pass_obj
def test(admt_ctx: Context, path_or_target: str | None, *, run_all: bool) -> None:
    """Run tests via redo (--all switches to test_all)."""
    _parse_positional(admt_ctx, path_or_target)
    admt_ctx.run_all = run_all
    _run_command(TestCommand(), admt_ctx)


@cli.command(name="style")
@click.argument("path_or_target", required=False)
@click.option("--all", "-a", "run_all", is_flag=True, help="Switch to redo style_all")
@click.pass_obj
def style(admt_ctx: Context, path_or_target: str | None, *, run_all: bool) -> None:
    """Check code style via redo (--all switches to style_all)."""
    _parse_positional(admt_ctx, path_or_target)
    admt_ctx.run_all = run_all
    _run_command(StyleCommand(), admt_ctx)


@cli.command(name="analyze")
@click.argument("path_or_target", required=False)
@click.option("--all", "-a", "run_all", is_flag=True, help="Switch to redo analyze_all")
@click.pass_obj
def analyze(admt_ctx: Context, path_or_target: str | None, *, run_all: bool) -> None:
    """Run static analysis via redo (--all switches to analyze_all)."""
    _parse_positional(admt_ctx, path_or_target)
    admt_ctx.run_all = run_all
    _run_command(AnalyzeCommand(), admt_ctx)


@cli.command(name="clean")
@click.argument("path_or_target", required=False)
@click.option("--all", "-a", "run_all", is_flag=True, help="Switch to redo clean_all")
@click.pass_obj
def clean(admt_ctx: Context, path_or_target: str | None, *, run_all: bool) -> None:
    """Remove build artifacts via redo (--all switches to clean_all)."""
    _parse_positional(admt_ctx, path_or_target)
    admt_ctx.run_all = run_all
    _run_command(CleanCommand(), admt_ctx)


@cli.command(name="prove")
@click.argument("path_or_target", required=False)
@click.pass_obj
def prove(admt_ctx: Context, path_or_target: str | None) -> None:
    """Run SPARK formal verification via redo."""
    _parse_positional(admt_ctx, path_or_target)
    _run_command(ProveCommand(), admt_ctx)


@cli.command(name="coverage")
@click.argument("path_or_target", required=False)
@click.option("--all", "-a", "run_all", is_flag=True, help="Switch to redo coverage_all")
@click.pass_obj
def coverage(admt_ctx: Context, path_or_target: str | None, *, run_all: bool) -> None:
    """Generate coverage reports via redo (--all switches to coverage_all)."""
    _parse_positional(admt_ctx, path_or_target)
    admt_ctx.run_all = run_all
    _run_command(CoverageCommand(), admt_ctx)


@cli.command(name="publish")
@click.argument("path_or_target", required=False)
@click.option("--all", "-a", "run_all", is_flag=True, help="Switch to redo publish_all")
@click.pass_obj
def publish(admt_ctx: Context, path_or_target: str | None, *, run_all: bool) -> None:
    """Publish build artifacts via redo (--all switches to publish_all)."""
    _parse_positional(admt_ctx, path_or_target)
    admt_ctx.run_all = run_all
    _run_command(PublishCommand(), admt_ctx)


# Passthrough aliases -- keep in sync with MVP_PLAN Phase 3 command table.
cli.add_alias("b", "build")
cli.add_alias("w", "what")
cli.add_alias("t", "test")
cli.add_alias("s", "style")
cli.add_alias("an", "analyze")
cli.add_alias("cl", "clean")
cli.add_alias("p", "prove")
cli.add_alias("cov", "coverage")
cli.add_alias("pub", "publish")


@cli.command(name="templates")
@click.argument("path_or_target", required=False)
@click.option("--undo", is_flag=True, help="Restore files from the most recent templates backup")
@click.pass_obj
def templates(admt_ctx: Context, path_or_target: str | None, *, undo: bool) -> None:
    """Run ``redo templates``, then optionally copy implementation stubs."""
    _parse_positional(admt_ctx, path_or_target)
    _run_command(TemplatesCommand(undo=undo), admt_ctx)


cli.add_alias("tmpl", "templates")


def _run_command(cmd: Command, admt_ctx: Context) -> None:
    """Execute a Command, wiring ContainerService on demand, converting errors to exits."""
    try:
        if cmd.requires_container:
            admt_ctx.container_service, admt_ctx.path_mapper = build_container_service(admt_ctx)
        result = cmd.execute(admt_ctx)
    except AdmtError as exc:
        admt_ctx.output.error(str(exc))
        raise click.exceptions.Exit(exc.exit_code) from exc
    if result.exit_code:
        raise click.exceptions.Exit(result.exit_code)
