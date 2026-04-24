"""Tests for the Context and Result dataclasses."""

from pathlib import Path

from admt.context import Result


def test_context_defaults_are_falsy(make_context):
    ctx = make_context()
    assert ctx.verbose is False
    assert ctx.quiet is False
    assert ctx.debug is False
    assert ctx.yes is False
    assert ctx.force is False
    assert ctx.noninteractive is False
    assert ctx.target is None
    assert ctx.path is None
    assert ctx.run_all is False


def test_context_accepts_overrides(make_context):
    ctx = make_context(
        verbose=True,
        target="build/obj/foo.o",
        path=Path("/sim/foo"),
        run_all=True,
    )
    assert ctx.verbose is True
    assert ctx.target == "build/obj/foo.o"
    assert ctx.path == Path("/sim/foo")
    assert ctx.run_all is True


def test_context_services_are_attached(make_context):
    ctx = make_context()
    assert ctx.config_service is not None
    assert ctx.output is not None


def test_result_defaults():
    r = Result()
    assert r.exit_code == 0
    assert r.files_created == []
    assert r.files_modified == []
    assert r.errors == []


def test_result_lists_are_not_shared_between_instances():
    r1 = Result()
    r2 = Result()
    r1.files_created.append(Path("/sim/a"))
    r1.files_modified.append(Path("/sim/b"))
    r1.errors.append("boom")
    assert r2.files_created == []
    assert r2.files_modified == []
    assert r2.errors == []


def test_result_accepts_nonzero_exit_code():
    nonzero = 2
    r = Result(exit_code=nonzero)
    assert r.exit_code == nonzero
