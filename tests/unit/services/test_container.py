"""Tests for ContainerService -- orchestration and env snapshot proxy (DockerAdapter mocked)."""

from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock

import pytest

from admt.adapters.docker import CommandResult, DockerAdapter
from admt.exceptions import ContainerError
from admt.services.config import ProjectConfig
from admt.services.container import ContainerService, ContainerStatus
from admt.services.output import OutputService


def _project(name="myproj") -> ProjectConfig:
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


@pytest.fixture
def docker():
    return MagicMock(spec=DockerAdapter)


@pytest.fixture
def output():
    return OutputService(verbose=False, quiet=True, yes=False, noninteractive=False)


@pytest.fixture
def svc(docker, output):
    return ContainerService(docker=docker, project=_project(), output=output)


def _ok():
    return CommandResult(returncode=0)


def _fail(code=1, stderr="boom"):
    return CommandResult(returncode=code, stderr=stderr)


# ----- status / is_running -----


def test_status_running_when_state_is_running(svc, docker):
    docker.compose_ps.return_value = CommandResult(
        returncode=0,
        stdout='{"Service": "myproj", "State": "running"}\n',
    )
    assert svc.status() is ContainerStatus.RUNNING
    assert svc.is_running() is True


def test_status_stopped_when_state_is_not_running(svc, docker):
    docker.compose_ps.return_value = CommandResult(
        returncode=0,
        stdout='{"Service": "myproj", "State": "exited"}\n',
    )
    assert svc.status() is ContainerStatus.STOPPED


def test_status_not_found_when_no_entries(svc, docker):
    docker.compose_ps.return_value = CommandResult(returncode=0, stdout="")
    assert svc.status() is ContainerStatus.NOT_FOUND


def test_status_unknown_when_ps_fails(svc, docker):
    docker.compose_ps.return_value = CommandResult(returncode=1, stderr="docker not running")
    assert svc.status() is ContainerStatus.UNKNOWN


def test_status_parses_json_array_form(svc, docker):
    docker.compose_ps.return_value = CommandResult(
        returncode=0,
        stdout='[{"Service": "myproj", "State": "running"}]',
    )
    assert svc.status() is ContainerStatus.RUNNING


def test_status_ignores_garbled_lines(svc, docker):
    docker.compose_ps.return_value = CommandResult(
        returncode=0,
        stdout='not-json\n{"Service": "myproj", "State": "running"}\n',
    )
    assert svc.status() is ContainerStatus.RUNNING


def test_status_handles_invalid_array_form(svc, docker):
    docker.compose_ps.return_value = CommandResult(
        returncode=0,
        stdout="[not a valid json array",
    )
    assert svc.status() is ContainerStatus.NOT_FOUND


def test_status_skips_non_mapping_entries_in_array(svc, docker):
    docker.compose_ps.return_value = CommandResult(
        returncode=0,
        # First entry is a string (should be skipped); second is the real service.
        stdout='["not-a-mapping", {"Service": "myproj", "State": "running"}]',
    )
    assert svc.status() is ContainerStatus.RUNNING


def test_status_skips_non_mapping_entries_in_ndjson(svc, docker):
    docker.compose_ps.return_value = CommandResult(
        returncode=0,
        stdout='"just-a-string"\n{"Service": "myproj", "State": "running"}\n',
    )
    assert svc.status() is ContainerStatus.RUNNING


def test_status_skips_blank_lines_in_ndjson(svc, docker):
    # Leading-trailing strip removes outer blanks, but blanks *between*
    # entries remain and exercise the ``continue`` branch.
    stdout = (
        '{"Service": "other", "State": "exited"}\n\n{"Service": "myproj", "State": "running"}\n'
    )
    docker.compose_ps.return_value = CommandResult(returncode=0, stdout=stdout)
    assert svc.status() is ContainerStatus.RUNNING


# ----- start / stop / restart -----


def test_start_pulls_when_image_missing_and_starts(svc, docker):
    docker.image_name.return_value = "img:tag"
    docker.image_exists_locally.return_value = False
    docker.compose_pull.return_value = _ok()
    docker.compose_up.return_value = _ok()
    # ensure_env_snapshot: assume proxy already exists so we don't touch the rest.
    docker.compose_exec_captured.return_value = _ok()
    svc.start()
    docker.compose_pull.assert_called_once()
    docker.compose_up.assert_called_once()


def test_start_skips_pull_when_image_present(svc, docker):
    docker.image_name.return_value = "img:tag"
    docker.image_exists_locally.return_value = True
    docker.compose_up.return_value = _ok()
    docker.compose_exec_captured.return_value = _ok()
    svc.start()
    docker.compose_pull.assert_not_called()


