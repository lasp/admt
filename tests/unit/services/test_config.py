"""Tests for ConfigService -- registry, refresh, activation."""

import os
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest

from admt.adapters.yaml_adapter import YamlAdapter
from admt.exceptions import ArgumentError, ConfigError
from admt.services.config import ConfigService, ProjectConfig
from admt.services.output import OutputService

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


def _make_project(
    base: Path,
    *,
    name: str = "myproj",
    compose_content: str | None = None,
    extra_compose_files: tuple[str, ...] = (),
    markers: tuple[str, ...] = ("default.do", "docker-compose.yml", "env/activate"),
):
    """Build a synthetic project tree under ``base`` and return its root path.

    When ``compose_content`` is not provided, DEFAULT_COMPOSE is rewritten to
    swap every ``myproj`` token with ``name`` so the project self-references
    line up with the directory layout.
    """
    root = base / name
    (root / "docker").mkdir(parents=True)
    (root / "env").mkdir(parents=True)
    if "default.do" in markers:
        (root / "default.do").touch()
    if "env/activate" in markers:
        (root / "env" / "activate").touch()
    content = (
        compose_content if compose_content is not None else DEFAULT_COMPOSE.replace("myproj", name)
    )
    if "docker-compose.yml" in markers:
        (root / "docker" / "docker-compose.yml").write_text(content)
    for extra in extra_compose_files:
        (root / "docker" / extra).write_text(content)
    # Create the sibling directories that the compose file references.
    (base / "adamant").mkdir(exist_ok=True)
    return root


def _svc(tmp_path, *, noninteractive: bool = False, yes: bool = False):
    output = OutputService(verbose=False, quiet=False, yes=yes, noninteractive=noninteractive)
    return ConfigService(
        config_dir=tmp_path / ".admt",
        output=output,
        yaml_adapter=YamlAdapter(),
    )


def test_load_missing_file_returns_empty_config(tmp_path):
    svc = _svc(tmp_path)
    config = svc.load()
    assert config.active_project is None
    assert config.projects == {}


def test_load_empty_yaml_file_returns_empty_config(tmp_path):
    config_dir = tmp_path / ".admt"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text("")
    svc = _svc(tmp_path)
    config = svc.load()
    assert config.active_project is None
    assert config.projects == {}


