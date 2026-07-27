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
from admt.exceptions import ArgumentError, ConfigError, PathNotMappedError
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
    assert cmd.resolve_targets(ctx_base) == [base_target]
    assert cmd.resolve_targets(ctx_all) == [all_target]


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
    with pytest.raises(ArgumentError, match="is not a directory"):
        cls().resolve_targets(make_context(targets=("../nonexistent/bogus",)))


def test_build_command_uses_context_targets_when_present(make_context):
    cmd = BuildCommand()
    ctx = make_context(targets=("build/obj/foo.o",))
    assert cmd.resolve_targets(ctx) == ["build/obj/foo.o"]


def test_build_command_defaults_to_all(make_context):
    cmd = BuildCommand()
    assert cmd.resolve_targets(make_context()) == ["all"]


def test_build_command_forwards_multiple_targets_in_order(make_context):
    ctx = make_context(targets=("a.elf", "b.elf", "c.elf"))
    assert BuildCommand().resolve_targets(ctx) == ["a.elf", "b.elf", "c.elf"]


def test_build_command_rejects_directory_among_multiple_targets(
    make_context, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "somedir").mkdir()
    ctx = make_context(targets=("a.elf", "somedir"))
    with pytest.raises(ArgumentError, match="sole positional"):
        BuildCommand().resolve_targets(ctx)


def test_build_command_maps_absolute_target_through_mounts(make_context):
    mapper = PathMapperService({Path("/sim/proj"): Path("/home/user/proj")})
    ctx = make_context(path_mapper=mapper, targets=("/sim/proj/views/build/dot/x.dot",))
    assert BuildCommand().resolve_targets(ctx) == ["/home/user/proj/views/build/dot/x.dot"]


def test_build_command_maps_absolute_targets_among_relative_ones(make_context):
    mapper = PathMapperService({Path("/sim/proj"): Path("/home/user/proj")})
    ctx = make_context(path_mapper=mapper, targets=("a.elf", "/sim/proj/b.dot"))
    assert BuildCommand().resolve_targets(ctx) == ["a.elf", "/home/user/proj/b.dot"]


def test_build_command_absolute_target_outside_mounts_errors(make_context):
    mapper = PathMapperService({Path("/sim/proj"): Path("/home/user/proj")})
    ctx = make_context(path_mapper=mapper, targets=("/elsewhere/x.dot",))
    with pytest.raises(PathNotMappedError, match="not under any volume mount"):
        BuildCommand().resolve_targets(ctx)


def test_build_command_absolute_target_without_mapper_errors(make_context):
    ctx = make_context(targets=("/sim/proj/x.dot",))
    with pytest.raises(ConfigError, match="requires an active project"):
        BuildCommand().resolve_targets(ctx)


# ----- generated-source hint (BuildCommand failure path) -----


def _failing_build_ctx(make_context, tmp_path, *, targets):
    container = MagicMock(spec=ContainerService)
    container.exec.return_value = 1
    mapper = PathMapperService({tmp_path: Path("/home/user/proj")})
    return make_context(
        path_mapper=mapper,
        container_service=container,
        path=tmp_path,
        targets=targets,
    )


def test_build_failure_hints_at_build_src_twin(make_context, tmp_path):
    (tmp_path / "src" / "types" / "build" / "src").mkdir(parents=True)
    (tmp_path / "src" / "types" / "build" / "src" / "foo.ads").touch()
    ctx = _failing_build_ctx(make_context, tmp_path, targets=("src/types/foo.ads",))
    result = BuildCommand().execute(ctx)
    assert result.exit_code == 1
    info_lines = [c.args[0] for c in ctx.output.info.call_args_list]
    assert (
        "Generated sources are built under build/src/; "
        "try 'admt build src/types/build/src/foo.ads'." in info_lines
    )


def test_build_failure_hints_for_each_qualifying_target(make_context, tmp_path):
    """One redo invocation can fail with several targets; every existing twin is suggested."""
    (tmp_path / "build" / "src").mkdir(parents=True)
    (tmp_path / "build" / "src" / "foo.ads").touch()
    (tmp_path / "build" / "src" / "bar.adb").touch()
    ctx = _failing_build_ctx(make_context, tmp_path, targets=("foo.ads", "bar.adb"))
    BuildCommand().execute(ctx)
    info_lines = [c.args[0] for c in ctx.output.info.call_args_list]
    expected_hints = 2
    hint_count = sum("Generated sources are built under build/src/" in line for line in info_lines)
    assert hint_count == expected_hints


def test_build_failure_no_hint_when_candidate_absent(make_context, tmp_path):
    ctx = _failing_build_ctx(make_context, tmp_path, targets=("src/types/foo.ads",))
    BuildCommand().execute(ctx)
    info_lines = [c.args[0] for c in ctx.output.info.call_args_list]
    assert not any("build/src" in line for line in info_lines)


@pytest.mark.parametrize(
    "targets",
    [
        (),  # no target supplied
        ("foo_type_ranges.elf",),  # not an Ada source
        ("/abs/host/foo.ads",),  # absolute paths are host paths, not source-relative names
        ("build/src/foo.ads",),  # already routed through a build dir
    ],
)
def test_build_failure_hint_gates(make_context, tmp_path, targets):
    ctx = _failing_build_ctx(make_context, tmp_path, targets=targets)
    assert BuildCommand._generated_source_hints(ctx) == []


def test_build_success_prints_no_hint(make_context, tmp_path):
    (tmp_path / "build" / "src").mkdir(parents=True)
    (tmp_path / "build" / "src" / "foo.ads").touch()
    ctx = _failing_build_ctx(make_context, tmp_path, targets=("foo.ads",))
    ctx.container_service.exec.return_value = 0
    BuildCommand().execute(ctx)
    info_lines = [c.args[0] for c in ctx.output.info.call_args_list]
    assert not any("Generated sources" in line for line in info_lines)


def test_what_command_ignores_run_all(make_context):
    cmd = WhatCommand()
    # supports_all is False; --all has no effect.
    assert cmd.resolve_targets(make_context(run_all=True)) == ["what"]


def test_prove_command_ignores_run_all(make_context):
    assert ProveCommand().resolve_targets(make_context(run_all=True)) == ["prove"]


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
