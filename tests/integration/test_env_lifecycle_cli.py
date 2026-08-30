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
from admt.exceptions import ConfigError, PathNotMappedError
from admt.services.config import ProjectConfig
from admt.services.container import ContainerService, ContainerStatus
from admt.services.path_mapper import PathMapperService

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
    # A synthetic session key: CliRunner tests are tty-less under CI, and the
    # resolution ladder refuses key-less tty-less callers -- the suite holds
    # a pin like any headless session (a dev terminal's tty still outranks).
    env = {"HOME": str(tmp_path), "ADMT_SESSION_KEY": "itest-session"}
    env.update({k: str(v) for k, v in overrides.items()})
    return env


def _project_config(root: Path) -> ProjectConfig:
    return ProjectConfig(
        name="myproj",
        compose_file=root / "docker" / "docker-compose.yml",
        compose_file_mtime=0,
        env_file=None,
        env_file_mtime=0,
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
    # build_container_service now returns (container, path_mapper) per Q5.
    # env lifecycle commands don't use the path mapper, so a mock is fine.
    mapper = MagicMock(spec=PathMapperService)
    monkeypatch.setattr("admt.cli.build_container_service", lambda _ctx: (container, mapper))
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


# ----- idempotency (TEST_PLAN.md §Idempotency Tests) -----


def test_env_start_when_already_running_is_noop(registered, mock_container, tmp_path):
    """``admt env start`` on an already-running container is exit 0 with notice."""
    _, runner = registered
    # ``mock_container.start`` is the unit under spec -- the integration test
    # only checks that the CLI exits cleanly and delegates exactly once. The
    # "already running" notice is asserted in the unit-tier test for
    # ContainerService.start (test_start_already_running_short_circuits).
    result = runner.invoke(cli, ["env", "start"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    mock_container.start.assert_called_once()


def test_env_stop_when_already_stopped_is_noop(registered, mock_container, tmp_path):
    """``admt env stop`` on an already-stopped container is exit 0 with notice."""
    _, runner = registered
    result = runner.invoke(cli, ["env", "stop"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    mock_container.stop.assert_called_once()


def test_env_use_already_active_is_noop(tmp_path):
    """``admt env use <already-active>`` is exit 0; no config file rewrite."""
    root = _make_project(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ["env", "init", str(root)], env=_env_vars(tmp_path))
    config_path = tmp_path / ".admt" / "config.yml"
    mtime_before = config_path.stat().st_mtime_ns
    result = runner.invoke(cli, ["env", "use", "myproj"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Active project: myproj" in result.output
    # No save -> mtime unchanged.
    assert config_path.stat().st_mtime_ns == mtime_before


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
    mock_container.build_image.assert_called_once_with(no_cache=False)


def test_env_build_no_cache_flag(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["env", "build", "--no-cache"], env=_env_vars(tmp_path))
    assert result.exit_code == 0
    mock_container.build_image.assert_called_once_with(no_cache=True)


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


@pytest.fixture
def mock_container_mapped(monkeypatch):
    """Like ``mock_container`` but with a real PathMapperService, for ``env exec``."""
    container = MagicMock(spec=ContainerService)

    def fake_build(ctx):
        project = ctx.config_service.get_active_project()
        return container, PathMapperService(project.volume_mounts)

    monkeypatch.setattr("admt.cli.build_container_service", fake_build)
    return container


def test_env_exec_runs_in_the_directory_mapped_from_cwd(
    registered, mock_container_mapped, tmp_path, monkeypatch
):
    root, runner = registered
    monkeypatch.chdir(root / "docker")
    mock_container_mapped.exec.return_value = 0
    result = runner.invoke(
        cli, ["env", "exec", "redo what"], env=_env_vars(tmp_path, ADMT_NONINTERACTIVE="1")
    )
    assert result.exit_code == 0, result.output
    assert (
        mock_container_mapped.exec.call_args.args[0] == "cd /home/user/myproj/docker && redo what"
    )


def test_env_exec_from_an_unmapped_directory_exits_4(
    registered, mock_container_mapped, tmp_path, monkeypatch
):
    _, runner = registered
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli, ["env", "exec", "pwd"], env=_env_vars(tmp_path, ADMT_NONINTERACTIVE="1")
    )
    assert result.exit_code == PathNotMappedError.exit_code
    assert "not under any volume mount" in result.output
    mock_container_mapped.exec.assert_not_called()


def test_env_exec_forwards_exit_code(registered, mock_container_mapped, tmp_path, monkeypatch):
    root, runner = registered
    monkeypatch.chdir(root)
    sentinel_code = 2
    mock_container_mapped.exec.return_value = sentinel_code
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


def test_env_rm_yes_alone_does_not_proceed(registered, mock_container, tmp_path):
    """``--yes`` selects the default of a prompt; default for rm is No.

    The user must pass ``--force`` to skip the prompt for a destructive op.
    """
    _, runner = registered
    result = runner.invoke(cli, ["-y", "env", "rm"], env=_env_vars(tmp_path), input="\n")
    assert result.exit_code == 0
    assert "Aborted" in result.output
    mock_container.rm.assert_not_called()


def test_env_rm_proceeds_with_explicit_y(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["env", "rm"], env=_env_vars(tmp_path), input="y\n")
    assert result.exit_code == 0, result.output
    mock_container.rm.assert_called_once_with(
        remove_volumes=False, remove_image=False, remove_all=False
    )


def test_env_rm_with_force_skips_prompt(registered, mock_container, tmp_path):
    """``--force`` skips the prompt entirely; no stdin is read."""
    _, runner = registered
    result = runner.invoke(cli, ["-f", "env", "rm"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Aborted" not in result.output
    mock_container.rm.assert_called_once_with(
        remove_volumes=False, remove_image=False, remove_all=False
    )


def test_env_rm_with_force_and_remove_all(registered, mock_container, tmp_path):
    """``--force`` composes with the scope flags and still skips the prompt."""
    _, runner = registered
    result = runner.invoke(cli, ["-f", "env", "rm", "--remove-all"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    mock_container.rm.assert_called_once_with(
        remove_volumes=False, remove_image=False, remove_all=True
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


# ----- Phase 5 flag matrix (env-side) -----


def test_verbose_flag_reaches_env_commands(registered, mock_container, tmp_path):
    """``-v`` is accepted on env subcommands (propagates via Context to OutputService)."""
    _, runner = registered
    result = runner.invoke(cli, ["-v", "env", "status"], env=_env_vars(tmp_path))
    # The status command itself doesn't produce verbose output, but exit
    # should be 0 and the flag parsing should not reject ``-v``.
    assert result.exit_code == 0


def test_force_flag_accepted_but_noop_for_non_templates(registered, mock_container, tmp_path):
    """``--force`` on env start is accepted gracefully (no-op, just passes through)."""
    _, runner = registered
    result = runner.invoke(cli, ["-f", "env", "start"], env=_env_vars(tmp_path))
    assert result.exit_code == 0
    mock_container.start.assert_called_once()