def test_start_skips_pull_when_service_has_no_image(svc, docker):
    docker.image_name.return_value = None
    docker.compose_up.return_value = _ok()
    docker.compose_exec_captured.return_value = _ok()
    svc.start()
    docker.compose_pull.assert_not_called()


def test_start_raises_on_pull_failure(svc, docker):
    docker.image_name.return_value = "img:tag"
    docker.image_exists_locally.return_value = False
    docker.compose_pull.return_value = _fail()
    with pytest.raises(ContainerError, match="Build the image locally"):
        svc.start()


def test_start_raises_on_up_failure(svc, docker):
    docker.image_name.return_value = None
    docker.compose_up.return_value = _fail()
    with pytest.raises(ContainerError, match="up failed"):
        svc.start()


def test_stop_delegates_and_raises_on_failure(svc, docker):
    docker.compose_stop.return_value = _fail()
    with pytest.raises(ContainerError, match="stop failed"):
        svc.stop()


def test_stop_success(svc, docker):
    docker.compose_stop.return_value = _ok()
    svc.stop()
    docker.compose_stop.assert_called_once()


def test_restart_calls_stop_then_start(svc, docker):
    docker.compose_stop.return_value = _ok()
    docker.image_name.return_value = None
    docker.compose_up.return_value = _ok()
    docker.compose_exec_captured.return_value = _ok()
    svc.restart()
    docker.compose_stop.assert_called_once()
    docker.compose_up.assert_called_once()


def test_restart_aborts_on_stop_failure(svc, docker):
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
    # Use a real OutputService (not quiet) so warnings hit stderr.
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
    docker.compose_exec.return_value = CommandResult(returncode=sentinel)
    assert svc.login() == sentinel
    docker.compose_exec.assert_called_once_with(["/bin/bash"], interactive=True)


# ----- exec + snapshot proxy -----


def _prime_running(docker):
    """Make ``is_running`` report True so ``exec`` doesn't prompt."""
    docker.compose_ps.return_value = CommandResult(
        returncode=0,
        stdout='{"Service": "myproj", "State": "running"}\n',
    )


def test_exec_ensures_snapshot_then_runs_via_proxy(svc, docker):
    _prime_running(docker)
    # Snapshot already exists -> ensure_env_snapshot returns quickly.
    docker.compose_exec_captured.return_value = _ok()
    docker.compose_exec.return_value = CommandResult(returncode=0)
    result = svc.exec("echo hi")
    assert result == 0
    call = docker.compose_exec.call_args
    args = call.args[0]
    # Container-side path inside the sandboxed /tmp/admt/<project>/ directory.
    assert args[0] == "/tmp/admt/myproj/exec.sh"  # noqa: S108 -- container path
    assert args[1:] == ["bash", "-c", "echo hi"]
    assert call.kwargs == {
        "interactive": False,
        "merge_stderr": False,
        "capture_output": False,
    }


def test_exec_propagates_interactive_flag(svc, docker):
    _prime_running(docker)
    docker.compose_exec_captured.return_value = _ok()
    docker.compose_exec.return_value = CommandResult(returncode=0)
    svc.exec("bash", interactive=True)
    assert docker.compose_exec.call_args.kwargs["interactive"] is True


def test_exec_threads_merge_stderr(svc, docker):
    _prime_running(docker)
    docker.compose_exec_captured.return_value = _ok()
    docker.compose_exec.return_value = CommandResult(returncode=0)
    svc.exec("redo all", merge_stderr=True)
    assert docker.compose_exec.call_args.kwargs["merge_stderr"] is True


def test_exec_captures_output_and_emits_on_failure(docker, capsys):

    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=True, noninteractive=True),
    )
    _prime_running(docker)
    docker.compose_exec_captured.return_value = _ok()
    docker.compose_exec.return_value = CommandResult(
        returncode=2, stdout="captured stdout\n", stderr="captured stderr\n"
    )
    failing_exit = 2
    exit_code = svc.exec("redo all", merge_stderr=True, capture_output=True)
    assert exit_code == failing_exit
    captured = capsys.readouterr()
    # emit_captured bypasses --quiet so the user sees why the command failed.
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
    docker.compose_exec_captured.return_value = _ok()
    docker.compose_exec.return_value = CommandResult(
        returncode=1, stdout="only stdout\n", stderr=""
    )
    svc.exec("redo all", capture_output=True)
    captured = capsys.readouterr()
    # Captured stdout is rendered verbatim on failure.
    assert "only stdout\n" in captured.out
    # The Phase 5 "failed command" diagnostic lands on stderr; no captured
    # stderr was provided, so that's the only thing there.
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
    docker.compose_exec_captured.return_value = _ok()
    docker.compose_exec.return_value = CommandResult(returncode=failing_exit)
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
    docker.compose_exec_captured.return_value = _ok()
    docker.compose_exec.return_value = CommandResult(returncode=0)
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
    docker.compose_exec_captured.return_value = _ok()
    docker.compose_exec.return_value = CommandResult(returncode=5)
    svc.exec("redo all")
    captured = capsys.readouterr()
    # Verbose path already printed via command_echo; the Phase 5 diagnostic
    # on stderr is suppressed to avoid duplication.
    assert "Failed (exit 5)" not in captured.err


