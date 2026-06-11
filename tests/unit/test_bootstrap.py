"""Tests for ``admt.bootstrap`` -- Context + ContainerService wiring."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from admt.bootstrap import build_container_service, build_context
from admt.services.config import ProjectConfig
from admt.services.container import ContainerService
from admt.services.path_mapper import PathMapperService


def _project() -> ProjectConfig:
    return ProjectConfig(
        name="proj",
        compose_file=Path("/sim/proj/docker/docker-compose.yml"),
        compose_file_mtime=0,
        env_file=None,
        env_file_mtime=0,
        service_name="svc",
        container_name="proj_container",
        project_root=Path("/sim/proj"),
        container_home=Path("/home/user"),
        volume_mounts={Path("/sim/proj"): Path("/home/user/proj")},
        activate_script=Path("/home/user/proj/env/activate"),
    )


def test_build_context_sets_global_flags_and_noninteractive_env(monkeypatch):
    monkeypatch.setenv("ADMT_NONINTERACTIVE", "1")
    ctx = build_context(verbose=True, quiet=False, debug=False, yes=True, force=False)
    assert ctx.verbose is True
    assert ctx.yes is True
    assert ctx.noninteractive is True
    assert ctx.config_service is not None
    assert ctx.output is not None


def test_build_context_noninteractive_false_when_env_unset(monkeypatch):
    monkeypatch.delenv("ADMT_NONINTERACTIVE", raising=False)
    ctx = build_context(verbose=False, quiet=False, debug=False, yes=False, force=False)
    assert ctx.noninteractive is False


@pytest.mark.parametrize("value", ["", "0"])
def test_build_context_noninteractive_zero_or_empty_evaluates_off(monkeypatch, value):
    """ADMT_NONINTERACTIVE=0 (or empty) is OFF, matching POSIX shell convention.

    Reverses the original "any non-empty value activates" rule; "0" now
    means explicitly off so users with ``ADMT_NONINTERACTIVE=0`` in their
    shell init don't trip into agent-mode unintentionally.
    """
    monkeypatch.setenv("ADMT_NONINTERACTIVE", value)
    ctx = build_context(verbose=False, quiet=False, debug=False, yes=False, force=False)
    assert ctx.noninteractive is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "anything", "2"])
def test_build_context_noninteractive_any_other_value_evaluates_on(monkeypatch, value):
    """Any non-zero, non-empty ADMT_NONINTERACTIVE value activates the mode."""
    monkeypatch.setenv("ADMT_NONINTERACTIVE", value)
    ctx = build_context(verbose=False, quiet=False, debug=False, yes=False, force=False)
    assert ctx.noninteractive is True


def test_build_container_service_returns_container_and_mapper_tuple():
    """Returns (ContainerService, PathMapperService) explicitly.

    Replaces the previous side-effect form where the function mutated
    ``context.path_mapper`` as a hidden side effect of returning a
    ContainerService. The caller (``cli._run_command``) now assigns both
    onto the Context, keeping the population of Context attributes in
    the CLI adapter where it belongs.
    """
    fake_ctx = MagicMock()
    fake_ctx.config_service.get_active_project.return_value = _project()
    container, mapper = build_container_service(fake_ctx)
    assert isinstance(container, ContainerService)
    assert isinstance(mapper, PathMapperService)
    fake_ctx.config_service.get_active_project.assert_called_once()


def test_build_container_service_does_not_mutate_context():
    """Regression: confirm the function no longer touches ``context.path_mapper``."""
    fake_ctx = MagicMock()
    fake_ctx.config_service.get_active_project.return_value = _project()
    # Sentinel: if the function mutated context.path_mapper, this would change.
    fake_ctx.path_mapper = "untouched-sentinel"
    build_container_service(fake_ctx)
    assert fake_ctx.path_mapper == "untouched-sentinel"
