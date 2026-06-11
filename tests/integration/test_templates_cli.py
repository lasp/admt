"""Integration tests for ``admt templates`` -- redo + copy + ``--undo``.

ContainerService is mocked so redo calls don't actually run, but the
host-side file operations (stub discovery, backup, restore) are real.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from admt.cli import cli
from admt.exceptions import ArgumentError
from admt.services.container import ContainerService
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


def _env_vars(tmp_path, **overrides):
    env = {"HOME": str(tmp_path)}
    env.update({k: str(v) for k, v in overrides.items()})
    return env


def _seed_stubs(project_dir: Path) -> None:
    # redo templates lands stubs in ``build/template/``, per Adamant's
    # ``redo/rules/build_templates.py``. Mirror that layout here so the
    # integration test exercises the real path.
    build_template = project_dir / "build" / "template"
    build_template.mkdir(parents=True)
    (build_template / "component-foo-implementation.ads").write_text("NEW SPEC\n")
    (build_template / "component-foo-implementation.adb").write_text("NEW BODY\n")


@pytest.fixture
def registered(tmp_path):
    root = _make_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["env", "init", str(root)], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    return root, runner


@pytest.fixture
def mock_container(monkeypatch):
    container = MagicMock(spec=ContainerService)
    container.exec.return_value = 0

    def fake_build(ctx):
        project = ctx.config_service.get_active_project()
        return container, PathMapperService(project.volume_mounts)

    monkeypatch.setattr("admt.cli.build_container_service", fake_build)
    return container


# ----- templates run -----


def test_templates_runs_redo_and_copies_with_yes(registered, mock_container, tmp_path, monkeypatch):
    root, runner = registered
    _seed_stubs(root)
    monkeypatch.chdir(root)
    result = runner.invoke(cli, ["-y", "templates"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    # redo templates was invoked.
    assert mock_container.exec.called
    # Stubs were copied to the project root (source directory).
    assert (root / "component-foo-implementation.ads").read_text() == "NEW SPEC\n"
    assert (root / "component-foo-implementation.adb").read_text() == "NEW BODY\n"


def test_templates_noninteractive_skips_copy(registered, mock_container, tmp_path, monkeypatch):
    root, runner = registered
    _seed_stubs(root)
    monkeypatch.chdir(root)
    result = runner.invoke(cli, ["templates"], env=_env_vars(tmp_path, ADMT_NONINTERACTIVE="1"))
    assert result.exit_code == 0, result.output
    # redo ran, but copy was skipped.
    assert mock_container.exec.called
    assert not (root / "component-foo-implementation.ads").exists()


def test_templates_force_skips_prompt(registered, mock_container, tmp_path, monkeypatch):
    root, runner = registered
    _seed_stubs(root)
    monkeypatch.chdir(root)
    result = runner.invoke(cli, ["-f", "templates"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    assert (root / "component-foo-implementation.ads").read_text() == "NEW SPEC\n"


def test_templates_prompt_decline_via_stdin(registered, mock_container, tmp_path, monkeypatch):
    root, runner = registered
    _seed_stubs(root)
    monkeypatch.chdir(root)
    # Input "n" declines the prompt; redo still ran, but no copy.
    result = runner.invoke(cli, ["templates"], env=_env_vars(tmp_path), input="n\n")
    assert result.exit_code == 0, result.output
    assert not (root / "component-foo-implementation.ads").exists()


def test_templates_no_stubs_no_copy(registered, mock_container, tmp_path, monkeypatch):
    root, runner = registered
    # No build/template/ at all; templates still runs redo and reports "no stubs".
    monkeypatch.chdir(root)
    result = runner.invoke(cli, ["-y", "templates"], env=_env_vars(tmp_path))
    assert result.exit_code == 0
    assert "No implementation stubs found" in result.output


# ----- backup + undo -----


def test_templates_backs_up_existing_files(registered, mock_container, tmp_path, monkeypatch):
    root, runner = registered
    _seed_stubs(root)
    # Pre-existing destination with OLD content.
    (root / "component-foo-implementation.ads").write_text("OLD SPEC\n")
    (root / "component-foo-implementation.adb").write_text("OLD BODY\n")
    monkeypatch.chdir(root)
    result = runner.invoke(cli, ["-f", "templates"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    # Destination now has NEW content.
    assert (root / "component-foo-implementation.ads").read_text() == "NEW SPEC\n"
    # Marker file was written; backup contains OLD content + manifest.
    marker = tmp_path / ".admt" / "backup-latest"
    assert marker.exists()
    backup_dir = Path(marker.read_text().strip())
    assert (backup_dir / "component-foo-implementation.ads").read_text() == "OLD SPEC\n"
    manifest = json.loads((backup_dir / "manifest.json").read_text())
    assert Path(manifest["source_dir"]) == root


def test_templates_undo_restores_backup(registered, mock_container, tmp_path, monkeypatch):
    root, runner = registered
    _seed_stubs(root)
    (root / "component-foo-implementation.ads").write_text("OLD SPEC\n")
    (root / "component-foo-implementation.adb").write_text("OLD BODY\n")
    monkeypatch.chdir(root)
    # First: run templates with --force to create the backup.
    runner.invoke(cli, ["-f", "templates"], env=_env_vars(tmp_path))
    assert (root / "component-foo-implementation.ads").read_text() == "NEW SPEC\n"
    # Now undo.
    result = runner.invoke(cli, ["templates", "--undo"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    assert (root / "component-foo-implementation.ads").read_text() == "OLD SPEC\n"
    assert (root / "component-foo-implementation.adb").read_text() == "OLD BODY\n"


def test_templates_undo_with_no_backup_errors(registered, mock_container, tmp_path):
    _, runner = registered
    result = runner.invoke(cli, ["templates", "--undo"], env=_env_vars(tmp_path))
    assert result.exit_code == ArgumentError.exit_code
    assert "No template backup" in result.output


# ----- alias -----


def test_tmpl_alias_works(registered, mock_container, tmp_path, monkeypatch):
    """``admt tmpl`` resolves to ``admt templates``; global flags go *before*
    the subcommand per ARCHITECTURE.md §Flag Placement.
    """
    root, runner = registered
    _seed_stubs(root)
    monkeypatch.chdir(root)
    # ``-y`` before the alias auto-accepts the (default=True) copy prompt.
    result = runner.invoke(cli, ["-y", "tmpl"], env=_env_vars(tmp_path))
    assert result.exit_code == 0, result.output
    assert (root / "component-foo-implementation.ads").exists()