def test_exec_captured_failure_with_stderr_only(docker, capsys):
    """Empty stdout branch: emit_captured skips stdout; diagnostic still fires."""
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=True, noninteractive=True),
    )
    _prime_running(docker)
    docker.compose_exec_captured.return_value = _ok()
    docker.compose_exec.return_value = CommandResult(
        returncode=1, stdout="", stderr="only stderr\n"
    )
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
    docker.compose_exec_captured.return_value = _ok()
    docker.compose_exec.return_value = CommandResult(returncode=0, stdout="quiet success output\n")
    exit_code = svc.exec("redo all", merge_stderr=True, capture_output=True)
    assert exit_code == 0
    captured = capsys.readouterr()
    # Success + quiet = silent.
    assert captured.out == ""
    assert captured.err == ""


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
    docker.compose_ps.return_value = CommandResult(returncode=0, stdout="")  # not found
    docker.image_name.return_value = None
    docker.compose_up.return_value = _ok()
    docker.compose_exec_captured.return_value = _ok()
    svc.ensure_running()
    docker.compose_up.assert_called_once()


def test_ensure_running_errors_in_noninteractive_mode(docker):

    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=False, noninteractive=True),
    )
    docker.compose_ps.return_value = CommandResult(returncode=0, stdout="")
    with pytest.raises(ContainerError, match="admt env start"):
        svc.ensure_running()


def test_ensure_running_raises_when_user_declines(docker, monkeypatch):

    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=False, noninteractive=False),
    )
    docker.compose_ps.return_value = CommandResult(returncode=0, stdout="")
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    with pytest.raises(ContainerError, match="Declined"):
        svc.ensure_running()


def test_verbose_echoes_compose_up_on_start(docker, capsys):
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=True, quiet=False, yes=False, noninteractive=False),
    )
    docker.image_name.return_value = None
    docker.compose_up.return_value = _ok()
    docker.compose_exec_captured.return_value = _ok()
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


def test_ensure_running_starts_when_user_accepts_prompt(docker, monkeypatch):

    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=False, noninteractive=False),
    )
    docker.compose_ps.return_value = CommandResult(returncode=0, stdout="")
    docker.image_name.return_value = None
    docker.compose_up.return_value = _ok()
    docker.compose_exec_captured.return_value = _ok()
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    svc.ensure_running()
    docker.compose_up.assert_called_once()


def test_ensure_env_snapshot_no_op_when_proxy_exists(svc, docker):
    docker.compose_exec_captured.return_value = _ok()
    svc.ensure_env_snapshot()
    # Only the test -f check was invoked.
    assert docker.compose_exec_captured.call_count == 1
    docker.compose_exec_with_stdin.assert_not_called()


def test_ensure_env_snapshot_generates_when_missing(svc, docker):
    # Sequence of captured responses:
    # 1. test -f exec.sh            -> missing (rc=1)
    # 2. env                        -> baseline
    # 3. bash -c source...          -> activated
    # 4. chmod +x                   -> ok
    def capture_side_effect(cmd, **_):
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)
        if cmd == ["env"]:
            return CommandResult(
                returncode=0,
                stdout="PATH=/usr/bin\nHOME=/home/user\n",
            )
        if cmd[0] == "bash":
            return CommandResult(
                returncode=0,
                stdout=(
                    "PATH=/opt/adamant:/usr/bin\nHOME=/home/user\nADAMANT_DIR=/home/user/adamant\n"
                ),
            )
        if cmd[:2] == ["chmod", "+x"]:
            return _ok()
        return _ok()

    docker.compose_exec_captured.side_effect = capture_side_effect
    docker.compose_exec_with_stdin.return_value = _ok()
    svc.ensure_env_snapshot()
    expected_writes = 2  # one snapshot + one proxy
    assert docker.compose_exec_with_stdin.call_count == expected_writes
    # First write is the snapshot, second the proxy. Inspect the second.
    snapshot_call, proxy_call = docker.compose_exec_with_stdin.call_args_list
    snapshot_content = snapshot_call.args[1]
    assert "ADAMANT_DIR" in snapshot_content
    # PATH value changed between baseline and activated -> must appear in snapshot.
    assert 'export PATH="/opt/adamant:/usr/bin"' in snapshot_content
    # HOME is identical in baseline and activated -> must NOT appear.
    assert "HOME=" not in snapshot_content
    proxy_content = proxy_call.args[1]
    assert "source /tmp/admt/myproj/env_snapshot.sh" in proxy_content


