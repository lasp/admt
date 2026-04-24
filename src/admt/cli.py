"""Click CLI adapter -- thin, no business logic.

Phase 0 surface: a single top-level group with the five global flags and
an ``AliasedGroup`` class ready for Phase 1's ``env`` subgroup alias and
Phase 3's single-letter command aliases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from typing import Any


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
    ctx.ensure_object(dict)
    ctx.obj.update(verbose=verbose or debug, quiet=quiet, debug=debug, yes=yes, force=force)
