"""Tests for DockerAdapter -- command construction + subprocess orchestration (mocked)."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from admt.adapters.docker import (
    DockerAdapter,
    _active_processes,
    _detect_compose_command,
    _track_subprocess,
    iter_active_pids,
)
from admt.exceptions import ContainerError

SENTINEL_EXIT = 7


@pytest.fixture
def compose_path(tmp_path):
    path = tmp_path / "docker-compose.yml"
    path.write_text("")
    return path


@pytest.fixture
def adapter(compose_path):
    return DockerAdapter(
        compose_file=compose_path,
        service_name="svc",
        container_name="svc_container",
        compose_cmd=["docker", "compose"],
    )


def _make_popen_mock(returncode=0):
    proc = MagicMock(spec=subprocess.Popen)
    proc.pid = 12345
    proc.wait.return_value = returncode
    proc.poll.return_value = returncode
    return proc


def _make_completed(returncode=0, stdout="", stderr=""):
    completed = MagicMock(spec=subprocess.CompletedProcess)
    completed.returncode = returncode
    completed.stdout = stdout
    completed.stderr = stderr
    return completed


# ----- compose command detection -----


def test_detect_compose_prefers_docker_over_docker_compose():
    with patch("admt.adapters.docker.shutil.which") as which:
        which.side_effect = lambda name: f"/usr/bin/{name}" if name == "docker" else None
        assert _detect_compose_command() == ["docker", "compose"]


def test_detect_compose_falls_back_to_legacy_binary():
    with patch("admt.adapters.docker.shutil.which") as which:
        which.side_effect = lambda name: (
            "/usr/bin/docker-compose" if name == "docker-compose" else None
        )
        assert _detect_compose_command() == ["docker-compose"]


def test_detect_compose_raises_when_neither_on_path():
    with (
        patch("admt.adapters.docker.shutil.which", return_value=None),
        pytest.raises(ContainerError, match="Neither 'docker'"),
    ):
        _detect_compose_command()


# ----- streaming lifecycle methods -----


def _assert_streamed(adapter, expected_tail, mock_popen):
    """Shared assertion: Popen received the compose prefix + expected tail args."""
    call_args = mock_popen.call_args.args[0]
    assert call_args[:2] == ["docker", "compose"]
    assert call_args[2:4] == ["-f", str(adapter.compose_file)]
    assert call_args[4:] == expected_tail


def test_compose_up_builds_argv(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
        result = adapter.compose_up()
    assert result.returncode == 0
    _assert_streamed(adapter, ["up", "-d"], popen)


def test_compose_stop_builds_argv(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
        adapter.compose_stop()
    _assert_streamed(adapter, ["stop"], popen)


def test_compose_down_without_volumes(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
        adapter.compose_down()
    _assert_streamed(adapter, ["down"], popen)


def test_compose_down_with_volumes(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
        adapter.compose_down(remove_volumes=True)
    _assert_streamed(adapter, ["down", "-v"], popen)


def test_compose_build_push_pull(adapter):
    for method, expected in [
        (adapter.compose_build, ["build"]),
        (adapter.compose_push, ["push"]),
        (adapter.compose_pull, ["pull"]),
    ]:
        with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
            method()
        _assert_streamed(adapter, expected, popen)


def test_streaming_forwards_return_code(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock(returncode=SENTINEL_EXIT)):
        result = adapter.compose_up()
    assert result.returncode == SENTINEL_EXIT


# ----- docker_exec family (bypasses compose, targets container by name) -----


def test_docker_exec_non_interactive_uses_i_flag(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
        adapter.docker_exec(["echo", "hi"])
    assert popen.call_args.args[0] == [
        "docker",
        "exec",
        "-u",
        "user",
        "-i",
        "svc_container",
        "echo",
        "hi",
    ]


def test_docker_exec_interactive_uses_it(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
        adapter.docker_exec(["bash"], interactive=True)
    assert popen.call_args.args[0] == [
        "docker",
        "exec",
        "-u",
        "user",
        "-it",
        "svc_container",
        "bash",
    ]


def test_docker_exec_custom_user(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
        adapter.docker_exec(["whoami"], user="root")
    # User flag changes; "-i" for non-interactive stays.
    assert popen.call_args.args[0][:6] == [
        "docker",
        "exec",
        "-u",
        "root",
        "-i",
        "svc_container",
    ]


def test_docker_exec_merge_stderr_passes_stdout_redirect(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
        adapter.docker_exec(["redo", "all"], merge_stderr=True)
    assert popen.call_args.kwargs["stderr"] is subprocess.STDOUT


def test_docker_exec_without_merge_inherits_stderr(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
        adapter.docker_exec(["bash", "-c", "true"])
    assert popen.call_args.kwargs.get("stderr") is None


def test_docker_exec_capture_output_uses_subprocess_run(adapter):
    with patch("subprocess.run", return_value=_make_completed(stdout="ok", stderr="warn")) as run:
        result = adapter.docker_exec(["redo", "all"], capture_output=True)
    assert result.returncode == 0
    assert result.stdout == "ok"
    assert result.stderr == "warn"
    # capture_output has no timeout (user-bounded build).
    assert "timeout" not in run.call_args.kwargs or run.call_args.kwargs["timeout"] is None
    # When ``merge_stderr`` is False (default), stdout and stderr are piped
    # separately via explicit PIPE args (not ``capture_output=True`` shorthand,
    # which would prevent honoring ``merge_stderr=True`` on another call).
    assert run.call_args.kwargs["stdout"] is subprocess.PIPE
    assert run.call_args.kwargs["stderr"] is subprocess.PIPE


def test_docker_exec_capture_with_merge_stderr_redirects_to_stdout(adapter):
    """Regression: ``capture_output=True`` + ``merge_stderr=True`` must plumb
    stderr to STDOUT at the subprocess level.

    Without this, redo's output (which redo writes to stderr) ends up in
    ``result.stderr`` and a caller that inspects only ``result.stdout`` --
    like ``WhatCommand._transform`` -- sees nothing and emits nothing.
    """
    with patch(
        "subprocess.run",
        return_value=_make_completed(stdout="merged output", stderr=""),
    ) as run:
        result = adapter.docker_exec(["redo", "what"], capture_output=True, merge_stderr=True)
    # subprocess.run was invoked with stderr=STDOUT so the child's stderr
    # stream folded into stdout before capture.
    assert run.call_args.kwargs["stderr"] is subprocess.STDOUT
    assert run.call_args.kwargs["stdout"] is subprocess.PIPE
    # ``capture_output=True`` shorthand is NOT used (it would force
    # stderr=PIPE and drop the redirect).
    assert run.call_args.kwargs.get("capture_output") is not True
    # The merged content lands on result.stdout.
    assert result.stdout == "merged output"


# ----- docker_exec streaming with line_transform -----


def _popen_stub_with_stdout(lines, returncode=0):
    """Popen stand-in whose ``stdout`` iterates the given lines."""
    stub = _make_popen_mock(returncode=returncode)
    stub.stdout = iter(lines)
    return stub


def test_docker_exec_line_transform_rewrites_each_stdout_line(adapter, capsys):
    """Each stdout line is fed through the transform before reaching the user."""
    captured = _popen_stub_with_stdout(["redo  all\n", "redo    build/x.adb\n"])

    def transform(line):
        return f"TX:{line.rstrip()}"

    with patch("subprocess.Popen", return_value=captured) as popen:
        result = adapter.docker_exec(["redo", "all"], line_transform=transform, merge_stderr=True)

    out = capsys.readouterr().out
    assert out == "TX:redo  all\nTX:redo    build/x.adb\n"
    assert result.returncode == 0
    # stdout is piped so we can intercept; stderr merges so redo's human
    # output reaches the transform.
    assert popen.call_args.kwargs["stdout"] is subprocess.PIPE
    assert popen.call_args.kwargs["stderr"] is subprocess.STDOUT


def test_docker_exec_line_transform_drops_none_returns(adapter, capsys):
    """When the transform returns None, the line is skipped."""
    captured = _popen_stub_with_stdout(["keep\n", "skip\n", "keep\n"])

    def transform(line):
        return None if line.startswith("skip") else line

    with patch("subprocess.Popen", return_value=captured):
        adapter.docker_exec(["echo"], line_transform=transform)

    # Only the "keep" lines reach stdout.
    assert capsys.readouterr().out == "keep\nkeep\n"


def test_docker_exec_line_transform_appends_missing_newline(adapter, capsys):
    """Transformed output without a trailing newline gets one appended."""
    captured = _popen_stub_with_stdout(["in\n"])

    def transform(line):
        return line.rstrip("\n")  # strip the newline

    with patch("subprocess.Popen", return_value=captured):
        adapter.docker_exec(["echo"], line_transform=transform)

    assert capsys.readouterr().out == "in\n"


def test_docker_exec_line_transform_without_merge_stderr_leaves_stderr_inherited(adapter):
    """``merge_stderr=False`` with a transform uses default stderr (parent's)."""
    captured = _popen_stub_with_stdout([])

    with patch("subprocess.Popen", return_value=captured) as popen:
        adapter.docker_exec(["echo"], line_transform=lambda line: line, merge_stderr=False)

    assert popen.call_args.kwargs.get("stderr") is None


def test_docker_exec_line_transform_ignored_in_interactive_mode(adapter):
    """Interactive shells can't route through a line transform; fall back to streaming."""
    with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
        adapter.docker_exec(["bash"], interactive=True, line_transform=lambda line: line)
    # ``stdout`` was NOT piped -- streaming-transform path was bypassed.
    assert popen.call_args.kwargs.get("stdout") is None


# ----- captured and stdin docker_exec variants -----


def test_docker_exec_captured_builds_argv_and_captures(adapter):
    with patch("subprocess.run", return_value=_make_completed(stdout="hi\n")) as run:
        result = adapter.docker_exec_captured(["echo", "hi"])
    assert result.stdout == "hi\n"
    assert run.call_args.args[0] == [
        "docker",
        "exec",
        "-i",
        "-u",
        "user",
        "svc_container",
        "echo",
        "hi",
    ]
    assert run.call_args.kwargs["timeout"] > 0
    assert run.call_args.kwargs["capture_output"] is True


def test_docker_exec_captured_timeout_raises(adapter):
    with (
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30)),
        pytest.raises(ContainerError, match="timed out"),
    ):
        adapter.docker_exec_captured(["hung"])