def test_ensure_env_snapshot_raises_on_baseline_capture_failure(svc, docker):
    def capture_side_effect(cmd, **_):
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)
        if cmd == ["env"]:
            return _fail(code=2, stderr="env broken")
        return _ok()

    docker.compose_exec_captured.side_effect = capture_side_effect
    with pytest.raises(ContainerError, match="capture container environment"):
        svc.ensure_env_snapshot()


def test_ensure_env_snapshot_raises_on_write_failure(svc, docker):
    def capture_side_effect(cmd, **_):
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)
        return CommandResult(returncode=0, stdout="PATH=/usr/bin\n")

    docker.compose_exec_captured.side_effect = capture_side_effect
    docker.compose_exec_with_stdin.return_value = _fail(code=3, stderr="write failed")
    with pytest.raises(ContainerError, match="Failed to write"):
        svc.ensure_env_snapshot()


def test_ensure_env_snapshot_raises_on_chmod_failure(svc, docker):
    call_log = []

    def capture_side_effect(cmd, **_):
        call_log.append(cmd)
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)
        if cmd[:2] == ["chmod", "+x"]:
            return _fail(code=4, stderr="nope")
        return CommandResult(returncode=0, stdout="PATH=/usr/bin\n")

    docker.compose_exec_captured.side_effect = capture_side_effect
    docker.compose_exec_with_stdin.return_value = _ok()
    with pytest.raises(ContainerError, match="chmod"):
        svc.ensure_env_snapshot()


# ----- refresh -----


def test_refresh_deletes_proxy_dir_then_regenerates(svc, docker):
    calls: list[list[str]] = []

    def capture_side_effect(cmd, **_):
        calls.append(cmd)
        if cmd[:2] == ["rm", "-rf"]:
            return _ok()
        if cmd[:2] == ["test", "-f"]:
            # After the delete, the proxy does NOT exist; force regeneration.
            return _fail(code=1)
        if cmd == ["env"]:
            return CommandResult(returncode=0, stdout="PATH=/usr/bin\n")
        if cmd[0] == "bash":
            return CommandResult(returncode=0, stdout="PATH=/opt/activated\n")
        return _ok()

    docker.compose_exec_captured.side_effect = capture_side_effect
    docker.compose_exec_with_stdin.return_value = _ok()
    svc.refresh()
    # First call was the delete.
    assert calls[0][:2] == ["rm", "-rf"]
    # The snapshot was rewritten (snapshot + proxy = 2 writes).
    expected_writes = 2
    assert docker.compose_exec_with_stdin.call_count == expected_writes


# ----- snapshot content helpers -----


def test_parse_env_output_ignores_lines_without_equals():
    out = dedent(
        """\
        KEY=value
        no-equals-here
        EMPTY=
        ANOTHER=yep
        """
    )
    env = ContainerService._parse_env_output(out)
    assert env == {"KEY": "value", "EMPTY": "", "ANOTHER": "yep"}


def test_parse_env_output_skips_invalid_shell_identifiers():
    """Activate-script chatter like ``Note:`` must not end up as an export.

    Real bug from the field: ``Note:=something`` split on first ``=`` produces
    key ``Note:``, which bash rejects as an identifier when the snapshot
    is sourced. The parser filters these out.
    """
    out = dedent(
        """\
        Note:=activation chatter
        1BAD_KEY=starts-with-digit
        bad-key=has-hyphen
        GOOD_KEY=value
        _underscore=ok
        """
    )
    env = ContainerService._parse_env_output(out)
    assert env == {"GOOD_KEY": "value", "_underscore": "ok"}


def test_snapshot_escapes_embedded_quotes(docker):
    svc = ContainerService(
        docker=docker,
        project=_project(),
        output=OutputService(verbose=False, quiet=True, yes=False, noninteractive=False),
    )
    baseline = {"KEY": "old"}
    activated = {"KEY": 'has "quotes"'}
    snapshot = svc._build_snapshot(baseline, activated)
    assert 'export KEY="has \\"quotes\\""' in snapshot
