"""Tests for the Command base class and ContainerPassthroughCommand."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from admt.commands.base import Command, ContainerPassthroughCommand
from admt.context import Result
from admt.exceptions import ContainerError
from admt.services.container import ContainerService
from admt.services.path_mapper import PathMapperService


def test_command_is_abstract():
    with pytest.raises(TypeError):
        Command()  # type: ignore[abstract]  # intentional: verify ABC blocks instantiation


def test_container_passthrough_command_is_concrete_after_phase_3():
    # Phase 3 completed execute(); CPC is now instantiable on its own
    # (though subclasses set redo_target and sometimes override
    # resolve_target). It still asserts via ``requires_container = True``.
    cpc = ContainerPassthroughCommand()
    assert cpc.requires_container is True
    assert cpc.requires_project is True


def test_container_passthrough_command_declares_requires_project():
    assert ContainerPassthroughCommand.requires_project is True


def test_command_subclass_without_execute_stays_abstract():
    class Incomplete(Command):
        name = "x"
        help = "y"
        requires_project = False

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]  # intentional: no execute()


def test_minimal_concrete_command_executes(make_context):
    class Dummy(Command):
        name = "dummy"
        help = "dummy help"
        requires_project = False

        def execute(self, context):
            return Result(exit_code=0)

    result = Dummy().execute(make_context())
    assert result.exit_code == 0


def test_container_passthrough_resolve_target_returns_class_attr(make_context):
    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"

        def execute(self, context):
            return Result()

    assert Fake().resolve_target(make_context()) == "all"


def test_container_passthrough_resolve_target_override_wins(make_context):
    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"

        def execute(self, context):
            return Result()

        def resolve_target(self, context):
            return context.target or self.redo_target

    assert Fake().resolve_target(make_context(target="custom")) == "custom"
    assert Fake().resolve_target(make_context()) == "all"


def _passthrough_ctx(make_context, *, path: Path | None = None, **flags):
    """Build a Context populated enough to drive ContainerPassthroughCommand."""
    container = MagicMock(spec=ContainerService)
    container.exec.return_value = 0
    mapper = PathMapperService({Path("/sim/proj"): Path("/home/user/proj")})
    return make_context(
        path_mapper=mapper, container_service=container, path=path, **flags
    ), container


def test_passthrough_raises_when_container_not_wired(make_context):
    # container_service is None -> CPC errors before trying to exec.
    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"

    mapper = PathMapperService({Path("/sim/proj"): Path("/home/user/proj")})
    ctx = make_context(path_mapper=mapper, path=Path("/sim/proj"))
    with pytest.raises(ContainerError, match="CLI bug"):
        Fake().execute(ctx)


def test_passthrough_assembles_cd_and_redo(make_context):
    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"

    ctx, container = _passthrough_ctx(make_context, path=Path("/sim/proj/src/foo"))
    result = Fake().execute(ctx)
    assert result.exit_code == 0
    # The redo command starts with the mapped container path.
    call = container.exec.call_args
    assert call.args[0] == "cd /home/user/proj/src/foo && redo all"
    assert call.kwargs["merge_stderr"] is True
    assert call.kwargs["capture_output"] is False


def test_passthrough_prepends_debug_when_context_debug(make_context):
    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "test"

    ctx, container = _passthrough_ctx(make_context, path=Path("/sim/proj"), debug=True)
    Fake().execute(ctx)
    assert "DEBUG=1 redo test" in container.exec.call_args.args[0]


def test_passthrough_threads_quiet_as_capture_output(make_context):
    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"

    ctx, container = _passthrough_ctx(make_context, path=Path("/sim/proj"), quiet=True)
    Fake().execute(ctx)
    assert container.exec.call_args.kwargs["capture_output"] is True


def test_passthrough_streaming_transform_tints_verb_only(make_context):
    """Streaming mode wraps only the verb through ``output.admt`` so deeper
    path text stays in the terminal's default color."""

    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"

    ctx, container = _passthrough_ctx(make_context, path=Path("/sim/proj"))
    ctx.output.admt.side_effect = lambda message: f"<GOLD>{message}<RESET>"
    Fake().execute(ctx)
    transform = container.exec.call_args.kwargs["line_transform"]
    # ``redo    build/src/foo.adb`` -> verb ``build`` gold, target plain.
    assert transform("redo    build/src/foo.adb") == "<GOLD>build<RESET> build/src/foo.adb"


