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
from admt.services import config as config_mod
from admt.services.config import (
    ActiveSource,
    ConfigService,
    ProjectConfig,
    _current_session_key,
    _current_terminal,
    _entry_alive,
    _session_alive,
)
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


def _registered(tmp_path, *, name="myproj", base=None):
    """Register a single project and return its ConfigService."""
    root = _make_project(base if base is not None else tmp_path, name=name)
    svc = _svc(tmp_path)
    svc.register_project(root)
    return svc


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


def test_check_and_refresh_reports_resolved_name_change(tmp_path, capsys):
    """A .env-driven rename is reported; the registry key intentionally stays."""
    root = _make_project(tmp_path)
    rroot = root.resolve(strict=False)
    resolver = _StubResolver(_default_resolver(root / "docker" / "docker-compose.yml"))
    svc = _svc(tmp_path, resolver)
    svc.register_project(root)
    capsys.readouterr()
    resolver.result = _compose(
        "myproj-renamed",
        [
            _service(
                "myproj",
                container_name="myproj_container",
                mounts=[
                    (tmp_path / "adamant", "/home/user/adamant"),
                    (rroot, "/home/user/myproj"),
                ],
            )
        ],
    )
    _bump_mtime(root / "docker" / "docker-compose.yml")
    svc.check_and_refresh_project("myproj")
    out = capsys.readouterr().out
    assert "resolved project name myproj -> myproj-renamed" in out
    assert "still registered as 'myproj'" in out
    # Registry key unchanged; pins keep working.
    assert "myproj" in svc.list_projects()


def test_check_and_refresh_detects_same_second_env_edit(tmp_path):
    """An edit within the same second as the last derive is still detected.

    Staleness compares st_mtime_ns; a 1ns bump must trigger a re-derive.
    """
    root = _make_project(tmp_path)
    rroot = root.resolve(strict=False)
    env_path = root / "docker" / ".env"
    env_path.write_text("COMPOSE_PROJECT_NAME=myproj\n")
    resolver = _StubResolver(_default_resolver(root / "docker" / "docker-compose.yml"))
    svc = _svc(tmp_path, resolver)
    svc.register_project(root)
    resolver.result = _compose(
        "myproj",
        [
            _service(
                "myproj",
                container_name="myproj-ns_container",
                mounts=[
                    (tmp_path / "adamant", "/home/user/adamant"),
                    (rroot, "/home/user/myproj"),
                ],
            )
        ],
    )
    stat = env_path.stat()
    os.utime(env_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))  # same second
    svc.check_and_refresh_project("myproj")
    assert svc.load().projects["myproj"].container_name == "myproj-ns_container"


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


def test_unparseable_config_yaml_raises_clean_config_error(tmp_path):
    """Malformed config.yml surfaces as ConfigError (exit 2), not a ruamel traceback."""
    config_dir = tmp_path / ".admt"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text("{[not yaml")
    with pytest.raises(ConfigError, match="Cannot parse YAML"):
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


def test_deserialize_config_missing_env_fields(tmp_path):
    """A config entry lacking env_file/env_file_mtime loads with safe defaults.

    These keys are optional; a load failure here would break every command
    for a user whose config does not carry them.
    """
    config_dir = tmp_path / ".admt"
    config_dir.mkdir()
    (config_dir / "config.yml").write_text(
        dedent(
            """\
            version: 1
            active_project: legacy
            projects:
              legacy:
                compose_file: /sim/legacy/docker/docker-compose.yml
                compose_file_mtime: 100
                service_name: legacy
                container_name: legacy_container
                project_root: /sim/legacy
                container_home: /home/user
                volume_mounts:
                  /sim/legacy: /home/user/legacy
                activate_script: /home/user/legacy/env/activate
            """
        )
    )
    project = _svc(tmp_path).load().projects["legacy"]
    assert project.env_file is None
    assert project.env_file_mtime == 0
    assert project.container_name == "legacy_container"


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


# ----- _current_terminal -----


def test_current_terminal_returns_tty_and_sid(monkeypatch):
    monkeypatch.setattr(os, "isatty", lambda _fd: True)
    monkeypatch.setattr(os, "ttyname", lambda _fd: "/dev/ttys009")
    monkeypatch.setattr(os, "getsid", lambda _pid: 4242)
    assert _current_terminal() == ("/dev/ttys009", 4242)