def test_register_project_happy_path(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    project = svc.register_project(root)
    assert project.name == "myproj"
    assert project.service_name == "myproj"
    assert project.container_name == "myproj_container"
    assert project.activate_script == Path("/home/user/myproj/env/activate")
    assert project.container_home == Path("/home/user")
    # Compose mount -> absolute host path
    assert (tmp_path / "adamant").resolve() in project.volume_mounts


def test_register_project_sets_active(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    assert svc.load().active_project == "myproj"


def test_register_project_writes_config_file(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    assert (tmp_path / ".admt" / "config.yml").exists()


def test_register_project_missing_default_do(tmp_path):
    root = _make_project(tmp_path, markers=("docker-compose.yml", "env/activate"))
    svc = _svc(tmp_path)
    with pytest.raises(ArgumentError, match=r"default\.do"):
        svc.register_project(root)


def test_register_project_missing_compose_file(tmp_path):
    root = _make_project(tmp_path, markers=("default.do", "env/activate"))
    svc = _svc(tmp_path)
    with pytest.raises(ArgumentError, match="docker/"):
        svc.register_project(root)


def test_register_project_missing_env_activate(tmp_path):
    root = _make_project(tmp_path, markers=("default.do", "docker-compose.yml"))
    svc = _svc(tmp_path)
    with pytest.raises(ArgumentError, match="env/activate"):
        svc.register_project(root)


def test_register_project_multiple_compose_files_prompts(tmp_path):
    root = _make_project(tmp_path, extra_compose_files=("alternate.yaml",))
    svc = _svc(tmp_path)
    with patch("builtins.input", side_effect=["2"]):
        project = svc.register_project(root)
    # Sorted names: alternate.yaml, docker-compose.yml. Choice 2 -> docker-compose.yml.
    assert project.compose_file.name == "docker-compose.yml"


def test_register_project_multiple_compose_files_noninteractive_raises(tmp_path):
    root = _make_project(tmp_path, extra_compose_files=("alternate.yaml",))
    svc = _svc(tmp_path, noninteractive=True)
    with pytest.raises(ArgumentError, match="NONINTERACTIVE"):
        svc.register_project(root)


def test_register_project_duplicate_same_path_returns_existing(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    first = svc.register_project(root)
    second = svc.register_project(root)
    assert first.compose_file == second.compose_file


def test_register_project_name_collision_different_path_raises(tmp_path):
    first_root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(first_root)
    # Build a second project with the same name under a different base.
    alt_base = tmp_path / "alt"
    alt_base.mkdir()
    second_root = _make_project(alt_base, name="myproj")
    with pytest.raises(ConfigError, match="already registered for a different path"):
        svc.register_project(second_root)


def test_register_project_force_overwrites(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    # Rewrite compose file to change container_name.
    new_compose = DEFAULT_COMPOSE.replace("myproj_container", "myproj_new")
    (root / "docker" / "docker-compose.yml").write_text(new_compose)
    project = svc.register_project(root, force=True)
    assert project.container_name == "myproj_new"


def test_is_project_registered_at_returns_name_when_registered(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    assert svc.is_project_registered_at(root) == "myproj"


def test_is_project_registered_at_returns_none_when_unregistered(tmp_path):
    svc = _svc(tmp_path)
    assert svc.is_project_registered_at(tmp_path / "ghost") is None


def test_set_active_project_happy_path(tmp_path):
    root_a = _make_project(tmp_path, name="proj_a")
    svc = _svc(tmp_path)
    svc.register_project(root_a)
    alt_base = tmp_path / "alt"
    alt_base.mkdir()
    _make_project(alt_base, name="proj_b")
    svc.register_project(alt_base / "proj_b")
    svc.set_active_project("proj_a")
    assert svc.load().active_project == "proj_a"


def test_set_active_project_unknown_name_raises(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ArgumentError, match="No registered project"):
        svc.set_active_project("ghost")


def test_get_active_project_returns_active(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    active = svc.get_active_project()
    assert active.name == "myproj"


def test_get_active_project_honors_admt_env(tmp_path, monkeypatch):
    root_a = _make_project(tmp_path, name="proj_a")
    svc = _svc(tmp_path)
    svc.register_project(root_a)
    # Register a second project and make it active, then override via env var.
    alt_base = tmp_path / "alt"
    alt_base.mkdir()
    root_b = _make_project(alt_base, name="proj_b")
    svc.register_project(root_b)
    # active_project is now proj_b (most recent register).
    monkeypatch.setenv("ADMT_ENV", "proj_a")
    assert svc.get_active_project().name == "proj_a"


def test_get_active_project_no_config_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMT_ENV", raising=False)
    svc = _svc(tmp_path)
    with pytest.raises(ConfigError, match="No project configured"):
        svc.get_active_project()


def test_get_active_project_unknown_override_raises(tmp_path, monkeypatch):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    monkeypatch.setenv("ADMT_ENV", "ghost")
    with pytest.raises(ConfigError, match="not registered"):
        svc.get_active_project()


def test_check_and_refresh_noop_when_unchanged(tmp_path, capsys):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    capsys.readouterr()  # flush registration output
    svc.check_and_refresh_project("myproj")
    captured = capsys.readouterr()
    assert captured.out == ""


def test_check_and_refresh_reports_service_name_change(tmp_path, capsys):
    """A refresh that flips service_name/container_name emits two diff lines."""
    root = _make_project(tmp_path, name="multi")
    # Initial compose: only ``multi`` service, mounts /adamant.
    compose_path = root / "docker" / "docker-compose.yml"
    compose_path.write_text(
        dedent(
            """\
            name: multi
            services:
              multi:
                container_name: first_container
                volumes:
                  - type: bind
                    source: ../../adamant
                    target: /home/user/adamant
                  - type: bind
                    source: ../../multi
                    target: /home/user/multi
            """
        )
    )
    svc = _svc(tmp_path)
    svc.register_project(root)
    capsys.readouterr()
    # Refresh: swap the service name AND rename the container.
    compose_path.write_text(
        dedent(
            """\
            name: multi
            services:
              renamed_svc:
                container_name: second_container
                volumes:
                  - type: bind
                    source: ../../adamant
                    target: /home/user/adamant
                  - type: bind
                    source: ../../multi
                    target: /home/user/multi
            """
        )
    )
    _bump_mtime(compose_path)
    svc.check_and_refresh_project("multi")
    captured = capsys.readouterr()
    assert "service_name multi -> renamed_svc" in captured.out
    assert "container_name first_container -> second_container" in captured.out


def test_check_and_refresh_reparses_when_stale(tmp_path, capsys):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    capsys.readouterr()  # flush registration output
    # Rewrite the compose file to add a new volume mount.
    updated = dedent(
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
              - type: bind
                source: ../../new-repo
                target: /home/user/new-repo
        """
    )
    compose_file = root / "docker" / "docker-compose.yml"
    compose_file.write_text(updated)
    (tmp_path / "new-repo").mkdir(exist_ok=True)
    _bump_mtime(compose_file)
    svc.check_and_refresh_project("myproj")
    refreshed = svc.load().projects["myproj"]
    assert Path("/home/user/new-repo") in refreshed.volume_mounts.values()
    captured = capsys.readouterr()
    assert "added mount" in captured.out


def test_check_and_refresh_missing_compose_raises(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    (root / "docker" / "docker-compose.yml").unlink()
    with pytest.raises(ConfigError, match="missing or unreadable"):
        svc.check_and_refresh_project("myproj")


def test_check_and_refresh_unknown_project_noop(tmp_path):
    svc = _svc(tmp_path)
    # No projects registered -- should not raise.
    svc.check_and_refresh_project("nonexistent")


def test_list_projects_returns_all(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    projects = svc.list_projects()
    assert "myproj" in projects


def test_multi_service_picks_adamant_mount(tmp_path):
    content = dedent(
        """\
        name: multi
        services:
          utility:
            volumes:
              - ../../utility:/home/user/utility
          multi:
            volumes:
              - ../../adamant:/home/user/adamant
              - ../../multi:/home/user/multi
        """
    )
    root = _make_project(tmp_path, name="multi", compose_content=content)
    svc = _svc(tmp_path)
    project = svc.register_project(root)
    assert project.service_name == "multi"


def test_multi_service_no_adamant_mount_raises(tmp_path):
    content = dedent(
        """\
        name: multi
        services:
          a:
            volumes:
              - ../../one:/home/user/one
          b:
            volumes:
              - ../../two:/home/user/two
        """
    )
    root = _make_project(tmp_path, name="multi", compose_content=content)
    svc = _svc(tmp_path)
    with pytest.raises(ConfigError, match="none mount /adamant"):
        svc.register_project(root)


def test_multi_service_all_adamant_mounts_raises(tmp_path):
    content = dedent(
        """\
        name: multi
        services:
          a:
            volumes:
              - ../../adamant:/home/user/adamant
          b:
            volumes:
              - ../../adamant:/home/user/adamant
        """
    )
    root = _make_project(tmp_path, name="multi", compose_content=content)
    svc = _svc(tmp_path)
    with pytest.raises(ConfigError, match="cannot disambiguate"):
        svc.register_project(root)


def test_empty_services_raises(tmp_path):
    content = dedent(
        """\
        name: empty
        services: {}
        """
    )
    root = _make_project(tmp_path, name="empty", compose_content=content)
    svc = _svc(tmp_path)
    with pytest.raises(ConfigError, match="no services"):
        svc.register_project(root)


def test_derived_container_name_when_missing(tmp_path):
    content = DEFAULT_COMPOSE.replace("    container_name: myproj_container\n", "")
    root = _make_project(tmp_path, compose_content=content)
    svc = _svc(tmp_path)
    project = svc.register_project(root)
    assert project.container_name == "myproj_container"


def test_project_name_falls_back_to_service_when_missing(tmp_path):
    content = DEFAULT_COMPOSE.replace("name: myproj\n", "")
    root = _make_project(tmp_path, compose_content=content)
    svc = _svc(tmp_path)
    project = svc.register_project(root)
    assert project.name == "myproj"


def test_derive_activate_script_no_project_root_mount_raises(tmp_path):
    # Compose only mounts ../../adamant; project_root itself is NOT mounted.
    content = dedent(
        """\
        name: myproj
        services:
          myproj:
            volumes:
              - ../../adamant:/home/user/adamant
        """
    )
    root = _make_project(tmp_path, compose_content=content)
    svc = _svc(tmp_path)
    with pytest.raises(ConfigError, match="not covered by any volume mount"):
        svc.register_project(root)


def test_deserialize_corrupt_yaml_raises(tmp_path):
    config_dir = tmp_path / ".admt"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text("- just\n- a\n- list\n")
    svc = _svc(tmp_path)
    with pytest.raises(ConfigError, match="not a YAML mapping"):
        svc.load()


def test_deserialize_project_missing_key_raises(tmp_path):
    config_dir = tmp_path / ".admt"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        dedent(
            """\
        version: 1
        active_project: x
        projects:
          x:
            compose_file: /tmp/nope.yml
        """
        )
    )
    svc = _svc(tmp_path)
    with pytest.raises(ConfigError, match="missing key"):
        svc.load()


def test_deserialize_non_mapping_project_entry_raises(tmp_path):
    config_dir = tmp_path / ".admt"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        dedent(
            """\
        version: 1
        projects:
          x: "not a mapping"
        """
        )
    )
    svc = _svc(tmp_path)
    with pytest.raises(ConfigError, match="not a mapping"):
        svc.load()


def test_round_trip_save_and_load(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    # Fresh service instance should see the same project.
    fresh = _svc(tmp_path)
    loaded = fresh.load()
    assert "myproj" in loaded.projects
    original = svc.load().projects["myproj"]
    reloaded = loaded.projects["myproj"]
    assert isinstance(reloaded, ProjectConfig)
    assert original.volume_mounts == reloaded.volume_mounts
    assert original.activate_script == reloaded.activate_script


def _bump_mtime(path: Path):
    """Advance ``path``'s mtime by a few seconds so stat changes."""
    current = path.stat()
    os.utime(path, (current.st_atime + 10, current.st_mtime + 10))
