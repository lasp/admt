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

from admt.bootstrap import build_context
from admt.commands.env import EnvInitCommand, EnvUseCommand
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


def _run_command(cmd: Command, admt_ctx: Context) -> None:
    """Execute a Command and translate errors to Click exits."""
    try:
        result = cmd.execute(admt_ctx)
    except AdmtError as exc:
        admt_ctx.output.error(str(exc))
        raise click.exceptions.Exit(exc.exit_code) from exc
    if result.exit_code:
        raise click.exceptions.Exit(result.exit_code)