def test_docker_exec_captured_accepts_none_timeout(adapter):
    """``timeout=None`` is forwarded verbatim so subprocess.run won't enforce a cap.

    Used for long-running captures whose duration is user-bounded -- e.g.,
    sourcing ``env/activate``, which pip-installs and alr-builds on first
    run.
    """
    with patch("subprocess.run", return_value=_make_completed(stdout="")) as run:
        adapter.docker_exec_captured(["bash", "-c", "slow"], timeout=None)
    assert run.call_args.kwargs["timeout"] is None


def test_docker_exec_with_stdin_pipes_input(adapter):
    with patch("subprocess.run", return_value=_make_completed()) as run:
        adapter.docker_exec_with_stdin(["cat"], "payload\n")
    assert run.call_args.kwargs["input"] == "payload\n"
    assert run.call_args.args[0][:6] == [
        "docker",
        "exec",
        "-i",
        "-u",
        "user",
        "svc_container",
    ]


def test_docker_exec_with_stdin_timeout_raises(adapter):
    with (
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30)),
        pytest.raises(ContainerError, match="timed out"),
    ):
        adapter.docker_exec_with_stdin(["cat"], "payload")


# ----- docker_inspect_state -----


def test_docker_inspect_state_builds_argv(adapter):
    with patch("subprocess.run", return_value=_make_completed(stdout="running\n")) as run:
        result = adapter.docker_inspect_state()
    assert result.stdout == "running\n"
    assert run.call_args.args[0] == [
        "docker",
        "inspect",
        "-f",
        "{{.State.Status}}",
        "svc_container",
    ]