def test_current_terminal_not_a_tty_returns_none(monkeypatch):
    monkeypatch.setattr(os, "isatty", lambda _fd: False)
    assert _current_terminal() is None


def test_current_terminal_oserror_returns_none(monkeypatch):
    def _raise(_fd):
        raise OSError

    monkeypatch.setattr(os, "isatty", lambda _fd: True)
    monkeypatch.setattr(os, "ttyname", _raise)
    assert _current_terminal() is None


def test_current_terminal_non_posix_returns_none(monkeypatch):
    # Platforms without os.getsid (e.g. Windows) disable the session layer.
    monkeypatch.delattr(os, "getsid", raising=False)
    assert _current_terminal() is None


_STDERR_FD = 2
_STDOUT_FD = 1


def test_current_terminal_falls_back_to_stderr_when_stdin_piped(monkeypatch):
    """`echo y | admt ...` must not lose the terminal: stderr is still the tty."""
    monkeypatch.setattr(os, "isatty", lambda fd: fd == _STDERR_FD)
    monkeypatch.setattr(os, "ttyname", lambda fd: f"/dev/ttys{fd}")
    monkeypatch.setattr(os, "getsid", lambda _pid: 5)
    assert _current_terminal() == ("/dev/ttys2", 5)


def test_current_terminal_falls_back_to_stdout_last(monkeypatch):
    monkeypatch.setattr(os, "isatty", lambda fd: fd == _STDOUT_FD)
    monkeypatch.setattr(os, "ttyname", lambda fd: f"/dev/ttys{fd}")
    monkeypatch.setattr(os, "getsid", lambda _pid: 5)
    assert _current_terminal() == ("/dev/ttys1", 5)


# ----- session-based active project resolution -----


def _two_projects(tmp_path):
    """Register proja then projb (projb becomes the global default)."""
    svc = _registered(tmp_path, name="proja")
    alt = tmp_path / "b"
    alt.mkdir()
    svc.register_project(_make_project(alt, name="projb"))
    return svc


def test_session_overrides_global(tmp_path, monkeypatch):
    svc = _two_projects(tmp_path)
    monkeypatch.delenv("ADMT_ENV", raising=False)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysA", 7))
    svc._write_session("proja")
    assert svc.load().active_project == "projb"  # global unchanged
    assert svc.get_active_source() == ActiveSource.SESSION
    assert svc.get_active_project().name == "proja"


def test_admt_env_overrides_session(tmp_path, monkeypatch):
    svc = _two_projects(tmp_path)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysA", 7))
    svc._write_session("proja")
    monkeypatch.setenv("ADMT_ENV", "projb")
    assert svc.get_active_source() == ActiveSource.ENV_OVERRIDE
    assert svc.get_active_project().name == "projb"


def test_global_source_when_no_session_and_no_env(tmp_path, monkeypatch):
    svc = _registered(tmp_path)
    monkeypatch.delenv("ADMT_ENV", raising=False)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: None)
    assert svc.get_active_source() == ActiveSource.GLOBAL


def test_resolved_active_name_reflects_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMT_ENV", raising=False)
    svc = _registered(tmp_path)  # global = myproj
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: None)
    assert svc.resolved_active_name() == "myproj"
    # ADMT_ENV wins; resolved_active_name is raw (no registration validation).
    monkeypatch.setenv("ADMT_ENV", "ghost")
    assert svc.resolved_active_name() == "ghost"


def test_resolved_active_name_none_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMT_ENV", raising=False)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: None)
    assert _svc(tmp_path).resolved_active_name() is None


def test_resolved_active_name_pins_terminal_on_global_resolution(tmp_path, monkeypatch):
    """Any resolution pins -- including the env-list path (resolved_active_name)."""
    monkeypatch.delenv("ADMT_ENV", raising=False)
    svc = _registered(tmp_path)  # global = myproj
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysL", 31))
    assert svc.resolved_active_name() == "myproj"
    assert svc._load_sessions().get("/dev/ttysL", {}).get("project") == "myproj"


