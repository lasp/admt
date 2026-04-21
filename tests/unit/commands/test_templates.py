"""Unit tests for TemplatesCommand -- stub discovery, copy, backup, undo."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from admt.commands.templates import TemplatesCommand, _find_stubs
from admt.exceptions import ArgumentError
from admt.services.container import ContainerService
from admt.services.output import OutputService
from admt.services.path_mapper import PathMapperService


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect ``Path.home()`` so the backup-latest marker lands in tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _ctx_with_container(make_context, **overrides):
    container = MagicMock(spec=ContainerService)
    container.exec.return_value = 0
    mapper = PathMapperService({Path("/sim/proj"): Path("/home/user/proj")})
    # Use a real OutputService so prompt() actually honors monkeypatched input,
    # --yes auto-accept, and ADMT_NONINTERACTIVE behavior. Align its flags with
    # the Context flags so ``context.yes`` / ``context.noninteractive`` stay in
    # sync with what OutputService uses for prompt policy.
    output = OutputService(
        verbose=False,
        quiet=True,
        yes=bool(overrides.get("yes", False)),
        noninteractive=bool(overrides.get("noninteractive", False)),
    )
    defaults = {
        "path_mapper": mapper,
        "container_service": container,
        "path": Path("/sim/proj"),
        "output": output,
    }
    defaults.update(overrides)
    return make_context(**defaults), container


# ----- stub detection -----


def test_find_stubs_empty_dir(tmp_path):
    assert _find_stubs(tmp_path) == []


def test_find_stubs_missing_dir(tmp_path):
    assert _find_stubs(tmp_path / "nonexistent") == []


def test_find_stubs_finds_ads_and_adb(tmp_path):
    (tmp_path / "component-foo-implementation.ads").touch()
    (tmp_path / "component-foo-implementation.adb").touch()
    stubs = _find_stubs(tmp_path)
    assert {p.name for p in stubs} == {
        "component-foo-implementation.ads",
        "component-foo-implementation.adb",
    }


def test_find_stubs_ignores_unrelated_files(tmp_path):
    (tmp_path / "component-foo-implementation.ads").touch()
    (tmp_path / "random.txt").touch()
    (tmp_path / "component-foo.ads").touch()  # wrong pattern
    stubs = _find_stubs(tmp_path)
    assert [p.name for p in stubs] == ["component-foo-implementation.ads"]


def test_find_stubs_multiple_components(tmp_path):
    names = ["alpha", "beta"]
    expected_count = len(names) * 2  # .ads + .adb per component
    for name in names:
        (tmp_path / f"component-{name}-implementation.ads").touch()
        (tmp_path / f"component-{name}-implementation.adb").touch()
    stubs = _find_stubs(tmp_path)
    assert len(stubs) == expected_count
    assert all("implementation" in p.name for p in stubs)


# ----- --undo flow -----


def test_undo_raises_when_no_marker(fake_home, make_context):
    ctx, _ = _ctx_with_container(make_context)
    with pytest.raises(ArgumentError, match="No template backup found"):
        TemplatesCommand(undo=True).execute(ctx)


def test_undo_raises_when_marker_points_to_missing_dir(fake_home, make_context):
    marker = fake_home / ".admt" / "backup-latest"
    marker.parent.mkdir(parents=True)
    marker.write_text(str(fake_home / "gone") + "\n")
    ctx, _ = _ctx_with_container(make_context)
    with pytest.raises(ArgumentError, match="missing"):
        TemplatesCommand(undo=True).execute(ctx)


def test_undo_raises_when_manifest_missing(fake_home, make_context):
    backup_dir = fake_home / "backup"
    backup_dir.mkdir()
    marker = fake_home / ".admt" / "backup-latest"
    marker.parent.mkdir(parents=True)
    marker.write_text(str(backup_dir) + "\n")
    ctx, _ = _ctx_with_container(make_context)
    with pytest.raises(ArgumentError, match="manifest"):
        TemplatesCommand(undo=True).execute(ctx)


def test_undo_restores_from_manifest(fake_home, tmp_path, make_context):
    source_dir = tmp_path / "comp"
    source_dir.mkdir()
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "component-foo-implementation.ads").write_text("OLD SPEC\n")
    (backup_dir / "component-foo-implementation.adb").write_text("OLD BODY\n")
    (backup_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_dir": str(source_dir),
                "files": [
                    "component-foo-implementation.ads",
                    "component-foo-implementation.adb",
                ],
            }
        )
    )
    marker = fake_home / ".admt" / "backup-latest"
    marker.parent.mkdir(parents=True)
    marker.write_text(str(backup_dir) + "\n")
    # Destination files exist with different content (simulating the
    # "stubs were written over originals" state we want to undo).
    (source_dir / "component-foo-implementation.ads").write_text("NEW STUB\n")
    (source_dir / "component-foo-implementation.adb").write_text("NEW STUB\n")
    ctx, _ = _ctx_with_container(make_context)
    result = TemplatesCommand(undo=True).execute(ctx)
    assert result.exit_code == 0
    assert (source_dir / "component-foo-implementation.ads").read_text() == "OLD SPEC\n"
    assert (source_dir / "component-foo-implementation.adb").read_text() == "OLD BODY\n"


# ----- copy flow -----


def _build_project(project_dir: Path) -> None:
    build_src = project_dir / "build" / "src"
    build_src.mkdir(parents=True)
    (build_src / "component-foo-implementation.ads").write_text("NEW SPEC\n")
    (build_src / "component-foo-implementation.adb").write_text("NEW BODY\n")


def test_handle_stub_copy_no_stubs_info(tmp_path, make_context, capsys):
    (tmp_path / "build").mkdir()  # no src/ subdir
    ctx, container = _ctx_with_container(make_context, path=tmp_path, yes=True)
    # Short-circuit the parent redo call -- _handle_stub_copy only runs after
    # a successful redo, so we invoke the private method directly.
    result = TemplatesCommand()._handle_stub_copy(ctx)
    assert result.exit_code == 0
    # No copy attempted.
    container.exec.assert_not_called()


def test_handle_stub_copy_copies_when_prompt_accepts(
    fake_home, tmp_path, make_context, monkeypatch
):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _build_project(project_dir)
    ctx, _ = _ctx_with_container(make_context, path=project_dir)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    TemplatesCommand()._handle_stub_copy(ctx)
    assert (project_dir / "component-foo-implementation.ads").read_text() == "NEW SPEC\n"


def test_handle_stub_copy_declined_does_nothing(fake_home, tmp_path, make_context, monkeypatch):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _build_project(project_dir)
    ctx, _ = _ctx_with_container(make_context, path=project_dir)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    TemplatesCommand()._handle_stub_copy(ctx)
    assert not (project_dir / "component-foo-implementation.ads").exists()


def test_handle_stub_copy_noninteractive_skips(fake_home, tmp_path, make_context):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _build_project(project_dir)
    ctx, _ = _ctx_with_container(make_context, path=project_dir, noninteractive=True)
    TemplatesCommand()._handle_stub_copy(ctx)
    assert not (project_dir / "component-foo-implementation.ads").exists()


def test_handle_stub_copy_force_skips_prompt(fake_home, tmp_path, make_context):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _build_project(project_dir)
    ctx, _ = _ctx_with_container(make_context, path=project_dir, force=True)
    TemplatesCommand()._handle_stub_copy(ctx)
    assert (project_dir / "component-foo-implementation.ads").read_text() == "NEW SPEC\n"


def test_handle_stub_copy_yes_accepts_default(fake_home, tmp_path, make_context):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _build_project(project_dir)
    # ``--yes`` + default=True in the prompt -> auto-accepts without stdin.
    ctx, _ = _ctx_with_container(make_context, path=project_dir, yes=True)
    TemplatesCommand()._handle_stub_copy(ctx)
    assert (project_dir / "component-foo-implementation.ads").read_text() == "NEW SPEC\n"


def test_handle_stub_copy_backs_up_existing(fake_home, tmp_path, make_context):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _build_project(project_dir)
    # Pre-existing destination file -- backup must capture it.
    (project_dir / "component-foo-implementation.ads").write_text("OLD SPEC\n")
    ctx, _ = _ctx_with_container(make_context, path=project_dir, force=True)
    result = TemplatesCommand()._handle_stub_copy(ctx)
    # Destination now has the new content.
    assert (project_dir / "component-foo-implementation.ads").read_text() == "NEW SPEC\n"
    # Backup marker is set.
    marker = fake_home / ".admt" / "backup-latest"
    assert marker.exists()
    backup_dir = Path(marker.read_text().strip())
    # Backup contains the old content.
    assert (backup_dir / "component-foo-implementation.ads").read_text() == "OLD SPEC\n"
    # Manifest records the source dir.
    manifest = json.loads((backup_dir / "manifest.json").read_text())
    assert Path(manifest["source_dir"]) == project_dir
    assert "component-foo-implementation.ads" in manifest["files"]
    # Result reflects the action.
    assert result.exit_code == 0
    assert result.files_created
    assert result.files_modified


def test_handle_stub_copy_no_backup_when_no_existing(fake_home, tmp_path, make_context):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _build_project(project_dir)
    ctx, _ = _ctx_with_container(make_context, path=project_dir, force=True)
    TemplatesCommand()._handle_stub_copy(ctx)
    # No pre-existing dest -> no backup marker.
    marker = fake_home / ".admt" / "backup-latest"
    assert not marker.exists()


# ----- execute dispatches to redo or --undo -----


def test_execute_undo_skips_super_redo(fake_home, make_context):
    ctx, container = _ctx_with_container(make_context)
    # No marker -> ArgumentError. Important: super().execute was never called.
    with pytest.raises(ArgumentError):
        TemplatesCommand(undo=True).execute(ctx)
    container.exec.assert_not_called()


def test_execute_forwards_redo_exit_code_on_failure(tmp_path, make_context):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    redo_failure = 7
    container = MagicMock(spec=ContainerService)
    container.exec.return_value = redo_failure
    # Mapper must cover project_dir so resolve_container_path succeeds; the
    # path_mapper's mapping isn't exercised for correctness here, just that
    # super().execute() runs through to container.exec.
    mapper = PathMapperService({project_dir: Path("/home/user/proj")})
    ctx = make_context(
        path_mapper=mapper,
        container_service=container,
        path=project_dir,
    )
    result = TemplatesCommand().execute(ctx)
    assert result.exit_code == redo_failure


# ----- metadata contract -----


def test_templates_command_metadata():
    assert TemplatesCommand.name == "templates"
    assert TemplatesCommand.help
    assert TemplatesCommand.redo_target == "templates"
    assert TemplatesCommand.requires_project is True
    assert TemplatesCommand.requires_container is True
