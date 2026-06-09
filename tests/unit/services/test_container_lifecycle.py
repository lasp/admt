"""Tests for ContainerService lifecycle ops -- start/stop/restart/login/status/image/rm.

DockerAdapter is mocked. Env-snapshot machinery is exercised in
``test_env_snapshot.py``; exec + recovery in ``test_container_exec.py``.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from admt.adapters.docker import CommandResult, DockerAdapter
from admt.exceptions import ContainerError
from admt.services.config import ProjectConfig
from admt.services.container import ContainerService, ContainerStatus
from admt.services.output import OutputService


def _project(name: str = "myproj") -> ProjectConfig:
    return ProjectConfig(
        name=name,
        compose_file=Path(f"/sim/{name}/docker/docker-compose.yml"),
        compose_file_mtime=0,
        env_file=None,
        env_file_mtime=0,
        service_name=name,
        container_name=f"{name}_container",
        project_root=Path(f"/sim/{name}"),
        container_home=Path("/home/user"),
        volume_mounts={Path(f"/sim/{name}"): Path(f"/home/user/{name}")},
        activate_script=Path(f"/home/user/{name}/env/activate"),
    )


def _ok() -> CommandResult:
    return CommandResult(returncode=0)


def _fail(code: int = 1, stderr: str = "boom") -> CommandResult:
    return CommandResult(returncode=code, stderr=stderr)


def _prime_running(docker: MagicMock) -> None:
    docker.docker_inspect_state.return_value = CommandResult(returncode=0, stdout="running\n")


def _prime_not_found(docker: MagicMock) -> None:
    docker.docker_inspect_state.return_value = CommandResult(
        returncode=1,
        stderr="Error: No such object: myproj_container\n",
    )


@pytest.fixture
def docker():
    return MagicMock(spec=DockerAdapter)


@pytest.fixture
def output():
    return OutputService(verbose=False, quiet=True, yes=False, noninteractive=False)


@pytest.fixture
def svc(docker, output):
    return ContainerService(docker=docker, project=_project(), output=output)


# ----- status / is_running -----


def test_status_running_when_inspect_reports_running(svc, docker):
    docker.docker_inspect_state.return_value = CommandResult(returncode=0, stdout="running\n")
    assert svc.status() is ContainerStatus.RUNNING
    assert svc.is_running() is True


def test_status_stopped_for_non_running_states(svc, docker):
    for state in ("exited", "paused", "restarting", "created", "dead"):
        docker.docker_inspect_state.return_value = CommandResult(returncode=0, stdout=f"{state}\n")
        assert svc.status() is ContainerStatus.STOPPED, state


def test_status_not_found_when_inspect_says_no_such_object(svc, docker):
    docker.docker_inspect_state.return_value = CommandResult(
        returncode=1,
        stderr="Error: No such object: myproj_container\n",
    )
    assert svc.status() is ContainerStatus.NOT_FOUND


def test_status_unknown_when_inspect_fails_for_other_reason(svc, docker):
    docker.docker_inspect_state.return_value = CommandResult(
        returncode=1,
        stderr="Cannot connect to the Docker daemon\n",
    )
    assert svc.status() is ContainerStatus.UNKNOWN


def test_status_not_found_when_stdout_empty(svc, docker):
    docker.docker_inspect_state.return_value = CommandResult(returncode=0, stdout="")
    assert svc.status() is ContainerStatus.NOT_FOUND


# ----- start / stop / restart -----


def test_start_pulls_when_image_missing_and_starts(svc, docker):
    _prime_not_found(docker)
    docker.image_name.return_value = "img:tag"
    docker.image_exists_locally.return_value = False
    docker.compose_pull.return_value = _ok()
    docker.compose_up.return_value = _ok()
    docker.docker_exec_captured.return_value = _ok()
    svc.start()
    docker.compose_pull.assert_called_once()
    docker.compose_up.assert_called_once()


def test_start_skips_pull_when_image_present(svc, docker):
    _prime_not_found(docker)
    docker.image_name.return_value = "img:tag"
    docker.image_exists_locally.return_value = True
    docker.compose_up.return_value = _ok()
    docker.docker_exec_captured.return_value = _ok()
    svc.start()
    docker.compose_pull.assert_not_called()


def test_start_skips_pull_when_service_has_no_image(svc, docker):
    _prime_not_found(docker)
    docker.image_name.return_value = None
    docker.compose_up.return_value = _ok()
    docker.docker_exec_captured.return_value = _ok()
    svc.start()
    docker.compose_pull.assert_not_called()


def test_start_raises_on_pull_failure(svc, docker):
    _prime_not_found(docker)
    docker.image_name.return_value = "img:tag"
    docker.image_exists_locally.return_value = False
    docker.compose_pull.return_value = _fail()
    with pytest.raises(ContainerError, match="Build the image locally"):
        svc.start()


def test_start_raises_on_up_failure(svc, docker):
    _prime_not_found(docker)
    docker.image_name.return_value = None
    docker.compose_up.return_value = _fail()
    with pytest.raises(ContainerError, match="up failed"):
        svc.start()


def test_start_already_running_short_circuits(docker, capsys):
    """Idempotent: ``start()`` on a running container emits a notice and returns."""
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=False, yes=False, noninteractive=False),
    )
    _prime_running(docker)
    svc.start()
    docker.compose_up.assert_not_called()
    docker.compose_pull.assert_not_called()
    captured = capsys.readouterr()
    assert "already running" in captured.out


def test_stop_delegates_and_raises_on_failure(svc, docker):
    _prime_running(docker)
    docker.compose_stop.return_value = _fail()
    with pytest.raises(ContainerError, match="stop failed"):
        svc.stop()


def test_stop_success(svc, docker):
    _prime_running(docker)
    docker.compose_stop.return_value = _ok()
    svc.stop()
    docker.compose_stop.assert_called_once()


def test_stop_already_stopped_short_circuits(docker, capsys):
    """Idempotent: ``stop()`` on a stopped/missing container emits a notice and returns."""
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=False, yes=False, noninteractive=False),
    )
    _prime_not_found(docker)
    svc.stop()
    docker.compose_stop.assert_not_called()
    captured = capsys.readouterr()
    assert "already stopped" in captured.out


def test_restart_calls_stop_then_start(docker):
    """Restart: stop sees running, then start sees not-running and proceeds."""
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=False, noninteractive=False),
    )
    docker.docker_inspect_state.side_effect = [
        CommandResult(returncode=0, stdout="running\n"),
        CommandResult(returncode=1, stderr="Error: No such object: myproj_container\n"),
    ]
    docker.compose_stop.return_value = _ok()
    docker.image_name.return_value = None
    docker.compose_up.return_value = _ok()
    docker.docker_exec_captured.return_value = _ok()
    svc.restart()
    docker.compose_stop.assert_called_once()
    docker.compose_up.assert_called_once()


def test_restart_aborts_on_stop_failure(svc, docker):
    _prime_running(docker)
    docker.compose_stop.return_value = _fail()
    with pytest.raises(ContainerError):
        svc.restart()
    docker.compose_up.assert_not_called()


# ----- image management / rm -----


def test_build_image(svc, docker):
    docker.compose_build.return_value = _ok()
    svc.build_image()
    docker.compose_build.assert_called_once()


def test_build_image_raises_on_failure(svc, docker):
    docker.compose_build.return_value = _fail()
    with pytest.raises(ContainerError, match="build failed"):
        svc.build_image()


def test_push_pull_delegate_and_raise(svc, docker):
    docker.compose_push.return_value = _ok()
    svc.push_image()
    docker.compose_pull.return_value = _fail()
    with pytest.raises(ContainerError, match="pull failed"):
        svc.pull_image()


def test_rm_plain(svc, docker):
    docker.compose_down.return_value = _ok()
    svc.rm()
    docker.compose_down.assert_called_once_with(remove_volumes=False)
    docker.remove_image.assert_not_called()


def test_rm_with_volumes(svc, docker):
    docker.compose_down.return_value = _ok()
    svc.rm(remove_volumes=True)
    docker.compose_down.assert_called_once_with(remove_volumes=True)


def test_rm_with_image(svc, docker):
    docker.compose_down.return_value = _ok()
    docker.remove_image.return_value = _ok()
    svc.rm(remove_image=True)
    docker.remove_image.assert_called_once()


def test_rm_remove_all(svc, docker):
    docker.compose_down.return_value = _ok()
    docker.remove_image.return_value = _ok()
    svc.rm(remove_all=True)
    docker.compose_down.assert_called_once_with(remove_volumes=True)
    docker.remove_image.assert_called_once()


def test_rm_warns_on_image_removal_failure(svc, docker, capsys):
    svc._output = OutputService(verbose=False, quiet=False, yes=False, noninteractive=False)
    docker.compose_down.return_value = _ok()
    docker.remove_image.return_value = _fail(stderr="image in use")
    svc.rm(remove_image=True)
    captured = capsys.readouterr()
    assert "Failed to remove image" in captured.err


def test_rm_raises_on_down_failure(svc, docker):
    docker.compose_down.return_value = _fail()
    with pytest.raises(ContainerError, match="down failed"):
        svc.rm()


# ----- login -----


def test_login_runs_bare_bash_interactive(svc, docker):
    sentinel = 42
    docker.docker_exec.return_value = CommandResult(returncode=sentinel)
    assert svc.login() == sentinel
    docker.docker_exec.assert_called_once_with(["/bin/bash"], interactive=True)


# ----- refresh (delegates to EnvSnapshotService.refresh) -----


def test_refresh_delegates_to_snapshot_service(svc, docker):
    """ContainerService.refresh forwards to EnvSnapshotService.refresh.

    End-to-end snapshot regeneration is covered in test_env_snapshot.py;
    here we just pin the delegation contract.
    """

    # Configure the docker mock so the snapshot service's underlying
    # rm + test + capture + write sequence succeeds.
    def capture_side_effect(cmd, **_):
        if cmd[:2] == ["rm", "-rf"]:
            return _ok()
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)  # snapshot missing -> regenerate
        if cmd == ["env"]:
            return CommandResult(returncode=0, stdout="PATH=/usr/bin\n")
        if cmd[:1] == ["cat"]:
            return CommandResult(returncode=0, stdout="PATH=/opt/activated\n")
        return _ok()

    docker.docker_exec_captured.side_effect = capture_side_effect
    docker.docker_exec.return_value = _ok()
    docker.docker_exec_with_stdin.return_value = _ok()
    svc.refresh()
    # rm -rf was the first captured call; snapshot+proxy got written.
    expected_writes = 2
    assert docker.docker_exec_with_stdin.call_count == expected_writes


# ----- ensure_running -----


def test_ensure_running_noop_when_already_running(svc, docker):
    _prime_running(docker)
    svc.ensure_running()
    docker.compose_up.assert_not_called()


def test_ensure_running_autostarts_when_yes(docker):
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=True, noninteractive=False),
    )
    _prime_not_found(docker)
    docker.image_name.return_value = None
    docker.compose_up.return_value = _ok()
    docker.docker_exec_captured.return_value = _ok()
    svc.ensure_running()
    docker.compose_up.assert_called_once()


def test_ensure_running_errors_in_noninteractive_mode(docker):
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=False, noninteractive=True),
    )
    _prime_not_found(docker)
    with pytest.raises(ContainerError, match="admt env start"):
        svc.ensure_running()


def test_ensure_running_raises_when_user_declines(docker, monkeypatch):
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=False, noninteractive=False),
    )
    _prime_not_found(docker)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    with pytest.raises(ContainerError, match="Declined"):
        svc.ensure_running()


def test_ensure_running_starts_when_user_accepts_prompt(docker, monkeypatch):
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=False, noninteractive=False),
    )
    _prime_not_found(docker)
    docker.image_name.return_value = None
    docker.compose_up.return_value = _ok()
    docker.docker_exec_captured.return_value = _ok()
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    svc.ensure_running()
    docker.compose_up.assert_called_once()


# ----- verbose echoes -----


def test_verbose_echoes_compose_up_on_start(docker, capsys):
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=True, quiet=False, yes=False, noninteractive=False),
    )
    _prime_not_found(docker)
    docker.image_name.return_value = None
    docker.compose_up.return_value = _ok()
    docker.docker_exec_captured.return_value = _ok()
    svc.start()
    captured = capsys.readouterr()
    assert "docker compose -f" in captured.out
    assert "up -d" in captured.out


def test_verbose_echoes_compose_stop_on_stop(docker, capsys):
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=True, quiet=False, yes=False, noninteractive=False),
    )
    _prime_running(docker)
    docker.compose_stop.return_value = _ok()
    svc.stop()
    captured = capsys.readouterr()
    assert "docker compose -f" in captured.out
    assert "stop" in captured.out


def test_verbose_echoes_down_v_when_removing_volumes(docker, capsys):
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=True, quiet=False, yes=True, noninteractive=False),
    )
    docker.compose_down.return_value = _ok()
    svc.rm(remove_volumes=True)
    captured = capsys.readouterr()
    assert "down -v" in captured.out