def test_resolved_active_name_does_not_pin_admt_env_override(tmp_path, monkeypatch):
    """ADMT_ENV resolutions are ephemeral -- never persisted as a pin."""
    svc = _registered(tmp_path)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysL", 31))
    monkeypatch.setenv("ADMT_ENV", "ghost")
    assert svc.resolved_active_name() == "ghost"
    assert not (tmp_path / ".admt" / "sessions.yml").exists()


def test_resolved_active_name_does_not_pin_unregistered_global(tmp_path, monkeypatch):
    """A dangling global (project since removed) is not pinned."""
    monkeypatch.delenv("ADMT_ENV", raising=False)
    svc = _registered(tmp_path)
    cfg = svc.load()
    cfg.active_project = "ghost"  # global points at an unregistered name
    svc.save(cfg)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysL", 31))
    assert svc.resolved_active_name() == "ghost"
    assert not (tmp_path / ".admt" / "sessions.yml").exists()


def test_env_list_only_terminal_does_not_follow_later_env_use(tmp_path, monkeypatch):
    """Regression (user-reported): a terminal that only ever ran `env list` must
    not follow a later `env use` from another terminal.

    Terminal A: env list -> sees proja (global), gets pinned by that resolution.
    Terminal B: env use projb -> moves the global.
    Terminal A: env list -> must STILL show proja.
    """
    monkeypatch.delenv("ADMT_ENV", raising=False)
    svc = _registered(tmp_path, name="proja")  # global = proja
    # Live PID for both sids: terminal B's prune-on-write checks the OTHER
    # entry's sid liveness; a made-up dead PID would (correctly) get pruned
    # and mask the regression this test guards.
    live_sid = os.getpid()
    term_a = ("/dev/ttysA", live_sid)
    term_b = ("/dev/ttysB", live_sid)
    # Terminal A runs `env list` (resolution only -- no get_active_project).
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: term_a)
    assert svc.resolved_active_name() == "proja"
    # Terminal B registers projb and switches to it (moves the global).
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: term_b)
    alt = tmp_path / "b"
    alt.mkdir()
    svc.register_project(_make_project(alt, name="projb"))
    svc.set_active_project("projb")
    assert svc.load().active_project == "projb"
    # Terminal A lists again: still proja.
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: term_a)
    assert svc.resolved_active_name() == "proja"


def test_set_active_writes_session_when_terminal_present(tmp_path, monkeypatch):
    svc = _two_projects(tmp_path)
    monkeypatch.delenv("ADMT_ENV", raising=False)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysC", 11))
    svc.set_active_project("proja")
    assert (tmp_path / ".admt" / "sessions.yml").exists()
    assert svc.get_active_source() == ActiveSource.SESSION
    assert svc.get_active_project().name == "proja"


def test_set_active_no_terminal_skips_session(tmp_path, monkeypatch):
    svc = _two_projects(tmp_path)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: None)
    svc.set_active_project("proja")
    assert not (tmp_path / ".admt" / "sessions.yml").exists()
    assert svc.load().active_project == "proja"  # global still updated


def test_set_active_already_global_still_pins_this_terminal(tmp_path, monkeypatch):
    """Regression: `env use <current-global>` must repin a differently-pinned terminal.

    The global-save idempotency short-circuit must not skip the session write:
    this terminal may be pinned elsewhere, and the user's explicit `env use` of
    the (already-global) name has to take effect HERE.
    """
    svc = _two_projects(tmp_path)  # global = projb (last registered)
    monkeypatch.delenv("ADMT_ENV", raising=False)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysB", 21))
    svc._write_session("proja")  # this terminal pinned to proja
    assert svc.get_active_project().name == "proja"
    svc.set_active_project("projb")  # projb is ALREADY the global default
    assert svc.get_active_project().name == "projb"  # terminal repinned
    assert svc.load().active_project == "projb"


def test_session_ignored_when_no_terminal(tmp_path, monkeypatch):
    svc = _registered(tmp_path)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: None)
    assert svc._session_project(svc.load()) is None


def test_session_stale_sid_falls_through_to_global(tmp_path, monkeypatch):
    svc = _registered(tmp_path)
    monkeypatch.delenv("ADMT_ENV", raising=False)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysX", 1))
    svc._write_session("myproj")
    # Same tty device, different session id: the terminal was recycled.
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysX", 999))
    assert svc._session_project(svc.load()) is None
    assert svc.get_active_source() == ActiveSource.GLOBAL


