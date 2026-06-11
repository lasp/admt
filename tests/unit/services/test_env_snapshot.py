"""Tests for EnvSnapshotService -- ensure / refresh / parse / build (DockerAdapter mocked).

Covers the per-project ``/tmp/admt/<project>/`` machinery from
ARCHITECTURE.md §Environment Activation: capture baseline + activated
env, diff, write the snapshot script and proxy.
"""

from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock

import pytest

from admt.adapters.docker import CommandResult, DockerAdapter
from admt.exceptions import ContainerError
from admt.services.config import ProjectConfig
from admt.services.env_snapshot import EnvSnapshotService
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


@pytest.fixture
def docker():
    return MagicMock(spec=DockerAdapter)


@pytest.fixture
def output():
    return OutputService(verbose=False, quiet=True, yes=False, noninteractive=False)


@pytest.fixture
def svc(docker, output):
    return EnvSnapshotService(docker=docker, project=_project(), output=output)


# ----- ensure -----


def test_ensure_no_op_when_proxy_exists(svc, docker):
    docker.docker_exec_captured.return_value = _ok()
    assert svc.ensure() is False
    assert docker.docker_exec_captured.call_count == 1
    docker.docker_exec_with_stdin.assert_not_called()


def test_ensure_generates_when_missing(svc, docker):
    """First-run: test -f says missing -> capture baseline + activated, write both files."""

    def capture_side_effect(cmd, **_):
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)
        if cmd == ["env"]:
            return CommandResult(
                returncode=0,
                stdout="PATH=/usr/bin\nHOME=/home/user\n",
            )
        if cmd[:1] == ["cat"]:
            return CommandResult(
                returncode=0,
                stdout=(
                    "PATH=/opt/adamant:/usr/bin\nHOME=/home/user\nADAMANT_DIR=/home/user/adamant\n"
                ),
            )
        if cmd[:2] == ["chmod", "+x"]:
            return _ok()
        return _ok()

    docker.docker_exec_captured.side_effect = capture_side_effect
    docker.docker_exec.return_value = _ok()
    docker.docker_exec_with_stdin.return_value = _ok()
    assert svc.ensure() is True
    expected_writes = 2  # one snapshot + one proxy
    assert docker.docker_exec_with_stdin.call_count == expected_writes
    snapshot_call, proxy_call = docker.docker_exec_with_stdin.call_args_list
    snapshot_content = snapshot_call.args[1]
    assert "ADAMANT_DIR" in snapshot_content
    # PATH value changed -> in snapshot. HOME identical -> NOT in snapshot.
    assert 'export PATH="/opt/adamant:/usr/bin"' in snapshot_content
    assert "HOME=" not in snapshot_content
    proxy_content = proxy_call.args[1]
    assert "source /tmp/admt/myproj/env_snapshot.sh" in proxy_content


def test_ensure_streams_activate_with_merged_stderr(svc, docker):
    """The activate call goes through streaming ``docker_exec`` (stdio inherited).

    First-run activation can take many minutes; hiding it inside captured
    output looks like a hang. The dump itself goes to a container-side
    file so the user's terminal isn't flooded.
    """

    def capture_side_effect(cmd, **_):
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)
        if cmd == ["env"]:
            return CommandResult(returncode=0, stdout="PATH=/usr/bin\n")
        if cmd[:1] == ["cat"]:
            return CommandResult(returncode=0, stdout="PATH=/opt/activated\n")
        return _ok()

    docker.docker_exec_captured.side_effect = capture_side_effect
    docker.docker_exec.return_value = _ok()
    docker.docker_exec_with_stdin.return_value = _ok()
    svc.ensure()

    assert docker.docker_exec.call_count == 1
    args, kwargs = docker.docker_exec.call_args
    assert args[0][0] == "bash"
    assert args[0][1] == "-c"
    shell_cmd = args[0][2]
    assert "source /home/user/myproj/env/activate" in shell_cmd
    assert "env > /tmp/admt/myproj/env_activated" in shell_cmd
    assert kwargs["merge_stderr"] is True
    cat_calls = [
        call for call in docker.docker_exec_captured.call_args_list if call.args[0][:1] == ["cat"]
    ]
    assert len(cat_calls) == 1
    assert cat_calls[0].args[0] == ["cat", "/tmp/admt/myproj/env_activated"]  # noqa: S108 -- container-side path, not a host tmp file


def test_ensure_raises_on_baseline_capture_failure(svc, docker):
    def capture_side_effect(cmd, **_):
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)
        if cmd == ["env"]:
            return _fail(code=2, stderr="env broken")
        return _ok()

    docker.docker_exec_captured.side_effect = capture_side_effect
    with pytest.raises(ContainerError, match="capture container environment"):
        svc.ensure()


