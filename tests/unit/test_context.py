"""Tests for the Context and Result dataclasses."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from admt.context import Result
from admt.exceptions import ConfigError, PathNotMappedError
from admt.services.path_mapper import PathMapperService


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


def test_context_path_mapper_defaults_to_none(make_context):
    ctx = make_context()
    assert ctx.path_mapper is None


def test_resolve_container_path_uses_cwd_by_default(make_context, monkeypatch):
    base = Path("/sim/proj")
    mapper = PathMapperService({base: Path("/home/user/proj")})
    ctx = make_context(path_mapper=mapper)
    # Simulate "user is in /sim/proj/src/foo"; rely on monkeypatched cwd.
    monkeypatch.setattr("admt.context.Path.cwd", lambda: base / "src" / "foo")
    assert ctx.resolve_container_path() == Path("/home/user/proj/src/foo")


def test_resolve_container_path_uses_explicit_path(make_context):
    base = Path("/sim/proj")
    mapper = PathMapperService({base: Path("/home/user/proj")})
    ctx = make_context(path_mapper=mapper, path=base / "src" / "bar")
    assert ctx.resolve_container_path() == Path("/home/user/proj/src/bar")


def test_resolve_container_path_raises_without_path_mapper(make_context):
    ctx = make_context()  # path_mapper not wired
    with pytest.raises(ConfigError, match="admt env init"):
        ctx.resolve_container_path()


def test_resolve_container_path_propagates_unmapped_error(make_context):
    mapper = PathMapperService({Path("/sim/other"): Path("/home/user/other")})
    ctx = make_context(path_mapper=mapper, path=Path("/sim/not-under-any-mount/foo"))
    # PathNotMappedError is raised by the mapper; Context just lets it propagate.
    with pytest.raises(PathNotMappedError):
        ctx.resolve_container_path()


def test_make_context_factory_accepts_path_mapper_override(make_context):
    sentinel = MagicMock(spec=PathMapperService)
    ctx = make_context(path_mapper=sentinel)
    assert ctx.path_mapper is sentinel


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
