"""Tests for the Command base class and ContainerPassthroughCommand stub."""

import pytest

from admt.commands.base import Command, ContainerPassthroughCommand
from admt.context import Context, Result


def test_command_is_abstract():
    with pytest.raises(TypeError):
        Command()  # type: ignore[abstract]  # intentional: verify ABC blocks instantiation


def test_container_passthrough_command_is_abstract_in_phase_0():
    # CPC does not implement execute() until Phase 3, so it remains abstract.
    with pytest.raises(TypeError):
        ContainerPassthroughCommand()  # type: ignore[abstract]  # intentional: stub


def test_container_passthrough_command_declares_requires_project():
    assert ContainerPassthroughCommand.requires_project is True


def test_command_subclass_without_execute_stays_abstract():
    class Incomplete(Command):
        name = "x"
        help = "y"
        requires_project = False

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]  # intentional: no execute()


def test_minimal_concrete_command_executes():
    class Dummy(Command):
        name = "dummy"
        help = "dummy help"
        requires_project = False

        def execute(self, context):
            return Result(exit_code=0)

    result = Dummy().execute(Context())
    assert result.exit_code == 0


def test_container_passthrough_resolve_target_returns_class_attr():
    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"

        def execute(self, context):
            return Result()

    assert Fake().resolve_target(Context()) == "all"


def test_container_passthrough_resolve_target_override_wins():
    class Fake(ContainerPassthroughCommand):
        name = "fake"
        help = "fake"
        redo_target = "all"

        def execute(self, context):
            return Result()

        def resolve_target(self, context):
            return context.target or self.redo_target

    assert Fake().resolve_target(Context(target="custom")) == "custom"
    assert Fake().resolve_target(Context()) == "all"
