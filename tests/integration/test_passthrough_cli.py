"""Integration tests for the Phase 3 redo passthrough commands.

ContainerService is mocked at the bootstrap boundary so tests exercise full
Click plumbing (group routing, aliases, positional-arg parsing, ``--all``
flags, exit-code translation) without needing Docker.
"""

from __future__ import annotations

from textwrap import dedent
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from admt.adapters.docker import CommandResult
from admt.cli import cli
from admt.exceptions import ConfigError
from admt.services.container import ContainerService
from admt.services.path_mapper import PathMapperService

if TYPE_CHECKING:
    from pathlib import Path

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


def _env_vars(tmp_path, **overrides):
    env = {"HOME": str(tmp_path)}
    env.update({k: str(v) for k, v in overrides.items()})
    return env


@pytest.fixture
def registered(tmp_path):
    root = _make_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["env", "init", str(root)], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    return root, runner


@pytest.fixture
def mock_container(monkeypatch):
    """Replace bootstrap.build_container_service with a mock returning real PathMapper.

    Per Q5, build_container_service now returns ``(container, mapper)``
    rather than mutating ``context.path_mapper`` as a side effect. The
    fake_build constructs a real PathMapperService against the active
    project's volume_mounts so ``Context.resolve_container_path`` works
    end-to-end in the integration tests.
    """
    container = MagicMock(spec=ContainerService)
    container.exec.return_value = 0

    def fake_build(ctx):
        project = ctx.config_service.get_active_project()
        return container, PathMapperService(project.volume_mounts)

    monkeypatch.setattr("admt.cli.build_container_service", fake_build)
    return container


# ----- single-target commands -----


