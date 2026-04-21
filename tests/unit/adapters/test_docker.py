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


def test_compose_exec_non_interactive_uses_t_flag(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
        adapter.compose_exec(["echo", "hi"])
    _assert_streamed(adapter, ["exec", "-u", "user", "-T", "svc", "echo", "hi"], popen)


def test_compose_exec_interactive_uses_it(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
        adapter.compose_exec(["bash"], interactive=True)
    _assert_streamed(adapter, ["exec", "-u", "user", "-it", "svc", "bash"], popen)


def test_compose_exec_custom_user(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock()) as popen:
        adapter.compose_exec(["whoami"], user="root")
    _assert_streamed(adapter, ["exec", "-u", "root", "-T", "svc", "whoami"], popen)


def test_streaming_forwards_return_code(adapter):
    with patch("subprocess.Popen", return_value=_make_popen_mock(returncode=SENTINEL_EXIT)):
        result = adapter.compose_up()
    assert result.returncode == SENTINEL_EXIT


# ----- captured methods -----


def test_compose_exec_captured_uses_subprocess_run(adapter):
    with patch("subprocess.run", return_value=_make_completed(stdout="hi\n")) as run:
        result = adapter.compose_exec_captured(["echo", "hi"])
    assert result.stdout == "hi\n"
    run_args = run.call_args.args[0]
    assert run_args[:2] == ["docker", "compose"]
    assert run_args[-7:] == ["exec", "-T", "-u", "user", "svc", "echo", "hi"]


def test_compose_exec_captured_includes_timeout(adapter):
    with patch("subprocess.run", return_value=_make_completed()) as run:
        adapter.compose_exec_captured(["true"])
    assert run.call_args.kwargs["timeout"] > 0
    assert run.call_args.kwargs["capture_output"] is True


def test_compose_exec_captured_timeout_raises(adapter):
    with (
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30)),
        pytest.raises(ContainerError, match="timed out"),
    ):
        adapter.compose_exec_captured(["hung"])


def test_compose_exec_with_stdin_pipes_input(adapter):
    with patch("subprocess.run", return_value=_make_completed()) as run:
        adapter.compose_exec_with_stdin(["cat"], "payload\n")
    assert run.call_args.kwargs["input"] == "payload\n"


def test_compose_exec_with_stdin_timeout_raises(adapter):
    with (
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=30)),
        pytest.raises(ContainerError, match="timed out"),
    ):
        adapter.compose_exec_with_stdin(["cat"], "payload")


def test_compose_ps_uses_json_format(adapter):
    with patch("subprocess.run", return_value=_make_completed(stdout="[]")) as run:
        result = adapter.compose_ps()
    assert result.stdout == "[]"
    run_args = run.call_args.args[0]
    assert "ps" in run_args
    assert run_args[-2:] == ["--format", "json"]


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