def test_session_no_entry_for_this_tty(tmp_path, monkeypatch):
    svc = _registered(tmp_path)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysX", 1))
    svc._write_session("myproj")
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysY", 2))
    assert svc._session_project(svc.load()) is None


def test_session_unregistered_project_ignored(tmp_path, monkeypatch):
    svc = _registered(tmp_path)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysX", 1))
    svc._write_session("ghost")  # not a registered project
    assert svc._session_project(svc.load()) is None


def test_session_entry_not_a_dict_ignored(tmp_path, monkeypatch):
    svc = _registered(tmp_path)
    (tmp_path / ".admt" / "sessions.yml").write_text("sessions:\n  /dev/ttysZ: corrupt\n")
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysZ", 1))
    assert svc._session_project(svc.load()) is None


def test_load_sessions_corrupt_non_dict_returns_empty(tmp_path):
    svc = _svc(tmp_path)
    (tmp_path / ".admt").mkdir()
    (tmp_path / ".admt" / "sessions.yml").write_text("- a\n- b\n")
    assert svc._load_sessions() == {}


def test_load_sessions_missing_sessions_key_returns_empty(tmp_path):
    svc = _svc(tmp_path)
    (tmp_path / ".admt").mkdir()
    (tmp_path / ".admt" / "sessions.yml").write_text("other: 1\n")
    assert svc._load_sessions() == {}


# ----- session-store pruning (closed terminals) -----


def test_session_alive_true_for_live_pid(monkeypatch):
    monkeypatch.setattr(os, "kill", lambda _pid, _sig: None)
    assert _session_alive({"sid": 123}) is True


def test_session_alive_false_for_dead_pid(monkeypatch):
    def _dead(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", _dead)
    assert _session_alive({"sid": 123}) is False


def test_session_alive_keeps_pid_on_permission_error(monkeypatch):
    def _perm(_pid, _sig):
        raise PermissionError

    monkeypatch.setattr(os, "kill", _perm)
    assert _session_alive({"sid": 123}) is True


def test_session_alive_false_for_malformed_entry():
    assert _session_alive("not-a-dict") is False
    assert _session_alive({"sid": "not-an-int"}) is False


def test_session_alive_false_for_non_positive_sid():
    """sid<=0 would be immortal: kill(0,..) hits our own pgroup, kill(-1,..) broadcasts."""
    assert _session_alive({"sid": 0}) is False
    assert _session_alive({"sid": -1}) is False


def test_load_sessions_unparseable_yaml_returns_empty(tmp_path):
    """A corrupt session cache must degrade to empty, never break commands."""
    svc = _svc(tmp_path)
    (tmp_path / ".admt").mkdir()
    (tmp_path / ".admt" / "sessions.yml").write_text("{[not yaml")
    assert svc._load_sessions() == {}


def test_register_project_pins_registering_terminal(tmp_path, monkeypatch):
    """Regression: `env init` activates the new project, so the terminal that ran
    it must be repinned -- a stale pin would contradict 'Active project: <new>'.
    """
    monkeypatch.delenv("ADMT_ENV", raising=False)
    live_sid = os.getpid()
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysI", live_sid))
    svc = _registered(tmp_path, name="proja")
    svc.set_active_project("proja")  # terminal pinned to proja
    alt = tmp_path / "b"
    alt.mkdir()
    svc.register_project(_make_project(alt, name="projb"))  # env init projb
    assert svc.resolved_active_name() == "projb"  # terminal follows the init


def test_write_session_failure_warns_and_does_not_raise(tmp_path, monkeypatch, capsys):
    """An unwritable session store must not break the command (review: PR #27).

    The pin write runs on every resolution in an unpinned session, so a raw
    write error here would brick every command. Degrade to a warning; the
    session then follows the global default.
    """
    monkeypatch.delenv("ADMT_ENV", raising=False)
    svc = _registered(tmp_path)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysW", os.getpid()))
    msg = "Cannot write YAML file /sim/sessions.yml: disk full"
    monkeypatch.setattr(svc._yaml, "dump", _raise_config_error(msg))
    svc.set_active_project("myproj")  # must not raise
    captured = capsys.readouterr()
    assert "Could not save the session's active-project pin" in captured.err
    assert "follow the global default" in captured.err
    # Resolution still works via the global default (the documented fallback).
    assert svc.get_active_project().name == "myproj"


def _raise_config_error(msg):
    def _raise(*_args, **_kwargs):
        raise ConfigError(msg)

    return _raise


# ----- ADMT_SESSION_KEY sessions (tty-less callers) -----


@pytest.fixture
def _headless(monkeypatch):
    """No controlling terminal and no ADMT_ENV -- the agent/CI shape."""
    monkeypatch.delenv("ADMT_ENV", raising=False)
    monkeypatch.delenv("ADMT_SESSION_KEY", raising=False)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: None)


