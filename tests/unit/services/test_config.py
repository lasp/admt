"""Tests for ConfigService -- registry, refresh, activation.

Compose metadata is resolved via ``docker compose config`` in production
(``adapters.docker.resolve_compose_config``). These tier-1 tests inject a
**fake resolver** that returns hand-built ``ResolvedCompose`` structs, so they
exercise ConfigService's own logic (service selection, container-name fallback,
activate-script derivation, mtime refresh, (de)serialization) without docker.
The marker tree (``default.do``, ``docker/*.yml``, ``env/activate``) is still
created on disk because ConfigService verifies and globs it.
"""

import os
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest

from admt.adapters.docker import ResolvedCompose, ResolvedService, ResolvedVolume
from admt.adapters.yaml_adapter import YamlAdapter
from admt.exceptions import ArgumentError, ConfigError
from admt.services.config import ConfigService, ProjectConfig
from admt.services.output import OutputService

# Placeholder compose content; never parsed (the resolver is faked) but a file
# must exist for marker verification and compose-file selection.
PLACEHOLDER_COMPOSE = "name: placeholder\nservices: {}\n"


# ----- resolved-compose builders -----


def _vol(source, target) -> ResolvedVolume:
    return ResolvedVolume(source=Path(source), target=Path(target))


def _service(name, *, container_name=None, mounts=()) -> ResolvedService:
    return ResolvedService(
        name=name,
        container_name=container_name,
        volumes=[_vol(s, t) for s, t in mounts],
    )


def _compose(project_name, services) -> ResolvedCompose:
    return ResolvedCompose(project_name=project_name, services={s.name: s for s in services})


def _default_resolver(compose_path: Path) -> ResolvedCompose:
    """Mimic ``docker compose config`` for the synthetic tree at ``compose_path``.

    A single service (named after the project dir) that bind-mounts the sibling
    ``adamant`` and the project root itself -- the shape register/refresh expect.
    """
    root = compose_path.parent.parent
    name = root.name
    return _compose(
        name,
        [
            _service(
                name,
                container_name=f"{name}_container",
                mounts=[
                    (root.parent / "adamant", "/home/user/adamant"),
                    (root, f"/home/user/{name}"),
                ],
            )
        ],
    )


def _fixed(resolved: ResolvedCompose):
    """A resolver that ignores the path and always returns ``resolved``."""
    return lambda _path: resolved


class _StubResolver:
    """Resolver whose return value can be swapped between calls (refresh tests)."""

    def __init__(self, result: ResolvedCompose) -> None:
        self.result = result

    def __call__(self, _path: Path) -> ResolvedCompose:
        return self.result


# ----- tree + service builders -----


def _make_project(
    base: Path,
    *,
    name: str = "myproj",
    extra_compose_files: tuple[str, ...] = (),
    markers: tuple[str, ...] = ("default.do", "docker-compose.yml", "env/activate"),
) -> Path:
    """Build a synthetic project marker tree under ``base`` and return its root."""
    root = base / name
    (root / "docker").mkdir(parents=True)
    (root / "env").mkdir(parents=True)
    if "default.do" in markers:
        (root / "default.do").touch()
    if "env/activate" in markers:
        (root / "env" / "activate").touch()
    if "docker-compose.yml" in markers:
        (root / "docker" / "docker-compose.yml").write_text(PLACEHOLDER_COMPOSE)
    for extra in extra_compose_files:
        (root / "docker" / extra).write_text(PLACEHOLDER_COMPOSE)
    (base / "adamant").mkdir(exist_ok=True)
    return root


def _svc(tmp_path, resolver=_default_resolver, *, noninteractive=False, yes=False):
    output = OutputService(verbose=False, quiet=False, yes=yes, noninteractive=noninteractive)
    return ConfigService(
        config_dir=tmp_path / ".admt",
        output=output,
        yaml_adapter=YamlAdapter(),
        compose_resolver=resolver,
    )


def _bump_mtime(path: Path) -> None:
    """Advance ``path``'s mtime by a few seconds so stat changes."""
    current = path.stat()
    os.utime(path, (current.st_atime + 10, current.st_mtime + 10))


# ----- load -----


def test_load_missing_file_returns_empty_config(tmp_path):
    config = _svc(tmp_path).load()
    assert config.active_project is None
    assert config.projects == {}


def test_load_empty_yaml_file_returns_empty_config(tmp_path):
    config_dir = tmp_path / ".admt"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text("")
    config = _svc(tmp_path).load()
    assert config.active_project is None
    assert config.projects == {}


# ----- register -----


