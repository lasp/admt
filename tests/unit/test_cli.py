"""Tests for the CLI adapter layer: ``AliasedGroup`` and the top-level ``cli`` group."""

import click
import pytest
from click.testing import CliRunner

from admt.cli import _run_command, cli
from admt.cli_utils import AliasedGroup
from admt.commands.base import Command
from admt.context import Context, Result
from admt.exceptions import ArgumentError


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


def test_aliased_group_format_commands_shows_aliases():
    group = AliasedGroup(name="demo")

    @group.command("build")
    def _build():
        """Build things."""

    @group.command("test")
    def _test():
        """Run tests."""

    group.add_alias("b", "build")
    group.add_alias("t", "test")

    runner = CliRunner()
    # ``--help`` on the group renders the custom command section.
    result = runner.invoke(group, ["--help"])
    assert result.exit_code == 0
    assert "build (b)" in result.output
    assert "test (t)" in result.output


def test_aliased_group_format_commands_with_no_aliases():
    """A command without an alias renders as its bare name."""
    group = AliasedGroup(name="demo")

    @group.command("solo")
    def _solo():
        """Solo command."""

    runner = CliRunner()
    result = runner.invoke(group, ["--help"])
    assert "solo" in result.output
    assert "solo (" not in result.output


def test_aliased_group_format_commands_handles_empty_group():
    """Empty group emits no Commands section at all."""
    group = AliasedGroup(name="empty")
    runner = CliRunner()
    result = runner.invoke(group, ["--help"])
    assert result.exit_code == 0
    assert "Commands:" not in result.output


def test_aliased_group_format_commands_hides_hidden_commands():
    """A command registered with ``hidden=True`` does not appear in --help."""
    group = AliasedGroup(name="demo")

    @group.command("visible")
    def _visible():
        """Visible command."""

    @group.command("ghost", hidden=True)
    def _ghost():
        """Ghost command."""

    runner = CliRunner()
    result = runner.invoke(group, ["--help"])
    assert "visible" in result.output
    assert "ghost" not in result.output


def _run_with_probe(args):
    @cli.command("_probe", hidden=True)
    @click.pass_obj
    def _probe(admt_ctx):
        click.echo(
            f"v={admt_ctx.verbose} q={admt_ctx.quiet} "
            f"d={admt_ctx.debug} y={admt_ctx.yes} f={admt_ctx.force}"
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


def test_run_command_propagates_non_zero_exit_code():
    """A Command that returns Result(exit_code!=0) surfaces as a Click exit."""
    sentinel_exit = 7

    class _Returner(Command):
        name = "_returner"
        help = "test"
        requires_project = False

        def execute(self, context):
            return Result(exit_code=sentinel_exit)

    admt_ctx = Context.__new__(Context)  # bare instance; output is not referenced
    admt_ctx.output = None  # unused on the success-but-nonzero path
    with pytest.raises(click.exceptions.Exit) as exc_info:
        _run_command(_Returner(), admt_ctx)
    assert exc_info.value.exit_code == sentinel_exit


# ----- exit-code mapping (AliasedGroup.main, ARCHITECTURE §Exit Codes) -----


def _mapping_group() -> AliasedGroup:
    """A scratch group with one command per ``main()`` outcome."""
    group = AliasedGroup(name="demo")

    @group.command()
    def ok() -> None: ...

    @group.command()
    @click.pass_context
    def bail(ctx: click.Context) -> None:
        ctx.exit(7)

    @group.command()
    def boom() -> None:
        msg = "generic failure"
        raise click.ClickException(msg)

    @group.command()
    def stop() -> None:
        raise click.exceptions.Abort

    return group


def _exit_code(group: AliasedGroup, args: list[str]) -> int:
    with pytest.raises(SystemExit) as excinfo:
        group.main(args, prog_name="demo")
    return excinfo.value.code


def test_group_main_maps_usage_error_to_argument_exit_code(capsys):
    """Parse-time rejections are argument errors (exit 3), not Click's 2."""
    code = _exit_code(_mapping_group(), ["ok", "unexpected-extra"])
    assert code == ArgumentError.exit_code
    err = capsys.readouterr().err
    assert "Usage:" in err  # Click's own formatting is preserved
    assert "unexpected-extra" in err


def test_group_main_preserves_other_click_exception_codes(capsys):
    code = _exit_code(_mapping_group(), ["boom"])
    assert code == click.ClickException.exit_code
    assert "generic failure" in capsys.readouterr().err


def test_group_main_maps_abort_to_sigint_code(capsys):
    expected = 130
    assert _exit_code(_mapping_group(), ["stop"]) == expected
    assert "Aborted!" in capsys.readouterr().err


def test_group_main_passes_ctx_exit_code_through():
    """``ctx.exit(N)`` -- how ``_run_command`` maps AdmtErrors -- survives."""
    expected = 7
    assert _exit_code(_mapping_group(), ["bail"]) == expected


def test_group_main_clean_return_exits_zero():
    assert _exit_code(_mapping_group(), ["ok"]) == 0