def test_passthrough_streaming_transform_drops_top_level_redo_header(make_context):
    """The 2-space ``redo  <target>`` header is still dropped via None."""

    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"

    ctx, container = _passthrough_ctx(make_context, path=Path("/sim/proj"))
    Fake().execute(ctx)
    transform = container.exec.call_args.kwargs["line_transform"]
    assert transform("redo  all") is None


def test_passthrough_streaming_transform_leaves_pass_through_uncolored(make_context):
    """Pass-through lines (compiler output) keep their original bytes; no
    call to ``output.admt`` is made for them so their own ANSI survives."""

    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"

    ctx, container = _passthrough_ctx(make_context, path=Path("/sim/proj"))
    Fake().execute(ctx)
    transform = container.exec.call_args.kwargs["line_transform"]
    original = "gnatmake: warning: something\n"
    assert transform(original) == original
    # admt() was never invoked with the pass-through text.
    admt_calls = [call.args[0] for call in ctx.output.admt.call_args_list]
    assert "gnatmake: warning: something" not in admt_calls
    assert "gnatmake: warning: something\n" not in admt_calls


def test_passthrough_skips_line_transform_in_quiet_mode(make_context):
    """Quiet mode captures; the transform would never see those lines."""

    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"

    ctx, container = _passthrough_ctx(make_context, path=Path("/sim/proj"), quiet=True)
    Fake().execute(ctx)
    assert container.exec.call_args.kwargs["line_transform"] is None


def test_passthrough_forwards_exec_exit_code(make_context):
    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"

    sentinel = 13
    ctx, container = _passthrough_ctx(make_context, path=Path("/sim/proj"))
    container.exec.return_value = sentinel
    result = Fake().execute(ctx)
    assert result.exit_code == sentinel


def test_passthrough_uses_resolve_target_output(make_context):
    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "test"

        def resolve_target(self, context):
            return "test_all" if context.run_all else self.redo_target

    ctx, container = _passthrough_ctx(make_context, path=Path("/sim/proj"), run_all=True)
    Fake().execute(ctx)
    assert "redo test_all" in container.exec.call_args.args[0]


def test_passthrough_status_verb_emits_static_line(make_context):
    """Commands with ``status_verb`` print ``:<verb>...`` before exec, in admt gold."""

    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"
        status_verb = "building"

    ctx, _container = _passthrough_ctx(make_context, path=Path("/sim/proj"))
    Fake().execute(ctx)
    info_lines = [call.args[0] for call in ctx.output.info.call_args_list]
    admt_calls = [call.args[0] for call in ctx.output.admt.call_args_list]
    assert "building..." in info_lines
    # Opening status is routed through admt() so it lands in gold when
    # stdout is a color-friendly TTY.
    assert "building..." in admt_calls


def test_passthrough_emits_done_after_successful_exec(make_context):
    """Status-verb commands print ``:done.`` in admt gold after exec returns 0."""

    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"
        status_verb = "building"

    ctx, _container = _passthrough_ctx(make_context, path=Path("/sim/proj"))
    Fake().execute(ctx)
    info_lines = [call.args[0] for call in ctx.output.info.call_args_list]
    admt_calls = [call.args[0] for call in ctx.output.admt.call_args_list]
    # Opening status, then :done. -- :done. lands last and goes through admt().
    assert info_lines[-1] == "done."
    assert "done." in admt_calls


def test_passthrough_suppresses_done_on_non_zero_exit(make_context):
    """Failure path: ``Failed (exit N)`` is the signal; no ``:done.`` on stdout."""

    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"
        status_verb = "building"

    ctx, container = _passthrough_ctx(make_context, path=Path("/sim/proj"))
    container.exec.return_value = 2
    Fake().execute(ctx)
    info_lines = [call.args[0] for call in ctx.output.info.call_args_list]
    assert "done." not in info_lines


def test_passthrough_without_status_verb_emits_no_status_line(make_context):
    """Default ``status_verb = None`` means neither opening nor closing line fires."""

    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"
        # status_verb not set -> inherits None

    ctx, _container = _passthrough_ctx(make_context, path=Path("/sim/proj"))
    Fake().execute(ctx)
    info_lines = [call.args[0] for call in ctx.output.info.call_args_list]
    assert not any(line.endswith("...") for line in info_lines)
    assert "done." not in info_lines