def test_register_project_happy_path(tmp_path):
    root = _make_project(tmp_path)
    project = _svc(tmp_path).register_project(root)
    assert project.name == "myproj"
    assert project.service_name == "myproj"
    assert project.container_name == "myproj_container"
    assert project.activate_script == Path("/home/user/myproj/env/activate")
    assert project.container_home == Path("/home/user")
    assert (tmp_path / "adamant").resolve() in project.volume_mounts


def test_register_project_records_no_env_file_when_absent(tmp_path):
    root = _make_project(tmp_path)
    project = _svc(tmp_path).register_project(root)
    assert project.env_file is None
    assert project.env_file_mtime == 0


def test_register_project_records_env_file_when_present(tmp_path):
    root = _make_project(tmp_path)
    env_path = root / "docker" / ".env"
    env_path.write_text("COMPOSE_PROJECT_NAME=myproj\n")
    project = _svc(tmp_path).register_project(root)
    assert project.env_file == env_path
    assert project.env_file_mtime > 0


def test_register_project_resolved_name_differs_from_service(tmp_path):
    """Worktree case: resolved project name (from .env) != compose service name."""
    root = _make_project(tmp_path, name="myproj")
    rroot = root.resolve(strict=False)
    resolved = _compose(
        "myproj-wt1",
        [
            _service(
                "adamant_example",
                container_name="myproj-wt1_container",
                mounts=[
                    (tmp_path / "adamant", "/home/user/adamant"),
                    (rroot, "/home/user/myproj"),
                ],
            )
        ],
    )
    project = _svc(tmp_path, _fixed(resolved)).register_project(root)
    assert project.name == "myproj-wt1"
    assert project.service_name == "adamant_example"
    assert project.container_name == "myproj-wt1_container"