def _keyed_entry(svc, key):
    return svc._load_sessions().get(f"session:{key}", {})


@pytest.mark.usefixtures("_headless")
def test_current_session_key_none_without_env():
    assert _current_session_key() is None


@pytest.mark.usefixtures("_headless")
def test_current_session_key_prefixes_env_value(monkeypatch):
    monkeypatch.setenv("ADMT_SESSION_KEY", "agent-7")
    assert _current_session_key() == "session:agent-7"


@pytest.mark.usefixtures("_headless")
def test_current_session_key_none_when_empty(monkeypatch):
    monkeypatch.setenv("ADMT_SESSION_KEY", "")
    assert _current_session_key() is None


def test_current_session_key_none_when_terminal_present(monkeypatch):
    """A controlling tty outranks the variable -- interactive shells stay tty-keyed."""
    monkeypatch.setenv("ADMT_SESSION_KEY", "agent-7")
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysK", 11))
    assert _current_session_key() is None


@pytest.mark.usefixtures("_headless")
def test_keyed_session_pin_overrides_global(tmp_path, monkeypatch):
    svc = _two_projects(tmp_path)  # global = projb
    monkeypatch.setenv("ADMT_SESSION_KEY", "agent-7")
    svc.set_active_project("proja")
    assert svc.load().active_project == "proja"
    assert _keyed_entry(svc, "agent-7")["project"] == "proja"
    # Another session moves the global; this session must not follow it.
    svc.save(_with_global(svc, "projb"))
    assert svc.get_active_project().name == "proja"
    assert svc.get_active_source() == ActiveSource.KEY_SESSION


@pytest.mark.usefixtures("_headless")
def test_admt_env_overrides_keyed_session(tmp_path, monkeypatch):
    svc = _two_projects(tmp_path)
    monkeypatch.setenv("ADMT_SESSION_KEY", "agent-7")
    svc.set_active_project("proja")
    monkeypatch.setenv("ADMT_ENV", "projb")
    assert svc.get_active_source() == ActiveSource.ENV_OVERRIDE
    assert svc.get_active_project().name == "projb"


def test_terminal_pin_wins_over_keyed_entry(tmp_path, monkeypatch):
    """With a tty present, a keyed entry for the same store is never consulted."""
    monkeypatch.delenv("ADMT_ENV", raising=False)
    svc = _two_projects(tmp_path)
    monkeypatch.setenv("ADMT_SESSION_KEY", "agent-7")
    # Seed a keyed pin from a headless context...
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: None)
    svc.set_active_project("proja")
    # ...then resolve from a terminal that pins elsewhere.
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysK", os.getpid()))
    svc.set_active_project("projb")
    assert svc.get_active_source() == ActiveSource.SESSION
    assert svc.get_active_project().name == "projb"


@pytest.mark.usefixtures("_headless")
def test_keyed_auto_pin_on_global_resolution(tmp_path, monkeypatch):
    """Parity with terminals: showing the project commits the session to it."""
    svc = _registered(tmp_path)  # global = myproj
    monkeypatch.setenv("ADMT_SESSION_KEY", "agent-7")
    assert svc.resolved_active_name() == "myproj"
    assert _keyed_entry(svc, "agent-7")["project"] == "myproj"