def test_ensure_raises_on_activate_streaming_failure(svc, docker):
    """A non-zero exit from the streaming activate surfaces as ContainerError."""

    def capture_side_effect(cmd, **_):
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)
        if cmd == ["env"]:
            return CommandResult(returncode=0, stdout="PATH=/usr/bin\n")
        return _ok()

    docker.docker_exec_captured.side_effect = capture_side_effect
    docker.docker_exec.return_value = CommandResult(returncode=7)
    with pytest.raises(ContainerError, match="env/activate failed in container"):
        svc.ensure()


def test_ensure_raises_on_activated_readback_failure(svc, docker):
    """If ``cat env_activated`` fails, ContainerError names the file."""

    def capture_side_effect(cmd, **_):
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)
        if cmd == ["env"]:
            return CommandResult(returncode=0, stdout="PATH=/usr/bin\n")
        if cmd[:1] == ["cat"]:
            return _fail(code=9, stderr="no such file")
        return _ok()

    docker.docker_exec_captured.side_effect = capture_side_effect
    docker.docker_exec.return_value = _ok()
    with pytest.raises(ContainerError, match="read activated environment"):
        svc.ensure()


def test_ensure_raises_on_write_failure(svc, docker):
    def capture_side_effect(cmd, **_):
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)
        return CommandResult(returncode=0, stdout="PATH=/usr/bin\n")

    docker.docker_exec_captured.side_effect = capture_side_effect
    docker.docker_exec.return_value = _ok()
    docker.docker_exec_with_stdin.return_value = _fail(code=3, stderr="write failed")
    with pytest.raises(ContainerError, match="Failed to write"):
        svc.ensure()


def test_ensure_raises_on_chmod_failure(svc, docker):
    def capture_side_effect(cmd, **_):
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)
        if cmd[:2] == ["chmod", "+x"]:
            return _fail(code=4, stderr="nope")
        return CommandResult(returncode=0, stdout="PATH=/usr/bin\n")

    docker.docker_exec_captured.side_effect = capture_side_effect
    docker.docker_exec.return_value = _ok()
    docker.docker_exec_with_stdin.return_value = _ok()
    with pytest.raises(ContainerError, match="chmod"):
        svc.ensure()


# ----- refresh -----


def test_refresh_deletes_proxy_dir_then_regenerates(svc, docker):
    calls: list[list[str]] = []

    def capture_side_effect(cmd, **_):
        calls.append(cmd)
        if cmd[:2] == ["rm", "-rf"]:
            return _ok()
        if cmd[:2] == ["test", "-f"]:
            return _fail(code=1)
        if cmd == ["env"]:
            return CommandResult(returncode=0, stdout="PATH=/usr/bin\n")
        if cmd[:1] == ["cat"]:
            return CommandResult(returncode=0, stdout="PATH=/opt/activated\n")
        return _ok()

    docker.docker_exec_captured.side_effect = capture_side_effect
    docker.docker_exec.return_value = _ok()
    docker.docker_exec_with_stdin.return_value = _ok()
    svc.refresh()
    assert calls[0][:2] == ["rm", "-rf"]
    expected_writes = 2
    assert docker.docker_exec_with_stdin.call_count == expected_writes


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
    env = EnvSnapshotService._parse_env_output(out)
    assert env == {"KEY": "value", "EMPTY": "", "ANOTHER": "yep"}


def test_parse_env_output_skips_invalid_shell_identifiers():
    """Activate-script chatter like ``Note:`` must not end up as an export.

    Real bug from the field: ``Note:=something`` split on first ``=`` produces
    key ``Note:``, which bash rejects as an identifier when the snapshot is
    sourced. The parser filters these out.
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
    env = EnvSnapshotService._parse_env_output(out)
    assert env == {"GOOD_KEY": "value", "_underscore": "ok"}


def test_snapshot_escapes_embedded_quotes(svc):
    """Values with embedded ``"`` are backslash-escaped so the export string is valid bash."""
    baseline = {"KEY": "old"}
    activated = {"KEY": 'has "quotes"'}
    snapshot = svc._build_snapshot(baseline, activated)
    assert 'export KEY="has \\"quotes\\""' in snapshot


def test_proxy_path_and_snapshot_path_are_per_project(svc):
    """Container-side paths live under ``/tmp/admt/<project>/`` per ARCHITECTURE.md."""
    assert svc.proxy_path() == "/tmp/admt/myproj/exec.sh"  # noqa: S108 -- container path
    assert svc.snapshot_path() == "/tmp/admt/myproj/env_snapshot.sh"  # noqa: S108 -- container path
    assert svc.project_tmp_dir() == "/tmp/admt/myproj"  # noqa: S108 -- container path