def test_register_project_sets_active(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    assert svc.load().active_project == "myproj"


def test_register_project_writes_config_file(tmp_path):
    root = _make_project(tmp_path)
    _svc(tmp_path).register_project(root)
    assert (tmp_path / ".admt" / "config.yml").exists()


def test_register_project_missing_default_do(tmp_path):
    root = _make_project(tmp_path, markers=("docker-compose.yml", "env/activate"))
    with pytest.raises(ArgumentError, match=r"default\.do"):
        _svc(tmp_path).register_project(root)


def test_register_project_missing_compose_file(tmp_path):
    root = _make_project(tmp_path, markers=("default.do", "env/activate"))
    with pytest.raises(ArgumentError, match="docker/"):
        _svc(tmp_path).register_project(root)


def test_register_project_missing_env_activate(tmp_path):
    root = _make_project(tmp_path, markers=("default.do", "docker-compose.yml"))
    with pytest.raises(ArgumentError, match="env/activate"):
        _svc(tmp_path).register_project(root)


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
    alt_base = tmp_path / "alt"
    alt_base.mkdir()
    second_root = _make_project(alt_base, name="myproj")
    with pytest.raises(ConfigError, match="already registered for a different path"):
        svc.register_project(second_root)


def test_register_project_force_overwrites(tmp_path):
    root = _make_project(tmp_path)
    rroot = root.resolve(strict=False)
    changed = _compose(
        "myproj",
        [
            _service(
                "myproj",
                container_name="myproj_new",
                mounts=[
                    (tmp_path / "adamant", "/home/user/adamant"),
                    (rroot, "/home/user/myproj"),
                ],
            )
        ],
    )
    resolver = _StubResolver(_default_resolver(root / "docker" / "docker-compose.yml"))
    svc = _svc(tmp_path, resolver)
    svc.register_project(root)
    resolver.result = changed
    project = svc.register_project(root, force=True)
    assert project.container_name == "myproj_new"


def test_is_project_registered_at_returns_name_when_registered(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    assert svc.is_project_registered_at(root) == "myproj"


def test_is_project_registered_at_returns_none_when_unregistered(tmp_path):
    assert _svc(tmp_path).is_project_registered_at(tmp_path / "ghost") is None


# ----- active project -----


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
    with pytest.raises(ArgumentError, match="No registered project"):
        _svc(tmp_path).set_active_project("ghost")


def test_set_active_project_already_active_is_noop(tmp_path):
    """Idempotent: setting the already-active project skips the save round-trip."""
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)  # registers myproj as active
    config_path = tmp_path / ".admt" / "config.yml"
    mtime_before = config_path.stat().st_mtime_ns
    svc.set_active_project("myproj")
    # No save -> mtime unchanged.
    assert config_path.stat().st_mtime_ns == mtime_before


def test_get_active_project_returns_active(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    assert svc.get_active_project().name == "myproj"


def test_get_active_project_honors_admt_env(tmp_path, monkeypatch):
    root_a = _make_project(tmp_path, name="proj_a")
    svc = _svc(tmp_path)
    svc.register_project(root_a)
    alt_base = tmp_path / "alt"
    alt_base.mkdir()
    svc.register_project(_make_project(alt_base, name="proj_b"))
    monkeypatch.setenv("ADMT_ENV", "proj_a")
    assert svc.get_active_project().name == "proj_a"


def test_get_active_project_no_config_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMT_ENV", raising=False)
    with pytest.raises(ConfigError, match="No project configured"):
        _svc(tmp_path).get_active_project()


def test_get_active_project_unknown_override_raises(tmp_path, monkeypatch):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    monkeypatch.setenv("ADMT_ENV", "ghost")
    with pytest.raises(ConfigError, match="not registered"):
        svc.get_active_project()


# ----- refresh -----


def test_check_and_refresh_noop_when_unchanged(tmp_path, capsys):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    capsys.readouterr()
    svc.check_and_refresh_project("myproj")
    assert capsys.readouterr().out == ""


def test_check_and_refresh_reports_service_name_change(tmp_path, capsys):
    root = _make_project(tmp_path, name="multi")
    rroot = root.resolve(strict=False)
    mounts = [(tmp_path / "adamant", "/home/user/adamant"), (rroot, "/home/user/multi")]
    resolver = _StubResolver(
        _compose("multi", [_service("multi", container_name="first_container", mounts=mounts)])
    )
    svc = _svc(tmp_path, resolver)
    svc.register_project(root)
    capsys.readouterr()
    resolver.result = _compose(
        "multi",
        [_service("renamed_svc", container_name="second_container", mounts=mounts)],
    )
    _bump_mtime(root / "docker" / "docker-compose.yml")
    svc.check_and_refresh_project("multi")
    out = capsys.readouterr().out
    assert "service_name multi -> renamed_svc" in out
    assert "container_name first_container -> second_container" in out


def test_check_and_refresh_reparses_when_compose_stale(tmp_path, capsys):
    root = _make_project(tmp_path)
    rroot = root.resolve(strict=False)
    resolver = _StubResolver(_default_resolver(root / "docker" / "docker-compose.yml"))
    svc = _svc(tmp_path, resolver)
    svc.register_project(root)
    capsys.readouterr()
    resolver.result = _compose(
        "myproj",
        [
            _service(
                "myproj",
                container_name="myproj_container",
                mounts=[
                    (tmp_path / "adamant", "/home/user/adamant"),
                    (rroot, "/home/user/myproj"),
                    (tmp_path / "new-repo", "/home/user/new-repo"),
                ],
            )
        ],
    )
    _bump_mtime(root / "docker" / "docker-compose.yml")
    svc.check_and_refresh_project("myproj")
    refreshed = svc.load().projects["myproj"]
    assert Path("/home/user/new-repo") in refreshed.volume_mounts.values()
    assert "added mount" in capsys.readouterr().out


def test_check_and_refresh_reparses_when_env_file_changes(tmp_path):
    """Editing the colocated .env re-derives even when the compose file is untouched."""
    root = _make_project(tmp_path)
    rroot = root.resolve(strict=False)
    env_path = root / "docker" / ".env"
    env_path.write_text("COMPOSE_PROJECT_NAME=myproj\n")
    resolver = _StubResolver(_default_resolver(root / "docker" / "docker-compose.yml"))
    svc = _svc(tmp_path, resolver)
    svc.register_project(root)
    # Compose file is NOT touched; only the .env changes.
    resolver.result = _compose(
        "myproj-wt9",
        [
            _service(
                "myproj",
                container_name="myproj-wt9_container",
                mounts=[
                    (tmp_path / "adamant", "/home/user/adamant"),
                    (rroot, "/home/user/myproj"),
                ],
            )
        ],
    )
    _bump_mtime(env_path)
    svc.check_and_refresh_project("myproj")
    assert svc.load().projects["myproj"].container_name == "myproj-wt9_container"


def test_check_and_refresh_detects_env_file_removed(tmp_path):
    """A .env present at registration but later removed counts as a change."""
    root = _make_project(tmp_path)
    env_path = root / "docker" / ".env"
    env_path.write_text("COMPOSE_PROJECT_NAME=myproj\n")
    resolver = _StubResolver(_default_resolver(root / "docker" / "docker-compose.yml"))
    svc = _svc(tmp_path, resolver)
    svc.register_project(root)
    assert svc.load().projects["myproj"].env_file is not None
    env_path.unlink()
    svc.check_and_refresh_project("myproj")
    assert svc.load().projects["myproj"].env_file is None


def test_check_and_refresh_missing_compose_raises(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    (root / "docker" / "docker-compose.yml").unlink()
    with pytest.raises(ConfigError, match="missing or unreadable"):
        svc.check_and_refresh_project("myproj")


def test_check_and_refresh_unknown_project_noop(tmp_path):
    _svc(tmp_path).check_and_refresh_project("nonexistent")


def test_list_projects_returns_all(tmp_path):
    root = _make_project(tmp_path)
    svc = _svc(tmp_path)
    svc.register_project(root)
    assert "myproj" in svc.list_projects()


# ----- service selection -----


def test_multi_service_picks_adamant_mount(tmp_path):
    root = _make_project(tmp_path, name="multi")
    rroot = root.resolve(strict=False)
    resolved = _compose(
        "multi",
        [
            _service("utility", mounts=[(tmp_path / "utility", "/home/user/utility")]),
            _service(
                "multi",
                container_name="multi_container",
                mounts=[
                    (tmp_path / "adamant", "/home/user/adamant"),
                    (rroot, "/home/user/multi"),
                ],
            ),
        ],
    )
    project = _svc(tmp_path, _fixed(resolved)).register_project(root)
    assert project.service_name == "multi"


def test_multi_service_no_adamant_mount_raises(tmp_path):
    root = _make_project(tmp_path, name="multi")
    resolved = _compose(
        "multi",
        [
            _service("a", mounts=[(tmp_path / "one", "/home/user/one")]),
            _service("b", mounts=[(tmp_path / "two", "/home/user/two")]),
        ],
    )
    with pytest.raises(ConfigError, match="none mount /adamant"):
        _svc(tmp_path, _fixed(resolved)).register_project(root)


def test_multi_service_all_adamant_mounts_raises(tmp_path):
    root = _make_project(tmp_path, name="multi")
    resolved = _compose(
        "multi",
        [
            _service("a", mounts=[(tmp_path / "adamant", "/home/user/adamant")]),
            _service("b", mounts=[(tmp_path / "adamant", "/home/user/adamant")]),
        ],
    )
    with pytest.raises(ConfigError, match="cannot disambiguate"):
        _svc(tmp_path, _fixed(resolved)).register_project(root)


def test_empty_services_raises(tmp_path):
    root = _make_project(tmp_path, name="empty")
    with pytest.raises(ConfigError, match="no services"):
        _svc(tmp_path, _fixed(_compose("empty", []))).register_project(root)


def test_derived_container_name_when_missing(tmp_path):
    """When the resolved service has no container_name, fall back to <service>_container."""
    root = _make_project(tmp_path)
    rroot = root.resolve(strict=False)
    resolved = _compose(
        "myproj",
        [
            _service(
                "myproj",
                container_name=None,
                mounts=[
                    (tmp_path / "adamant", "/home/user/adamant"),
                    (rroot, "/home/user/myproj"),
                ],
            )
        ],
    )
    project = _svc(tmp_path, _fixed(resolved)).register_project(root)
    assert project.container_name == "myproj_container"


def test_derive_activate_script_no_project_root_mount_raises(tmp_path):
    # Only ../../adamant is mounted; the project root itself is NOT.
    root = _make_project(tmp_path)
    resolved = _compose(
        "myproj",
        [_service("myproj", mounts=[(tmp_path / "adamant", "/home/user/adamant")])],
    )
    with pytest.raises(ConfigError, match="not covered by any volume mount"):
        _svc(tmp_path, _fixed(resolved)).register_project(root)


# ----- (de)serialization -----


def test_deserialize_corrupt_yaml_raises(tmp_path):
    config_dir = tmp_path / ".admt"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="not a YAML mapping"):
        _svc(tmp_path).load()


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
    with pytest.raises(ConfigError, match="missing key"):
        _svc(tmp_path).load()


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
    with pytest.raises(ConfigError, match="not a mapping"):
        _svc(tmp_path).load()


def test_round_trip_save_and_load_preserves_env_fields(tmp_path):
    root = _make_project(tmp_path)
    (root / "docker" / ".env").write_text("COMPOSE_PROJECT_NAME=myproj\n")
    svc = _svc(tmp_path)
    svc.register_project(root)
    reloaded = _svc(tmp_path).load().projects["myproj"]
    original = svc.load().projects["myproj"]
    assert isinstance(reloaded, ProjectConfig)
    assert original.volume_mounts == reloaded.volume_mounts
    assert original.activate_script == reloaded.activate_script
    assert reloaded.env_file == original.env_file
    assert reloaded.env_file_mtime == original.env_file_mtime
