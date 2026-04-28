"""Tests for ContainerService.exec / exec_captured + recovery (DockerAdapter mocked).

Covers the optimistic-execute-then-recover pattern from Phase 5.1: skip
pre-flight probes on the happy path, diagnose the failure mode (container
down vs. snapshot wiped) on exec failure, and retry once.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from admt.adapters.docker import CommandResult, DockerAdapter
from admt.services.config import ProjectConfig
from admt.services.container import ContainerService
from admt.services.output import OutputService


def _project(name: str = "myproj") -> ProjectConfig:
    return ProjectConfig(
        name=name,
        compose_file=Path(f"/sim/{name}/docker/docker-compose.yml"),
        compose_file_mtime=0,
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
    """Make ``is_running`` report True so ``exec`` doesn't prompt."""
    docker.docker_inspect_state.return_value = CommandResult(returncode=0, stdout="running\n")


def _prime_not_found(docker: MagicMock) -> None:
    """Make ``is_running`` report False as if the container does not exist."""
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


# ----- exec basics -----


def test_exec_ensures_snapshot_then_runs_via_proxy(svc, docker):
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.return_value = CommandResult(returncode=0)
    result = svc.exec("echo hi")
    assert result == 0
    call = docker.docker_exec.call_args
    args = call.args[0]
    assert args[0] == "/tmp/admt/myproj/exec.sh"  # noqa: S108 -- container path
    assert args[1:] == ["bash", "-c", "echo hi"]
    assert call.kwargs == {
        "interactive": False,
        "merge_stderr": False,
        "capture_output": False,
        "line_transform": None,
    }


def test_exec_propagates_interactive_flag(svc, docker):
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.return_value = CommandResult(returncode=0)
    svc.exec("bash", interactive=True)
    assert docker.docker_exec.call_args.kwargs["interactive"] is True


def test_exec_threads_merge_stderr(svc, docker):
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.return_value = CommandResult(returncode=0)
    svc.exec("redo all", merge_stderr=True)
    assert docker.docker_exec.call_args.kwargs["merge_stderr"] is True


def test_exec_forwards_line_transform_to_adapter(svc, docker):
    """ContainerPassthroughCommand can thread a line_transform through."""
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.return_value = CommandResult(returncode=0)

    def transform(line):
        return line

    svc.exec("redo all", line_transform=transform)
    assert docker.docker_exec.call_args.kwargs["line_transform"] is transform


# ----- exec output capture / failure diagnostics -----


def test_exec_captures_output_and_emits_on_failure(docker, capsys):
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=True, noninteractive=True),
    )
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.return_value = CommandResult(
        returncode=2, stdout="captured stdout\n", stderr="captured stderr\n"
    )
    failing_exit = 2
    exit_code = svc.exec("redo all", merge_stderr=True, capture_output=True)
    assert exit_code == failing_exit
    captured = capsys.readouterr()
    assert "captured stdout" in captured.out
    assert "captured stderr" in captured.err


def test_exec_captured_failure_with_stdout_only(docker, capsys):
    """Empty stderr branch: emit_captured skips stderr, failed-cmd diagnostic fires."""
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=True, noninteractive=True),
    )
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.return_value = CommandResult(returncode=1, stdout="only stdout\n", stderr="")
    svc.exec("redo all", capture_output=True)
    captured = capsys.readouterr()
    assert "only stdout\n" in captured.out
    assert "Failed (exit 1)" in captured.err


def test_exec_failed_command_diagnostic_non_verbose(docker, capsys):
    """Without --verbose, a failed exec still shows the command that ran."""
    failing_exit = 5
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=False, yes=True, noninteractive=True),
    )
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.return_value = CommandResult(returncode=failing_exit)
    exit_code = svc.exec("redo all")
    assert exit_code == failing_exit
    captured = capsys.readouterr()
    assert f"Failed (exit {failing_exit})" in captured.err
    assert "redo all" in captured.err


def test_exec_successful_command_no_diagnostic(docker, capsys):
    """Success path: no "Failed" line."""
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=False, yes=True, noninteractive=True),
    )
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.return_value = CommandResult(returncode=0)
    svc.exec("redo all")
    captured = capsys.readouterr()
    assert "Failed" not in captured.err


def test_exec_verbose_failure_does_not_double_echo(docker, capsys):
    """Verbose already echoed pre-exec; don't duplicate on failure."""
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=True, quiet=False, yes=True, noninteractive=True),
    )
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.return_value = CommandResult(returncode=5)
    svc.exec("redo all")
    captured = capsys.readouterr()
    assert "Failed (exit 5)" not in captured.err


def test_exec_captured_failure_with_stderr_only(docker, capsys):
    """Empty stdout branch: emit_captured skips stdout; diagnostic still fires."""
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=True, noninteractive=True),
    )
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.return_value = CommandResult(returncode=1, stdout="", stderr="only stderr\n")
    svc.exec("redo all", capture_output=True)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "only stderr\n" in captured.err
    assert "Failed (exit 1)" in captured.err


def test_exec_captures_output_suppresses_on_success(docker, capsys):
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=True, noninteractive=True),
    )
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.return_value = CommandResult(returncode=0, stdout="quiet success output\n")
    exit_code = svc.exec("redo all", merge_stderr=True, capture_output=True)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# ----- exec_captured -----