def test_docker_inspect_state_timeout_raises(adapter):
    with (
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30)),
        pytest.raises(ContainerError, match="timed out"),
    ):
        adapter.docker_inspect_state()


def test_docker_inspect_state_surfaces_non_zero_exit(adapter):
    with patch(
        "subprocess.run",
        return_value=_make_completed(returncode=1, stderr="Error: No such object: svc_container\n"),
    ):
        result = adapter.docker_inspect_state()
    assert result.returncode == 1
    assert "no such object" in result.stderr.lower()


# ----- image helpers -----


def test_image_name_returns_first_line(adapter):
    with patch("subprocess.run", return_value=_make_completed(stdout="registry/img:tag\n")):
        assert adapter.image_name() == "registry/img:tag"


def test_image_name_returns_none_on_non_zero_exit(adapter):
    with patch("subprocess.run", return_value=_make_completed(returncode=1)):
        assert adapter.image_name() is None


def test_image_name_returns_none_when_empty(adapter):
    with patch("subprocess.run", return_value=_make_completed(stdout="\n")):
        assert adapter.image_name() is None


def test_image_name_timeout_raises(adapter):
    """image_name goes through _run_captured; the bounded timeout must surface."""
    with (
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30)),
        pytest.raises(ContainerError, match="timed out"),
    ):
        adapter.image_name()


