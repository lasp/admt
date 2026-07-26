"""Metadata + target-resolution tests for the 9 passthrough commands."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from admt.commands.analyze import AnalyzeCommand
from admt.commands.build import BuildCommand
from admt.commands.clean import CleanCommand
from admt.commands.coverage import CoverageCommand
from admt.commands.prove import ProveCommand
from admt.commands.publish import PublishCommand
from admt.commands.style import StyleCommand
from admt.commands.templates import TemplatesCommand
from admt.commands.test_cmd import TestCommand
from admt.commands.what import WhatCommand
from admt.exceptions import ArgumentError
from admt.services.container import ContainerService
from admt.services.path_mapper import PathMapperService


@pytest.mark.parametrize(
    ("cls", "expected_name", "expected_target", "has_all"),
    [
        (BuildCommand, "build", "all", False),
        (WhatCommand, "what", "what", False),
        (TestCommand, "test", "test", True),
        (StyleCommand, "style", "style", True),
        (AnalyzeCommand, "analyze", "analyze", True),
        (CleanCommand, "clean", "clean", True),
        (ProveCommand, "prove", "prove", False),
        (CoverageCommand, "coverage", "coverage", True),
        (PublishCommand, "publish", "publish", True),
    ],
)
def test_passthrough_commands_metadata(cls, expected_name, expected_target, has_all):
    assert cls.name == expected_name
    assert cls.help
    assert cls.requires_project is True
    assert cls.requires_container is True
    assert cls.redo_target == expected_target
    assert cls.supports_all is has_all


@pytest.mark.parametrize(
    ("cls", "base_target", "all_target"),
    [
        (TestCommand, "test", "test_all"),
        (StyleCommand, "style", "style_all"),
        (AnalyzeCommand, "analyze", "analyze_all"),
        (CleanCommand, "clean", "clean_all"),
        (CoverageCommand, "coverage", "coverage_all"),
        (PublishCommand, "publish", "publish_all"),
    ],
)
def test_supports_all_commands_switch_target_on_run_all(cls, base_target, all_target, make_context):
    ctx_base = make_context(run_all=False)
    ctx_all = make_context(run_all=True)
    cmd = cls()
    assert cmd.resolve_target(ctx_base) == base_target
    assert cmd.resolve_target(ctx_all) == all_target


@pytest.mark.parametrize(
    "cls",
    [
        WhatCommand,
        TestCommand,
        StyleCommand,
        AnalyzeCommand,
        CleanCommand,
        ProveCommand,
        CoverageCommand,
        PublishCommand,
        TemplatesCommand,
    ],
)
def test_fixed_target_commands_reject_positional_target(cls, make_context):
    """A non-directory positional is an argument error, never silently dropped."""
    with pytest.raises(ArgumentError, match="only meaningful for 'admt build'"):
        cls().resolve_target(make_context(target="../nonexistent/bogus"))


def test_build_command_uses_context_target_when_present(make_context):
    cmd = BuildCommand()
    assert cmd.resolve_target(make_context(target="build/obj/foo.o")) == "build/obj/foo.o"


def test_build_command_defaults_to_all(make_context):
    cmd = BuildCommand()
    assert cmd.resolve_target(make_context()) == "all"


def test_what_command_ignores_run_all(make_context):
    cmd = WhatCommand()
    # supports_all is False; --all has no effect.
    assert cmd.resolve_target(make_context(run_all=True)) == "what"


def test_prove_command_ignores_run_all(make_context):
    assert ProveCommand().resolve_target(make_context(run_all=True)) == "prove"


# ----- End-to-end execute wiring (one representative command) -----


def test_test_command_with_all_builds_cd_and_test_all_cmd(make_context):
    container = MagicMock(spec=ContainerService)
    container.exec.return_value = 0
    mapper = PathMapperService({Path("/sim/proj"): Path("/home/user/proj")})
    ctx = make_context(
        path_mapper=mapper,
        container_service=container,
        path=Path("/sim/proj/src/components/ccsds_router"),
        run_all=True,
    )
    result = TestCommand().execute(ctx)
    assert result.exit_code == 0
    command = container.exec.call_args.args[0]
    assert command == "cd /home/user/proj/src/components/ccsds_router && redo test_all"
