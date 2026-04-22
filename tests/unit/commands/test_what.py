"""Tests for WhatCommand -- output transformation + execute wiring."""

from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock

import pytest

from admt.adapters.docker import CommandResult
from admt.commands.what import WhatCommand
from admt.exceptions import ContainerError
from admt.services.container import ContainerService
from admt.services.path_mapper import PathMapperService

# ----- _transform: pure string-processing -----


def test_transform_drops_redo_header():
    # "redo  what " (two spaces) is the status header.
    out = "redo  what \nredo all\n"
    assert WhatCommand._transform(out) == ["admt build"]


def test_transform_maps_known_named_targets():
    out = dedent(
        """\
        redo  what
        redo all
        redo test
        redo style
        redo analyze
        redo clean
        redo coverage
        redo publish
        redo prove
        redo templates
        """
    )
    assert WhatCommand._transform(out) == [
        "admt build",
        "admt test",
        "admt style",
        "admt analyze",
        "admt clean",
        "admt coverage",
        "admt publish",
        "admt prove",
        "admt templates",
    ]


def test_transform_maps_all_suffix_variants():
    out = dedent(
        """\
        redo test_all
        redo style_all
        redo analyze_all
        redo clean_all
        redo coverage_all
        redo publish_all
        """
    )
    assert WhatCommand._transform(out) == [
        "admt test --all",
        "admt style --all",
        "admt analyze --all",
        "admt clean --all",
        "admt coverage --all",
        "admt publish --all",
    ]


def test_transform_fallback_to_admt_build_target():
    out = dedent(
        """\
        redo pretty
        redo clear_cache
        redo targets
        redo build/obj/Linux/foo.o
        redo build/dot/code_interface.dot
        """
    )
    assert WhatCommand._transform(out) == [
        "admt build pretty",
        "admt build clear_cache",
        "admt build targets",
        "admt build build/obj/Linux/foo.o",
        "admt build build/dot/code_interface.dot",
    ]


def test_transform_skips_blank_lines():
    out = "redo  what\n\nredo all\n\n\nredo test\n"
    assert WhatCommand._transform(out) == ["admt build", "admt test"]


def test_transform_strips_ansi_escape_codes():
    # redo emits green ANSI escapes on its header line in a TTY.
    out = "\x1b[32mredo  what\x1b[0m\n\x1b[32mredo\x1b[0m all\n"
    transformed = WhatCommand._transform(out)
    # The header is dropped; the listing entry is transformed.
    assert transformed == ["admt build"]


def test_transform_passes_through_non_redo_lines():
    out = dedent(
        """\
        redo all
        Note: some message
        redo test
        """
    )
    assert WhatCommand._transform(out) == [
        "admt build",
        "Note: some message",
        "admt test",
    ]


def test_transform_handles_empty_output():
    assert WhatCommand._transform("") == []


# ----- execute: full command flow -----


def test_execute_captures_redo_output_and_transforms(make_context):
    container = MagicMock(spec=ContainerService)
    container.exec_captured.return_value = CommandResult(
        returncode=0,
        stdout="redo  what\nredo all\nredo test_all\n",
    )
    mapper = PathMapperService({Path("/sim/proj"): Path("/home/user/proj")})
    ctx = make_context(path_mapper=mapper, container_service=container, path=Path("/sim/proj"))
    result = WhatCommand().execute(ctx)
    assert result.exit_code == 0
    # exec_captured was called with the redo-what command.
    redo_cmd = container.exec_captured.call_args.args[0]
    assert redo_cmd == "cd /home/user/proj && redo what"
    # The transformed list was emitted.
    info_lines = [call.args[0] for call in ctx.output.info.call_args_list]
    assert "admt build" in info_lines
    assert "admt test --all" in info_lines


def test_execute_propagates_non_zero_exit_and_emits_captured(make_context):
    sentinel_exit = 2
    container = MagicMock(spec=ContainerService)
    container.exec_captured.return_value = CommandResult(
        returncode=sentinel_exit,
        stdout="error: something is wrong\n",
    )
    mapper = PathMapperService({Path("/sim/proj"): Path("/home/user/proj")})
    ctx = make_context(path_mapper=mapper, container_service=container, path=Path("/sim/proj"))
    result = WhatCommand().execute(ctx)
    assert result.exit_code == sentinel_exit
    # Non-zero path emits captured output via emit_captured.
    ctx.output.emit_captured.assert_called_once()


def test_execute_raises_when_container_service_missing(make_context):
    mapper = PathMapperService({Path("/sim/proj"): Path("/home/user/proj")})
    ctx = make_context(path_mapper=mapper, path=Path("/sim/proj"))  # no container_service
    with pytest.raises(ContainerError, match="CLI bug"):
        WhatCommand().execute(ctx)


def test_execute_failure_without_stdout_skips_emit(make_context):
    """Branch coverage: when captured stdout is empty on failure, don't emit."""
    container = MagicMock(spec=ContainerService)
    container.exec_captured.return_value = CommandResult(returncode=1, stdout="")
    mapper = PathMapperService({Path("/sim/proj"): Path("/home/user/proj")})
    ctx = make_context(path_mapper=mapper, container_service=container, path=Path("/sim/proj"))
    result = WhatCommand().execute(ctx)
    assert result.exit_code == 1
    ctx.output.emit_captured.assert_not_called()


# ----- metadata -----


def test_what_command_metadata_has_no_status_verb():
    # WhatCommand intentionally doesn't emit "admt <gerund>..." -- output is fast
    # and self-explanatory, and a prefix line would clutter the listing.
    assert WhatCommand.status_verb is None
    assert WhatCommand.name == "what"
    assert WhatCommand.redo_target == "what"