def test_exec_captured_returns_full_command_result(docker):
    """exec_captured returns the raw CommandResult so callers can post-process."""
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=False, yes=True, noninteractive=True),
    )
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.return_value = CommandResult(returncode=0, stdout="payload\n")
    result = svc.exec_captured("redo what")
    assert result.returncode == 0
    assert result.stdout == "payload\n"
    assert docker.docker_exec.call_args.kwargs["capture_output"] is True
    assert docker.docker_exec.call_args.kwargs["merge_stderr"] is True


def test_exec_captured_failure_prints_diagnostic_when_non_verbose(docker, capsys):
    failing_exit = 2
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=False, yes=True, noninteractive=True),
    )
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.return_value = CommandResult(returncode=failing_exit, stdout="")
    result = svc.exec_captured("redo what")
    assert result.returncode == failing_exit
    assert f"Failed (exit {failing_exit})" in capsys.readouterr().err


def test_exec_captured_recovers_via_infrastructure_check(docker):
    """First exec_captured fails; infra recovery succeeds; retried call succeeds."""
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=False, yes=True, noninteractive=True),
    )
    docker.docker_inspect_state.return_value = CommandResult(returncode=0, stdout="running\n")
    docker.docker_exec_with_stdin.return_value = _ok()
    docker.docker_exec.side_effect = [
        CommandResult(returncode=1),
        CommandResult(returncode=0),
        CommandResult(returncode=0, stdout="payload\n"),
    ]
    docker.docker_exec_captured.side_effect = [
        _fail(code=1),
        CommandResult(returncode=0, stdout="PATH=/usr/bin\n"),
        CommandResult(returncode=0, stdout="PATH=/opt/admt:/usr/bin\n"),
        _ok(),
    ]
    result = svc.exec_captured("redo what")
    assert result.returncode == 0
    assert result.stdout == "payload\n"


# ----- exec: optimistic happy-path + recovery (Phase 5.1) -----


def test_exec_happy_path_skips_preflight_probes(svc, docker):
    """Success path: one docker_exec, no inspect-state probe, no snapshot check."""
    docker.docker_exec.return_value = CommandResult(returncode=0)
    result = svc.exec("echo hi")
    assert result == 0
    docker.docker_exec.assert_called_once()
    docker.docker_inspect_state.assert_not_called()
    docker.docker_exec_captured.assert_not_called()


def test_exec_recovers_from_stopped_container(docker):
    """Exec fails, is_running=False -> ensure_running auto-starts, retry succeeds."""
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=True, noninteractive=False),
    )
    _prime_not_found(docker)
    docker.image_name.return_value = None
    docker.compose_up.return_value = _ok()
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.side_effect = [CommandResult(returncode=1), CommandResult(returncode=0)]
    assert svc.exec("echo hi") == 0
    expected_attempts = 2
    assert docker.docker_exec.call_count == expected_attempts
    docker.compose_up.assert_called_once()


def test_exec_recovers_from_wiped_snapshot(svc, docker):
    """Container up but /tmp was wiped: regenerate snapshot and retry."""
    _prime_running(docker)

    def capture_side_effect(cmd, **_):
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)
        if cmd == ["env"]:
            return CommandResult(returncode=0, stdout="PATH=/usr/bin\n")
        if cmd[:1] == ["cat"]:
            return CommandResult(returncode=0, stdout="PATH=/opt/active\n")
        return _ok()

    docker.docker_exec_captured.side_effect = capture_side_effect
    docker.docker_exec_with_stdin.return_value = _ok()
    docker.docker_exec.side_effect = [
        CommandResult(returncode=127),
        CommandResult(returncode=0),
        CommandResult(returncode=0),
    ]
    assert svc.exec("echo hi") == 0
    expected_attempts = 3  # first proxy attempt, activate stream, retry attempt
    assert docker.docker_exec.call_count == expected_attempts
    expected_writes = 2  # snapshot + proxy
    assert docker.docker_exec_with_stdin.call_count == expected_writes


def test_exec_propagates_real_command_failure_without_retry(svc, docker):
    """Container up, snapshot present: the user's command failed. No retry."""
    failing_exit = 3
    _prime_running(docker)
    docker.docker_exec_captured.return_value = _ok()  # snapshot present
    docker.docker_exec.return_value = CommandResult(returncode=failing_exit)
    assert svc.exec("redo all") == failing_exit
    docker.docker_exec.assert_called_once()


def test_exec_verbose_echoes_command_on_retry(docker, capsys):
    """Verbose mode echoes the docker exec line for each attempt, not just the first."""
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=True, quiet=False, yes=True, noninteractive=False),
    )
    _prime_not_found(docker)
    docker.image_name.return_value = None
    docker.compose_up.return_value = _ok()
    docker.docker_exec_captured.return_value = _ok()
    docker.docker_exec.side_effect = [CommandResult(returncode=1), CommandResult(returncode=0)]
    svc.exec("echo hi")
    captured = capsys.readouterr()
    assert captured.out.count("docker exec -u user") >= 2  # noqa: PLR2004 -- at least two echoes
