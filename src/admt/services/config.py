"""Config service: project registry at ``~/.admt/config.yml``.

Owns the ``ProjectConfig``/``AdmtConfig`` dataclasses, the marker-verification
logic for project roots, compose-file parsing orchestration, and the
``compose_file_mtime``-based auto-refresh on every ``get_active_project``
call. Depends on ``YamlAdapter`` for file I/O and ``OutputService`` for the
multi-compose-file prompt and the refresh notice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from admt.exceptions import ArgumentError, ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable

    from admt.adapters.docker import ResolvedCompose, ResolvedService
    from admt.adapters.yaml_adapter import YamlAdapter
    from admt.services.output import OutputService


CONFIG_FILENAME = "config.yml"
CONFIG_SCHEMA_VERSION = 1


@dataclass
class ProjectConfig:
    """A single registered project and everything derived from its compose file."""

    name: str
    compose_file: Path
    compose_file_mtime: int
    env_file: Path | None
    env_file_mtime: int
    service_name: str
    container_name: str
    project_root: Path
    container_home: Path
    volume_mounts: dict[Path, Path]
    activate_script: Path


@dataclass
class AdmtConfig:
    """The full ``~/.admt/config.yml`` document in memory."""

    version: int = CONFIG_SCHEMA_VERSION
    active_project: str | None = None
    projects: dict[str, ProjectConfig] = field(default_factory=dict)


class ConfigService:
    """Manages ``~/.admt/config.yml`` -- load, save, register, refresh, activate."""

    def __init__(
        self,
        config_dir: Path,
        output: OutputService,
        yaml_adapter: YamlAdapter,
        compose_resolver: Callable[[Path], ResolvedCompose],
    ) -> None:
        """Bind the config directory and injected collaborators.

        ``compose_resolver`` derives resolved compose metadata (after ``.env``
        interpolation); in production it is ``adapters.docker.resolve_compose_config``.
        Injected so unit tests can supply hand-built resolved structs without docker.
        """
        self._config_dir = config_dir
        self._output = output
        self._yaml = yaml_adapter
        self._resolve_compose = compose_resolver

    @property
    def config_path(self) -> Path:
        """The absolute path to ``config.yml`` in the config directory."""
        return self._config_dir / CONFIG_FILENAME

    def load(self) -> AdmtConfig:
        """Load the config (empty ``AdmtConfig`` when the file does not exist)."""
        if not self.config_path.exists():
            return AdmtConfig()
        doc = self._yaml.load(self.config_path)
        return self._deserialize(doc)

    def save(self, config: AdmtConfig) -> None:
        """Write ``config`` to ``~/.admt/config.yml`` (creates the directory)."""
        self._yaml.dump(self._serialize(config), self.config_path)

    def list_projects(self) -> dict[str, ProjectConfig]:
        """Return a snapshot of every registered project."""
        return dict(self.load().projects)

    def is_project_registered_at(self, project_root: Path) -> str | None:
        """Return the registered name for ``project_root`` if any, else ``None``."""
        resolved = project_root.resolve(strict=False)
        for name, proj in self.load().projects.items():
            if proj.project_root.resolve(strict=False) == resolved:
                return name
        return None

    def register_project(self, project_root: Path, *, force: bool = False) -> ProjectConfig:
        """Register ``project_root`` as a project; overwrite when ``force``.

        Returns:
            The newly stored ``ProjectConfig``, or the pre-existing one if a
            duplicate registration was attempted without ``force``.

        Raises:
            ConfigError: When markers are missing, compose parsing fails, or
                the project name collides with a *different* project_root.
        """
        root = project_root.resolve(strict=False)
        self._verify_markers(root)
        compose_path = self._select_compose_file(root)
        project = self._build_project(root, compose_path)
        config = self.load()
        existing = config.projects.get(project.name)
        if existing and not force:
            if existing.project_root.resolve(strict=False) != root:
                msg = (
                    f"Project name '{project.name}' is already registered for a "
                    f"different path ({existing.project_root}). Use --force to replace."
                )
                raise ConfigError(msg)
            return existing
        config.projects[project.name] = project
        config.active_project = project.name
        self.save(config)
        return project

    def set_active_project(self, name: str) -> None:
        """Set ``name`` as the active project; raises if not registered.

        Raises ``ArgumentError`` (exit 3) -- the user passed a bad project
        name, which is an argument-shape failure, not an environment one.

        Idempotent: if ``name`` is already the active project, no save is
        performed (avoids the round-trip to disk for a no-op).
        """
        config = self.load()
        if name not in config.projects:
            available = sorted(config.projects)
            msg = f"No registered project named '{name}'. Available: {available}"
            raise ArgumentError(msg)
        if config.active_project == name:
            return
        config.active_project = name
        self.save(config)

    def get_active_project(self) -> ProjectConfig:
        """Return the active project, honoring ``ADMT_ENV``; refreshes first."""
        override = os.environ.get("ADMT_ENV")
        config = self.load()
        chosen = override if override else config.active_project
        if not chosen:
            msg = "No project configured. Run 'admt env init' to set up a project."
            raise ConfigError(msg)
        if chosen not in config.projects:
            msg = (
                f"Project '{chosen}' is not registered. "
                f"Run 'admt env init' or 'admt env use' first."
            )
            raise ConfigError(msg)
        self.check_and_refresh_project(chosen)
        return self.load().projects[chosen]

    def check_and_refresh_project(self, name: str) -> None:
        """Re-parse the compose file and update the project when mtime changed."""
        config = self.load()
        proj = config.projects.get(name)
        if proj is None:
            return
        if not proj.compose_file.exists():
            msg = f"Compose file '{proj.compose_file}' is missing or unreadable."
            raise ConfigError(msg)
        current_compose_mtime = int(proj.compose_file.stat().st_mtime)
        # Track the colocated .env too: editing it (project name, ports) changes
        # the resolved config even when the compose file is untouched. A .env
        # added or removed since registration also reads as a change (0 sentinel).
        _, current_env_mtime = self._resolve_env_file(proj.compose_file)
        if (
            current_compose_mtime == proj.compose_file_mtime
            and current_env_mtime == proj.env_file_mtime
        ):
            return
        updated = self._build_project(proj.project_root, proj.compose_file)
        for line in self._diff_projects(proj, updated):
            self._output.info(line)
        config.projects[name] = updated
        self.save(config)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_markers(project_root: Path) -> None:
        missing: list[str] = []
        if not (project_root / "default.do").exists():
            missing.append("default.do")
        docker_dir = project_root / "docker"
        compose_found = docker_dir.is_dir() and (
            any(docker_dir.glob("*.yml")) or any(docker_dir.glob("*.yaml"))
        )
        if not compose_found:
            missing.append("docker/*.yml or docker/*.yaml")
        if not (project_root / "env" / "activate").exists():
            missing.append("env/activate")
        if missing:
            formatted = "\n".join(f"  - {m}" for m in missing)
            msg = (
                f"Not a recognized Adamant project root: {project_root}\n"
                f"Missing markers:\n{formatted}\n"
                f"Run 'admt env init' from a directory with default.do, "
                f"docker/*.yml, and env/activate."
            )
            # ArgumentError (exit 3) -- the path the user passed is the wrong
            # shape (no project at that location). Distinct from ConfigError
            # (exit 2), which is for environment/registry failures.
            raise ArgumentError(msg)

    def _select_compose_file(self, project_root: Path) -> Path:
        docker_dir = project_root / "docker"
        candidates = sorted(list(docker_dir.glob("*.yml")) + list(docker_dir.glob("*.yaml")))
        if len(candidates) == 1:
            return candidates[0]
        names = [p.name for p in candidates]
        chosen = self._output.choose(
            f"Multiple compose files found in {docker_dir}:",
            names,
        )
        return docker_dir / chosen

    def _build_project(self, project_root: Path, compose_path: Path) -> ProjectConfig:
        resolved = self._resolve_compose(compose_path)
        service_name = self._resolve_service_name(resolved.services)
        service = resolved.services[service_name]
        # ``docker compose config`` always emits a resolved top-level name.
        project_name = resolved.project_name
        container_name = service.container_name or f"{service_name}_container"
        volume_mounts = {vol.source: vol.target for vol in service.volumes}
        activate_script = self._derive_activate_script(project_root, volume_mounts)
        # activate_script is ``<container_home>/<project>/env/activate``; walking
        # up three parents lands on ``<container_home>``. Path saturates at ``/``
        # when the mount is pathologically short, so no fallback branch is needed.
        container_home = activate_script.parent.parent.parent
        env_file, env_mtime = self._resolve_env_file(compose_path)
        return ProjectConfig(
            name=project_name,
            compose_file=compose_path,
            compose_file_mtime=int(compose_path.stat().st_mtime),
            env_file=env_file,
            env_file_mtime=env_mtime,
            service_name=service_name,
            container_name=container_name,
            project_root=project_root,
            container_home=container_home,
            volume_mounts=volume_mounts,
            activate_script=activate_script,
        )

    @staticmethod
    def _resolve_env_file(compose_path: Path) -> tuple[Path | None, int]:
        """Return the colocated ``.env`` and its mtime (``None``/``0`` if absent).

        docker compose auto-loads ``.env`` from the compose file's directory;
        admt tracks its mtime so an edit to it (project name, ports) triggers a
        re-derive even when the compose file itself is untouched.
        """
        env_path = compose_path.parent / ".env"
        if env_path.exists():
            return env_path, int(env_path.stat().st_mtime)
        return None, 0

    @staticmethod
    def _resolve_service_name(services: dict[str, ResolvedService]) -> str:
        if not services:
            msg = "Compose file defines no services."
            raise ConfigError(msg)
        if len(services) == 1:
            return next(iter(services))
        candidates = [
            name
            for name, svc in services.items()
            if any(vol.target.name == "adamant" for vol in svc.volumes)
        ]
        if len(candidates) == 1:
            return candidates[0]
        available = sorted(services)
        if not candidates:
            msg = f"Multiple services defined but none mount /adamant. Services: {available}"
            raise ConfigError(msg)
        msg = f"Multiple services mount /adamant (cannot disambiguate): {sorted(candidates)}"
        raise ConfigError(msg)

    @staticmethod
    def _derive_activate_script(project_root: Path, volume_mounts: dict[Path, Path]) -> Path:
        resolved = project_root.resolve(strict=False)
        ordered = sorted(volume_mounts.items(), key=lambda kv: len(str(kv[0])), reverse=True)
        for host, container in ordered:
            try:
                rel = resolved.relative_to(host)
            except ValueError:
                continue
            return container / rel / "env" / "activate"
        msg = (
            f"Project root '{project_root}' is not covered by any volume mount; "
            f"cannot derive the container-side activate script."
        )
        raise ConfigError(msg)

    @staticmethod
    def _diff_projects(old: ProjectConfig, new: ProjectConfig) -> list[str]:
        messages: list[str] = []
        if old.service_name != new.service_name:
            messages.append(
                f"Updated config: service_name {old.service_name} -> {new.service_name}"
            )
        if old.container_name != new.container_name:
            messages.append(
                f"Updated config: container_name {old.container_name} -> {new.container_name}"
            )
        added = set(new.volume_mounts) - set(old.volume_mounts)
        removed = set(old.volume_mounts) - set(new.volume_mounts)
        messages.extend(
            f"Updated config: added mount {host} -> {new.volume_mounts[host]}"
            for host in sorted(added, key=str)
        )
        messages.extend(
            f"Updated config: removed mount {host} -> {old.volume_mounts[host]}"
            for host in sorted(removed, key=str)
        )
        return messages

    @staticmethod
    def _serialize(config: AdmtConfig) -> dict[str, Any]:
        projects_out: dict[str, dict[str, Any]] = {}
        for name, proj in config.projects.items():
            projects_out[name] = {
                "compose_file": str(proj.compose_file),
                "compose_file_mtime": proj.compose_file_mtime,
                "env_file": str(proj.env_file) if proj.env_file else None,
                "env_file_mtime": proj.env_file_mtime,
                "service_name": proj.service_name,
                "container_name": proj.container_name,
                "project_root": str(proj.project_root),
                "container_home": str(proj.container_home),
                "volume_mounts": {
                    str(host): str(container) for host, container in proj.volume_mounts.items()
                },
                "activate_script": str(proj.activate_script),
            }
        return {
            "version": config.version,
            "active_project": config.active_project,
            "projects": projects_out,
        }

    def _deserialize(self, doc: object) -> AdmtConfig:
        if doc is None:
            return AdmtConfig()
        if not isinstance(doc, dict):
            msg = f"Corrupt config at {self.config_path}: not a YAML mapping."
            raise ConfigError(msg)
        projects_raw = doc.get("projects") or {}
        projects: dict[str, ProjectConfig] = {
            name: self._deserialize_project(name, body) for name, body in projects_raw.items()
        }
        return AdmtConfig(
            version=int(doc.get("version", CONFIG_SCHEMA_VERSION)),
            active_project=doc.get("active_project"),
            projects=projects,
        )

    @staticmethod
    def _deserialize_project(name: str, body: object) -> ProjectConfig:
        if not isinstance(body, dict):
            msg = f"Corrupt project entry '{name}': not a mapping."
            raise ConfigError(msg)
        try:
            return ProjectConfig(
                name=name,
                compose_file=Path(body["compose_file"]),
                compose_file_mtime=int(body["compose_file_mtime"]),
                env_file=Path(body["env_file"]) if body.get("env_file") else None,
                env_file_mtime=int(body["env_file_mtime"]),
                service_name=body["service_name"],
                container_name=body["container_name"],
                project_root=Path(body["project_root"]),
                container_home=Path(body["container_home"]),
                volume_mounts={Path(h): Path(c) for h, c in body["volume_mounts"].items()},
                activate_script=Path(body["activate_script"]),
            )
        except KeyError as exc:
            msg = f"Corrupt project entry '{name}': missing key {exc.args[0]!r}"
            raise ConfigError(msg) from exc
