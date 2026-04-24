"""Tests for ``admt.bootstrap`` -- Context + ContainerService wiring."""

from pathlib import Path
from unittest.mock import MagicMock

from admt.bootstrap import build_container_service, build_context
from admt.services.config import ProjectConfig
from admt.services.container import ContainerService
from admt.services.path_mapper import PathMapperService


def _project() -> ProjectConfig:
    return ProjectConfig(
        name="proj",
        compose_file=Path("/sim/proj/docker/docker-compose.yml"),
        compose_file_mtime=0,
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


def test_build_container_service_wires_docker_adapter():
    fake_ctx = MagicMock()
    fake_ctx.config_service.get_active_project.return_value = _project()
    container = build_container_service(fake_ctx)
    assert isinstance(container, ContainerService)
    fake_ctx.config_service.get_active_project.assert_called_once()


def test_build_container_service_attaches_path_mapper_to_context():
    fake_ctx = MagicMock()
    fake_ctx.config_service.get_active_project.return_value = _project()
    build_container_service(fake_ctx)
    assert isinstance(fake_ctx.path_mapper, PathMapperService)
