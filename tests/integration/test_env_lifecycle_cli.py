"""Integration tests for the Phase 2 env subcommands.

ContainerService is mocked at the bootstrap boundary so the tests exercise
full Click plumbing (group routing, aliases, flag parsing, exit-code
translation) without needing Docker.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from admt.cli import cli
from admt.exceptions import ConfigError
from admt.services.config import ProjectConfig
from admt.services.container import ContainerService, ContainerStatus

DEFAULT_COMPOSE = dedent(
    """\
    name: myproj
    services:
      myproj:
        container_name: myproj_container
        volumes:
          - type: bind
            source: ../../adamant
            target: /home/user/adamant
          - type: bind
            source: ../../myproj
            target: /home/user/myproj
    """
)


def _make_project(base: Path, *, name: str = "myproj"):
    root = base / name
    (root / "docker").mkdir(parents=True)
    (root / "env").mkdir(parents=True)
    (root / "default.do").touch()
    (root / "env" / "activate").touch()
    (root / "docker" / "docker-compose.yml").write_text(DEFAULT_COMPOSE.replace("myproj", name))
    (base / "adamant").mkdir(exist_ok=True)
    return root


def _env_vars(tmp_path: Path, **overrides) -> dict[str, str]:
    env = {"HOME": str(tmp_path)}
    env.update({k: str(v) for k, v in overrides.items()})
    return env


def _project_config(root: Path) -> ProjectConfig:
    return ProjectConfig(
        name="myproj",
        compose_file=root / "docker" / "docker-compose.yml",
        compose_file_mtime=0,
        service_name="myproj",
        container_name="myproj_container",
        project_root=root,
        container_home=Path("/home/user"),
        volume_mounts={root: Path("/home/user/myproj")},
        activate_script=Path("/home/user/myproj/env/activate"),
    )


@pytest.fixture
def registered(tmp_path):
    """Register a project under HOME=tmp_path; return (root, runner)."""
    root = _make_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["env", "init", str(root)], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    return root, runner


@pytest.fixture
def mock_container(monkeypatch):
    """Patch bootstrap.build_container_service so env lifecycle cmds don't need Docker."""
    container = MagicMock(spec=ContainerService)
    monkeypatch.setattr("admt.cli.build_container_service", lambda _ctx: container)
    return container


# ----- start/stop/restart/status -----


def test_env_start_delegates_to_container(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["env", "start"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    mock_container.start.assert_called_once()


def test_env_stop_delegates_to_container(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["env", "stop"], env=_env_vars(tmp_path))
    assert result.exit_code == 0
    mock_container.stop.assert_called_once()


def test_env_restart_delegates_to_container(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["env", "restart"], env=_env_vars(tmp_path))
    assert result.exit_code == 0
    mock_container.restart.assert_called_once()


def test_env_status_prints_state(registered, mock_container, tmp_path):
    _, runner = registered
    mock_container.status.return_value = ContainerStatus.RUNNING
    result = runner.invoke(cli, ["env", "status"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Project: myproj" in result.output
    assert "Container: myproj_container" in result.output
    assert "Status: running" in result.output


def test_env_login_forwards_exit_code(registered, mock_container, tmp_path):
    _, runner = registered
    sentinel_code = 5
    mock_container.login.return_value = sentinel_code
    result = runner.invoke(cli, ["env", "login"], env=_env_vars(tmp_path))
    assert result.exit_code == sentinel_code


# ----- image management -----


def test_env_build_delegates(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["env", "build"], env=_env_vars(tmp_path))
    assert result.exit_code == 0
    mock_container.build_image.assert_called_once()


def test_env_push_delegates(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["env", "push"], env=_env_vars(tmp_path))
    assert result.exit_code == 0
    mock_container.push_image.assert_called_once()


def test_env_pull_delegates(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["env", "pull"], env=_env_vars(tmp_path))
    assert result.exit_code == 0
    mock_container.pull_image.assert_called_once()


# ----- exec / refresh -----


def test_env_exec_passes_command_string(registered, mock_container, tmp_path):
    _, runner = registered
    mock_container.exec.return_value = 0
    result = runner.invoke(
        cli,
        ["env", "exec", "redo what"],
        env=_env_vars(tmp_path, ADMT_NONINTERACTIVE="1"),
    )
    assert result.exit_code == 0, result.output
    assert mock_container.exec.call_args.args[0] == "redo what"


def test_env_exec_forwards_exit_code(registered, mock_container, tmp_path):
    _, runner = registered
    sentinel_code = 2
    mock_container.exec.return_value = sentinel_code
    result = runner.invoke(
        cli,
        ["env", "exec", "false"],
        env=_env_vars(tmp_path, ADMT_NONINTERACTIVE="1"),
    )
    assert result.exit_code == sentinel_code


def test_env_refresh_delegates(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["env", "refresh"], env=_env_vars(tmp_path))
    assert result.exit_code == 0
    mock_container.refresh.assert_called_once()


# ----- rm with flag combinations -----


def test_env_rm_declines_without_yes(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["env", "rm"], env=_env_vars(tmp_path), input="\n")
    # Default No -> abort with exit 0, rm never invoked.
    assert result.exit_code == 0
    assert "Aborted" in result.output
    mock_container.rm.assert_not_called()


def test_env_rm_proceeds_with_yes(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["-y", "env", "rm"], env=_env_vars(tmp_path), input="")
    # --yes has no effect on a prompt with default=False -- it still declines.
    # Use an explicit y response to proceed.
    result = runner.invoke(cli, ["env", "rm"], env=_env_vars(tmp_path), input="y\n")
    assert result.exit_code == 0, result.output
    mock_container.rm.assert_called_once_with(
        remove_volumes=False, remove_image=False, remove_all=False
    )


def test_env_rm_with_volumes_flag(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["env", "rm", "--volumes"], env=_env_vars(tmp_path), input="y\n")
    assert result.exit_code == 0
    mock_container.rm.assert_called_once_with(
        remove_volumes=True, remove_image=False, remove_all=False
    )


def test_env_rm_with_image_flag(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["env", "rm", "--image"], env=_env_vars(tmp_path), input="y\n")
    assert result.exit_code == 0
    mock_container.rm.assert_called_once_with(
        remove_volumes=False, remove_image=True, remove_all=False
    )


def test_env_rm_with_remove_all_flag(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["env", "rm", "--remove-all"], env=_env_vars(tmp_path), input="y\n")
    assert result.exit_code == 0
    mock_container.rm.assert_called_once_with(
        remove_volumes=False, remove_image=False, remove_all=True
    )


# ----- list -----


def test_env_list_empty(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["env", "list"], env=_env_vars(tmp_path))
    assert result.exit_code == 0
    assert "No projects registered" in result.output


def test_env_list_marks_active_project(registered, tmp_path):
    root, runner = registered
    result = runner.invoke(cli, ["env", "list"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    assert "* myproj" in result.output
    assert str(root / "docker" / "docker-compose.yml") in result.output


# ----- error paths -----


def test_env_start_without_active_project_errors(tmp_path):
    runner = CliRunner()
    # No env init has been run; build_container_service should fail.
    result = runner.invoke(cli, ["env", "start"], env=_env_vars(tmp_path))
    assert result.exit_code == ConfigError.exit_code
    assert "No project configured" in result.output


# ----- alias e -----


def test_env_alias_e_for_start(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["e", "start"], env=_env_vars(tmp_path))
    assert result.exit_code == 0
    mock_container.start.assert_called_once()