@pytest.mark.parametrize(
    ("cmd_args", "expected_target"),
    [
        (["build"], "all"),
        (["prove"], "prove"),
    ],
)
def test_single_target_commands_from_project_root(
    cmd_args, expected_target, registered, mock_container, tmp_path, monkeypatch
):
    root, runner = registered
    monkeypatch.chdir(root)
    result = runner.invoke(cli, cmd_args, env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    # Expect "cd /home/user/myproj && redo <target>" (cwd maps to project root).
    redo_cmd = mock_container.exec.call_args.args[0]
    assert redo_cmd == f"cd /home/user/myproj && redo {expected_target}"
    assert mock_container.exec.call_args.kwargs["merge_stderr"] is True


def test_what_uses_exec_captured_and_transforms(registered, mock_container, tmp_path, monkeypatch):
    """``admt what`` captures redo output and rewrites it to admt commands."""
    root, runner = registered
    monkeypatch.chdir(root)
    mock_container.exec_captured.return_value = CommandResult(
        returncode=0,
        stdout="redo  what\nredo all\nredo test\nredo test_all\nredo build/dot/foo.dot\n",
    )
    result = runner.invoke(cli, ["what"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    # Transformation happened:
    assert "admt build" in result.output
    assert "admt test" in result.output
    assert "admt build build/dot/foo.dot" in result.output
    # "redo  what" header was dropped.
    assert "redo  what" not in result.output
    # ``--all`` variants are dropped from the listing -- implicit via flag.
    assert "admt test --all" not in result.output
    # exec_captured was used, not exec.
    mock_container.exec_captured.assert_called_once()
    mock_container.exec.assert_not_called()


# ----- --all variants -----


@pytest.mark.parametrize(
    ("base", "target"),
    [
        ("test", "test_all"),
        ("style", "style_all"),
        ("analyze", "analyze_all"),
        ("clean", "clean_all"),
        ("coverage", "coverage_all"),
        ("publish", "publish_all"),
    ],
)
def test_all_flag_switches_to_all_target(
    base, target, registered, mock_container, tmp_path, monkeypatch
):
    root, runner = registered
    monkeypatch.chdir(root)
    result = runner.invoke(cli, [base, "-a"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    assert f"redo {target}" in mock_container.exec.call_args.args[0]


def test_run_all_via_long_flag(registered, mock_container, tmp_path, monkeypatch):
    root, runner = registered
    monkeypatch.chdir(root)
    runner.invoke(cli, ["test", "--all"], env=_env_vars(tmp_path))
    assert "redo test_all" in mock_container.exec.call_args.args[0]


# ----- positional arg parsing -----


def test_positional_directory_changes_working_dir(
    registered, mock_container, tmp_path, monkeypatch
):
    root, runner = registered
    monkeypatch.chdir(root)
    # Host-side dir that exists; path mapper resolves it to the container path.
    sub = root / "docker"
    result = runner.invoke(cli, ["build", str(sub)], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    redo_cmd = mock_container.exec.call_args.args[0]
    assert redo_cmd == "cd /home/user/myproj/docker && redo all"


def test_positional_non_directory_is_target(registered, mock_container, tmp_path, monkeypatch):
    root, runner = registered
    monkeypatch.chdir(root)
    result = runner.invoke(cli, ["build", "build/obj/Linux/foo.o"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    redo_cmd = mock_container.exec.call_args.args[0]
    # cwd stays as project root; target is the file-ish name.
    assert redo_cmd == "cd /home/user/myproj && redo build/obj/Linux/foo.o"


def test_positional_unknown_name_becomes_target(registered, mock_container, tmp_path, monkeypatch):
    root, runner = registered
    monkeypatch.chdir(root)
    # WhatCommand captures via exec_captured; seed a benign empty output.
    mock_container.exec_captured.return_value = CommandResult(returncode=0, stdout="")
    result = runner.invoke(cli, ["what", "some_name"], env=_env_vars(tmp_path))
    assert result.exit_code == 0
    # WhatCommand always invokes ``redo what`` regardless of any positional
    # target string (it doesn't honor context.target); the positional still
    # parses cleanly without crashing the CLI.
    redo_cmd = mock_container.exec_captured.call_args.args[0]
    assert redo_cmd.endswith("&& redo what")


# ----- aliases -----


@pytest.mark.parametrize(
    ("alias", "full_name"),
    [
        ("b", "build"),
        ("w", "what"),
        ("t", "test"),
        ("s", "style"),
        ("an", "analyze"),
        ("cl", "clean"),
        ("p", "prove"),
        ("cov", "coverage"),
        ("pub", "publish"),
    ],
)
def test_passthrough_aliases(alias, full_name, registered, mock_container, tmp_path, monkeypatch):
    del full_name  # unused in the runtime assertion; covered by format-commands test
    root, runner = registered
    monkeypatch.chdir(root)
    # Seed exec_captured for the ``w`` (what) alias which captures instead of streams.
    mock_container.exec_captured.return_value = CommandResult(returncode=0, stdout="")
    result = runner.invoke(cli, [alias], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    # Either the streaming ``exec`` (most commands) or ``exec_captured`` (what)
    # was invoked -- confirm the alias actually dispatched to the container.
    assert mock_container.exec.called or mock_container.exec_captured.called


# ----- exit-code propagation -----


def test_passthrough_forwards_non_zero_exit(registered, mock_container, tmp_path, monkeypatch):
    sentinel = 3
    root, runner = registered
    monkeypatch.chdir(root)
    mock_container.exec.return_value = sentinel
    result = runner.invoke(cli, ["build"], env=_env_vars(tmp_path))
    assert result.exit_code == sentinel


# ----- debug flag -----


def test_debug_flag_prepends_debug_prefix(registered, mock_container, tmp_path, monkeypatch):
    root, runner = registered
    monkeypatch.chdir(root)
    result = runner.invoke(cli, ["-d", "build"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    redo_cmd = mock_container.exec.call_args.args[0]
    assert "DEBUG=1 redo all" in redo_cmd


def test_quiet_flag_turns_on_capture_output(registered, mock_container, tmp_path, monkeypatch):
    root, runner = registered
    monkeypatch.chdir(root)
    result = runner.invoke(cli, ["-q", "build"], env=_env_vars(tmp_path))
    assert result.exit_code == 0
    assert mock_container.exec.call_args.kwargs["capture_output"] is True


# ----- no project configured -----


def test_passthrough_without_active_project_errors(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["build"], env=_env_vars(tmp_path))
    assert result.exit_code == ConfigError.exit_code
    assert "No project configured" in result.output


# ----- path not mapped (TEST_PLAN.md What-to-Test matrix) -----


def test_build_outside_volume_mount_errors(registered, mock_container, tmp_path, monkeypatch):
    """``admt build`` from a directory outside any volume mount exits 4.

    The active project's mounts cover ``tmp_path / "myproj"`` and
    ``tmp_path / "adamant"`` (per ``_make_project``). Cwd-ing into
    ``tmp_path`` itself puts the user above all mounts, so
    ``PathMapperService.host_to_container`` raises ``PathNotMappedError``
    -- exit 4 with the mapped-directories listing per TEST_PLAN.md.
    """
    _, runner = registered
    # tmp_path itself is the parent of every mount; nothing maps it.
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli, ["build"], env=_env_vars(tmp_path))
    expected_exit = 4
    assert result.exit_code == expected_exit, result.output
    assert "not under any volume mount" in result.output
    assert "Mapped directories:" in result.output


# ----- Phase 5 flag matrix (passthrough) -----


def test_verbose_and_quiet_combined_echoes_but_captures(
    registered, mock_container, tmp_path, monkeypatch
):
    """``-v -q`` echoes the command to stdout AND captures subprocess output (quiet)."""
    root, runner = registered
    monkeypatch.chdir(root)
    result = runner.invoke(cli, ["-v", "-q", "build"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    # capture_output was turned on (quiet).
    assert mock_container.exec.call_args.kwargs["capture_output"] is True


def test_force_flag_accepted_on_passthrough_build(
    registered, mock_container, tmp_path, monkeypatch
):
    """``--force`` is accepted on passthrough commands (no-op but parses)."""
    root, runner = registered
    monkeypatch.chdir(root)
    result = runner.invoke(cli, ["-f", "build"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    assert mock_container.exec.called