@pytest.mark.usefixtures("_headless")
def test_keyed_entry_idle_past_window_falls_back_to_global(tmp_path, monkeypatch):
    svc = _two_projects(tmp_path)
    monkeypatch.setenv("ADMT_SESSION_KEY", "agent-7")
    svc.set_active_project("proja")
    svc.save(_with_global(svc, "projb"))  # another session moves the global
    # Jump past the idle window: the pin is no longer honored.
    _advance_clock(monkeypatch, config_mod._KEYED_MAX_IDLE_SECS + 1)
    assert svc.get_active_source() == ActiveSource.GLOBAL
    assert svc.get_active_project().name == "projb"


@pytest.mark.usefixtures("_headless")
def test_expired_keyed_entry_is_pruned_and_repinned(tmp_path, monkeypatch):
    svc = _registered(tmp_path)
    monkeypatch.setenv("ADMT_SESSION_KEY", "agent-7")
    svc.set_active_project("myproj")
    _advance_clock(monkeypatch, config_mod._KEYED_MAX_IDLE_SECS + 1)
    # The fall-through auto-pin rewrites the entry with a fresh stamp.
    assert svc.resolved_active_name() == "myproj"
    assert _keyed_entry(svc, "agent-7")["written"] == int(config_mod.time.time())


@pytest.mark.usefixtures("_headless")
def test_resolving_keyed_pin_refreshes_its_stamp(tmp_path, monkeypatch):
    """Touch-on-read: an actively-resolving session never ages out."""
    svc = _registered(tmp_path)
    monkeypatch.setenv("ADMT_SESSION_KEY", "agent-7")
    svc.set_active_project("myproj")
    first = _keyed_entry(svc, "agent-7")["written"]
    _advance_clock(monkeypatch, config_mod._KEYED_TOUCH_INTERVAL_SECS + 1)
    assert svc.resolved_active_name() == "myproj"
    assert _keyed_entry(svc, "agent-7")["written"] > first


@pytest.mark.usefixtures("_headless")
def test_keyed_pin_stamp_not_rewritten_within_throttle(tmp_path, monkeypatch):
    """The refresh is throttled so concurrent sessions don't rewrite on every command."""
    svc = _registered(tmp_path)
    monkeypatch.setenv("ADMT_SESSION_KEY", "agent-7")
    svc.set_active_project("myproj")
    first = _keyed_entry(svc, "agent-7")["written"]
    _advance_clock(monkeypatch, config_mod._KEYED_TOUCH_INTERVAL_SECS - 10)
    assert svc.resolved_active_name() == "myproj"
    assert _keyed_entry(svc, "agent-7")["written"] == first


@pytest.mark.usefixtures("_headless")
def test_keyed_entry_for_unregistered_project_falls_through(tmp_path, monkeypatch):
    """A pin naming a since-removed project is ignored, not honored."""
    svc = _two_projects(tmp_path)
    monkeypatch.setenv("ADMT_SESSION_KEY", "agent-7")
    svc.set_active_project("proja")
    cfg = svc.load()
    del cfg.projects["proja"]
    cfg.active_project = "projb"
    svc.save(cfg)
    assert svc.get_active_source() == ActiveSource.GLOBAL
    assert svc.get_active_project().name == "projb"


@pytest.mark.usefixtures("_headless")
def test_headless_without_session_key_follows_global(tmp_path, monkeypatch):
    """Regression: a key-less headless caller behaves exactly as before."""
    svc = _two_projects(tmp_path)  # global = projb
    svc.set_active_project("proja")
    assert not (tmp_path / ".admt" / "sessions.yml").exists()
    svc.save(_with_global(svc, "projb"))
    assert svc.get_active_source() == ActiveSource.GLOBAL
    assert svc.get_active_project().name == "projb"


@pytest.mark.usefixtures("_headless")
def test_write_prunes_expired_keyed_entries_of_other_sessions(tmp_path, monkeypatch):
    svc = _registered(tmp_path)
    monkeypatch.setenv("ADMT_SESSION_KEY", "stale-agent")
    svc.set_active_project("myproj")
    _advance_clock(monkeypatch, config_mod._KEYED_MAX_IDLE_SECS + 1)
    monkeypatch.setenv("ADMT_SESSION_KEY", "fresh-agent")
    svc.set_active_project("myproj")
    sessions = svc._load_sessions()
    assert "session:stale-agent" not in sessions
    assert "session:fresh-agent" in sessions


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("not-a-dict", False),
        ({"project": "p"}, False),  # neither sid nor written
        ({"project": "p", "written": "soon"}, False),  # malformed stamp
        ({"project": "p", "written": 0}, False),  # epoch: long idle
    ],
)
def test_entry_alive_rejects_malformed_entries(entry, expected):
    assert _entry_alive(entry) is expected