def test_remove_image_no_op_when_no_image(adapter):
    with patch.object(adapter, "image_name", return_value=None):
        result = adapter.remove_image()
    assert result.returncode == 0
    assert "no image" in result.stderr


def test_image_exists_locally_true_on_zero_exit(adapter):
    with patch("subprocess.run", return_value=_make_completed(returncode=0)) as run:
        assert adapter.image_exists_locally("img:tag") is True
    assert run.call_args.args[0] == ["docker", "image", "inspect", "img:tag"]


def test_image_exists_locally_false_on_non_zero(adapter):
    with patch("subprocess.run", return_value=_make_completed(returncode=1)):
        assert adapter.image_exists_locally("img:tag") is False


def test_image_exists_locally_timeout_raises(adapter):
    with (
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30)),
        pytest.raises(ContainerError, match="timed out"),
    ):
        adapter.image_exists_locally("img:tag")


def test_remove_image_invokes_docker_image_rm(adapter):
    with (
        patch.object(adapter, "image_name", return_value="img:tag"),
        patch("subprocess.Popen", return_value=_make_popen_mock()) as popen,
    ):
        result = adapter.remove_image()
    assert result.returncode == 0
    assert popen.call_args.args[0] == ["docker", "image", "rm", "-f", "img:tag"]


# ----- active-process tracking -----


def test_iter_active_pids_empty_initially():
    # Any leftover entries from other tests should be gone once the context
    # manager exited; assert current snapshot is empty.
    assert iter_active_pids() == []


def test_track_subprocess_tolerates_already_removed_process():
    """Exercise the defensive ``if proc in _active_processes`` branch.

    If something clears the registry mid-call, the cleanup loop must
    quietly skip the already-removed process instead of raising.
    """
    fake_proc = _make_popen_mock()
    with _track_subprocess() as register:
        register(fake_proc)
        assert fake_proc in _active_processes
        _active_processes.remove(fake_proc)
    # Context manager exited cleanly; the process is gone from the registry.
    assert fake_proc not in _active_processes


def test_iter_active_pids_reports_running_process(adapter):
    observed = []
    # Build the Popen stand-in BEFORE patching subprocess.Popen, so
    # MagicMock(spec=subprocess.Popen) uses the real class (not the patch).
    popen_stub = _make_popen_mock()
    # poll() returning None means "still running"; the tracker filters on this.
    popen_stub.poll.return_value = None
    popen_stub.wait.side_effect = lambda: observed.append(iter_active_pids()) or 0

    with patch("subprocess.Popen", return_value=popen_stub):
        adapter.compose_up()
    assert observed == [[12345]]
    # After the call, the registry is cleared.
    assert iter_active_pids() == []
