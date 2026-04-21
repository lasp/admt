"""Click utilities used by the CLI adapter.

Lives outside ``cli.py`` so its methods are not subject to the 15-line
function-body cap enforced on the CLI adapter itself. ``cli.py``
``import``s ``AliasedGroup`` from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from typing import Any


class AliasedGroup(click.Group):
    """Click group that resolves command aliases and shows them in ``--help``.

    Aliases are registered via ``add_alias("b", "build")``. ``get_command``
    resolves the alias to its target before dispatch, and
    ``format_commands`` renders ``build (b)`` entries in the help output.
    """

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

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """Render commands with their aliases appended as ``name (a, b)``."""
        reverse: dict[str, list[str]] = {}
        for alias, cmd_name in self._aliases.items():
            reverse.setdefault(cmd_name, []).append(alias)
        rows = self._collect_command_rows(ctx, formatter, reverse)
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)

    def _collect_command_rows(
        self,
        ctx: click.Context,
        formatter: click.HelpFormatter,
        reverse: dict[str, list[str]],
    ) -> list[tuple[str, str]]:
        entries: list[tuple[str, click.Command]] = []
        for subcommand in self.list_commands(ctx):
            cmd = self.get_command(ctx, subcommand)
            if cmd is None or cmd.hidden:
                continue
            display = self._format_display_name(subcommand, reverse)
            entries.append((display, cmd))
        if not entries:
            return []
        limit = formatter.width - 6 - max(len(name) for name, _ in entries)
        return [(name, cmd.get_short_help_str(limit)) for name, cmd in entries]

    @staticmethod
    def _format_display_name(name: str, reverse: dict[str, list[str]]) -> str:
        if name not in reverse:
            return name
        aliases_str = ", ".join(sorted(reverse[name]))
        return f"{name} ({aliases_str})"