def test_entry_alive_accepts_fresh_keyed_entry():
    assert _entry_alive({"project": "p", "written": int(config_mod.time.time())}) is True


def test_entry_alive_delegates_sid_entries_to_session_alive():
    assert _entry_alive({"project": "p", "sid": os.getpid()}) is True
    assert _entry_alive({"project": "p", "sid": -1}) is False


def _with_global(svc, name):
    """Return the stored config with ``active_project`` moved to ``name``."""
    cfg = svc.load()
    cfg.active_project = name
    return cfg


def _advance_clock(monkeypatch, seconds):
    """Shift ``config.time.time`` forward by ``seconds`` (no real sleeping)."""
    base = config_mod.time.time()
    monkeypatch.setattr(config_mod.time, "time", lambda: base + seconds)


def test_write_session_prunes_closed_terminals(tmp_path, monkeypatch):
    svc = _registered(tmp_path)
    (tmp_path / ".admt" / "sessions.yml").write_text(
        dedent(
            """\
            sessions:
              /dev/ttysNEW:
                project: myproj
                sid: 100
              /dev/ttysLIVE:
                project: myproj
                sid: 111
              /dev/ttysDEAD:
                project: myproj
                sid: 999999
            """
        )
    )

    def _kill(pid, _sig):
        if pid == 999999:  # noqa: PLR2004 -- the seeded dead PID
            raise ProcessLookupError

    monkeypatch.setattr(os, "kill", _kill)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysNEW", 222))
    svc._write_session("myproj")
    sessions = svc._load_sessions()
    assert "/dev/ttysDEAD" not in sessions  # dead sid pruned
    assert "/dev/ttysLIVE" in sessions  # live sid kept
    assert sessions["/dev/ttysNEW"]["sid"] == 222  # current terminal rewritten  # noqa: PLR2004


# ----- lazy auto-pin (a terminal locks in on first resolve) -----


def test_get_active_project_autopins_terminal_to_global(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMT_ENV", raising=False)
    svc = _registered(tmp_path)  # global = myproj
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysA", 5))
    assert svc.get_active_project().name == "myproj"
    # First resolve wrote a session pin for this terminal.
    assert svc._load_sessions().get("/dev/ttysA", {}).get("project") == "myproj"


def test_get_active_project_no_autopin_without_terminal(tmp_path, monkeypatch):
    monkeypatch.delenv("ADMT_ENV", raising=False)
    svc = _registered(tmp_path)
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: None)
    svc.get_active_project()
    assert not (tmp_path / ".admt" / "sessions.yml").exists()


def test_global_change_does_not_move_already_resolved_terminal(tmp_path, monkeypatch):
    """Regression: a global move from ANOTHER terminal must not change this one.

    A terminal that has resolved the active project once is pinned, so a later
    global move (another terminal's `env init` + `env use`) leaves it untouched.
    """
    monkeypatch.delenv("ADMT_ENV", raising=False)
    live_sid = os.getpid()  # dead sids would get pruned by the other terminal's write
    svc = _registered(tmp_path, name="proja")  # global = proja
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysA", live_sid))
    assert svc.get_active_project().name == "proja"  # this terminal auto-pins to proja
    # ANOTHER terminal registers projb (which activates it, moving the global).
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysOther", live_sid))
    alt = tmp_path / "b"
    alt.mkdir()
    svc.register_project(_make_project(alt, name="projb"))
    assert svc.load().active_project == "projb"
    # Back in this terminal: unaffected.
    monkeypatch.setattr(config_mod, "_current_terminal", lambda: ("/dev/ttysA", live_sid))
    assert svc.get_active_project().name == "proja"
    assert svc.get_active_source() == ActiveSource.SESSION
