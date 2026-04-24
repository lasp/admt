"""Tests for the CLI adapter layer: ``AliasedGroup`` and the top-level ``cli`` group."""

import click
from click.testing import CliRunner

from admt.cli import AliasedGroup, cli


def test_cli_help_prints_tool_description():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Adamant Multitool" in result.output


def test_cli_version_prints_version_string():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_aliased_group_resolves_registered_alias():
    group = AliasedGroup()

    @group.command("real")
    def _real():
        pass

    group.add_alias("r", "real")
    ctx = click.Context(group)
    resolved = group.get_command(ctx, "r")
    assert resolved is not None
    assert resolved.name == "real"


def test_aliased_group_passes_through_unaliased_names():
    group = AliasedGroup()

    @group.command("real")
    def _real():
        pass

    ctx = click.Context(group)
    resolved = group.get_command(ctx, "real")
    assert resolved is not None
    assert resolved.name == "real"


def test_aliased_group_returns_none_for_unknown_command():
    group = AliasedGroup()
    ctx = click.Context(group)
    assert group.get_command(ctx, "ghost") is None


def _run_with_probe(args):
    @cli.command("_probe", hidden=True)
    @click.pass_context
    def _probe(ctx):
        flags = ctx.obj
        click.echo(
            f"v={flags['verbose']} q={flags['quiet']} "
            f"d={flags['debug']} y={flags['yes']} f={flags['force']}"
        )

    try:
        runner = CliRunner()
        return runner.invoke(cli, [*args, "_probe"])
    finally:
        cli.commands.pop("_probe", None)


def test_cli_callback_populates_all_flags_from_defaults():
    result = _run_with_probe([])
    assert result.exit_code == 0
    assert "v=False q=False d=False y=False f=False" in result.output


def test_cli_callback_debug_implies_verbose():
    result = _run_with_probe(["-d"])
    assert result.exit_code == 0
    assert "v=True" in result.output
    assert "d=True" in result.output


def test_cli_callback_verbose_and_quiet_coexist():
    result = _run_with_probe(["-v", "-q"])
    assert result.exit_code == 0
    assert "v=True q=True" in result.output


def test_cli_callback_yes_and_force_flags():
    result = _run_with_probe(["-y", "-f"])
    assert result.exit_code == 0
    assert "y=True f=True" in result.output
