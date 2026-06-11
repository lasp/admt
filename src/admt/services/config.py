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
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from admt.exceptions import ArgumentError, ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable

    from admt.adapters.docker import ResolvedCompose, ResolvedService
    from admt.adapters.yaml_adapter import YamlAdapter
    from admt.services.output import OutputService


CONFIG_FILENAME = "config.yml"
SESSIONS_FILENAME = "sessions.yml"
CONFIG_SCHEMA_VERSION = 1

# File descriptors probed (in order) for the controlling terminal. stdin
# first, but a piped/redirected stdin (``echo y | admt env rm``) must not
# lose the terminal identity -- stderr and stdout usually still point at the
# terminal, so fall back to them. Only when all three are redirected (CI,
# full pipelines, agents) is the session layer disabled.
_TTY_FDS = (0, 2, 1)


class ActiveSource(StrEnum):
    """Which mechanism selected the active project (reported by ``env status``)."""

    ENV_OVERRIDE = "ADMT_ENV"
    SESSION = "this terminal"
    GLOBAL = "global default"


def _current_terminal() -> tuple[str, int] | None:
    """Return ``(tty_path, session_id)`` for the controlling terminal, else ``None``.

    ``None`` when there is no controlling tty (piped/redirected stdin, CI,
    ``ADMT_NONINTERACTIVE`` agents) or on non-POSIX platforms lacking
    ``os.ttyname``/``os.getsid`` (e.g. Windows) -- the per-terminal session
    layer is simply skipped there.
    """
    tty_name = getattr(os, "ttyname", None)
    get_sid = getattr(os, "getsid", None)
    if tty_name is None or get_sid is None:
        return None
    for fd in _TTY_FDS:
        try:
            if os.isatty(fd):
                return tty_name(fd), get_sid(0)
        except OSError:
            continue
    return None


def _session_alive(entry: object) -> bool:
    """True if a session entry's owning shell (its ``sid``) is still running.

    Used to prune entries for closed terminals. A live PID we may not signal
    (``PermissionError``) still counts as alive; only a confirmed-dead PID
    (``ProcessLookupError``) is pruned. Malformed entries are dropped --
    including ``sid <= 0``, which would otherwise be immortal (``kill(0, ...)``
    signals the caller's own process group and ``kill(-1, ...)`` broadcasts,
    so both always "succeed").
    """
    if not isinstance(entry, dict) or not isinstance(entry.get("sid"), int):
        return False
    if entry["sid"] <= 0:
        return False
    try:
        os.kill(entry["sid"], 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


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
        # Registration activates the project ("Active project: <name>" is
        # printed), so this terminal must be pinned to it too -- otherwise a
        # previously-pinned terminal would keep resolving to its old pin and
        # the activation message would lie.
        self._write_session(project.name)
        return project

    def set_active_project(self, name: str) -> None:
        """Set ``name`` active: the per-terminal session entry AND the global default.

        Writing the session entry switches the *current* terminal immediately
        (resolution step 2); updating ``active_project`` makes *new* terminals
        default to it (step 3).

        The session entry is written UNCONDITIONALLY -- even when ``name`` is
        already the global default. This terminal may be pinned to a different
        project (or unpinned), so `env use <current-global>` must still repin
        it; skipping the session write here would silently ignore the user's
        explicit switch. Only the redundant global save is skipped when the
        global already matches (idempotency: no config.yml round-trip).

        Raises ``ArgumentError`` (exit 3) for an unregistered ``name`` -- an
        argument-shape failure, not an environment one.
        """
        config = self.load()
        if name not in config.projects:
            available = sorted(config.projects)
            msg = f"No registered project named '{name}'. Available: {available}"
            raise ArgumentError(msg)
        self._write_session(name)
        if config.active_project == name:
            return
        config.active_project = name
        self.save(config)

    def get_active_project(self) -> ProjectConfig:
        """Return the active project (see resolution order); refreshes first."""
        config = self.load()
        chosen, _source = self._resolve_active_name(config)
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

    def get_active_source(self) -> ActiveSource:
        """Report which mechanism selects the active project (for ``env status``)."""
        return self._resolve_active_name(self.load())[1]

    def resolved_active_name(self) -> str | None:
        """Return the active project name THIS terminal resolves to, or ``None``.

        Same precedence as ``get_active_project`` (ADMT_ENV -> session -> global)
        but non-raising: an unconfigured or unregistered name comes back as-is
        (or ``None``) instead of erroring. Used by ``env list`` so its ``*``
        marks the per-terminal active project.

        Resolution auto-pins (see ``_resolve_active_name``): once any command
        has told the user which project this terminal is on -- including a bare
        ``env list`` -- the terminal must STAY on it until told otherwise.
        """
        return self._resolve_active_name(self.load())[0]

    def _resolve_active_name(self, config: AdmtConfig) -> tuple[str | None, ActiveSource]:
        """Resolve the active project name by precedence; pin on first resolve.

        ``ADMT_ENV`` (explicit) -> this terminal's session entry -> the global
        ``active_project``. The session entry is consulted only when it names a
        currently-registered project for the live terminal, so a stale entry
        (tty recycled, project removed) transparently falls through to global.

        **Lazy auto-pin:** when resolution falls through to the global default,
        the result is immediately pinned to this terminal. Without this, an
        unpinned terminal keeps following the global, so a later ``env use`` in
        ANOTHER terminal (which moves the global) would silently change what
        this terminal targets -- even after this terminal already displayed its
        active project. Every resolution path pins (``env list``, ``env
        status``, passthrough commands alike): showing the user a project is a
        commitment. The pin is the no-shim equivalent of exporting ``ADMT_ENV``
        at shell startup. No-ops without a controlling terminal (CI/pipes),
        which correctly keep following the global. ``ADMT_ENV`` resolutions are
        never persisted -- explicit per-invocation overrides stay ephemeral.
        """
        override = os.environ.get("ADMT_ENV")
        if override:
            return override, ActiveSource.ENV_OVERRIDE
        session_name = self._session_project(config)
        if session_name:
            return session_name, ActiveSource.SESSION
        if config.active_project and config.active_project in config.projects:
            self._write_session(config.active_project)
        return config.active_project, ActiveSource.GLOBAL

    # ------------------------------------------------------------------
    # Per-terminal session store (~/.admt/sessions.yml)
    # ------------------------------------------------------------------

    @property
    def _sessions_path(self) -> Path:
        return self._config_dir / SESSIONS_FILENAME

    def _load_sessions(self) -> dict[str, dict[str, Any]]:
        """Return the ``tty -> {project, sid}`` map (empty when absent/corrupt).

        The session store is a disposable cache that admt itself writes; an
        unreadable or unparseable file must degrade to "no sessions" -- it can
        never be allowed to break every command in every terminal. The next
        ``_write_session`` rewrites it wholesale.
        """
        path = self._sessions_path
        if not path.exists():
            return {}
        try:
            doc = self._yaml.load(path)
        except ConfigError:
            return {}
        if not isinstance(doc, dict):
            return {}
        sessions = doc.get("sessions")
        return sessions if isinstance(sessions, dict) else {}

    def _write_session(self, name: str) -> None:
        """Pin ``name`` to the current terminal; no-op when there is no tty.

        Drops entries for terminals that have since closed (dead ``sid``) so the
        store does not accumulate stale pins over time.
        """
        term = _current_terminal()
        if term is None:
            return
        tty, sid = term
        sessions = {
            other: entry
            for other, entry in self._load_sessions().items()
            if other == tty or _session_alive(entry)
        }
        sessions[tty] = {"project": name, "sid": sid}
        # The session store is a best-effort cache and pinning happens on every
        # resolution, so a write failure (unwritable ~/.admt, disk full) must
        # not break the command -- but it must not be silent either: without
        # the pin, this terminal keeps following the global default.
        try:
            self._yaml.dump({"sessions": sessions}, self._sessions_path)
        except ConfigError as exc:
            self._output.warning(
                f"Could not save the terminal's active-project pin ({exc}); "
                f"this terminal will follow the global default."
            )

    def _session_project(self, config: AdmtConfig) -> str | None:
        """Return this terminal's pinned project, or ``None``.

        ``None`` when there is no tty, no entry, the entry is stale (its stored
        sid no longer matches the live session -- the tty was recycled), or it
        names a project that is no longer registered.
        """
        term = _current_terminal()
        if term is None:
            return None
        tty, sid = term
        entry = self._load_sessions().get(tty)
        if not isinstance(entry, dict) or entry.get("sid") != sid:
            return None
        name = entry.get("project")
        if isinstance(name, str) and name in config.projects:
            return name
        return None

    def check_and_refresh_project(self, name: str) -> None:
        """Re-parse the compose file and update the project when mtime changed."""
        config = self.load()
        proj = config.projects.get(name)
        if proj is None:
            return
        if not proj.compose_file.exists():
            msg = f"Compose file '{proj.compose_file}' is missing or unreadable."
            raise ConfigError(msg)
        current_compose_mtime = proj.compose_file.stat().st_mtime_ns
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
            compose_file_mtime=compose_path.stat().st_mtime_ns,
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
        re-derive even when the compose file itself is untouched. Nanosecond
        mtimes (``st_mtime_ns``) so edits within the same second cannot slip
        past the staleness check.
        """
        env_path = compose_path.parent / ".env"
        if env_path.exists():
            return env_path, env_path.stat().st_mtime_ns
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
        if old.name != new.name:
            # A .env edit can rename the resolved compose project. The registry
            # key (and any session pins) intentionally keep the original name --
            # re-keying would orphan them -- but the rename must be visible.
            messages.append(
                f"Updated config: resolved project name {old.name} -> {new.name} "
                f"(still registered as '{old.name}')"
            )
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
                # env_file/env_file_mtime are optional keys: configs may lack
                # them, and a load failure here would break every command. The
                # defaults are safe -- a present .env is picked up on refresh.
                env_file=Path(body["env_file"]) if body.get("env_file") else None,
                env_file_mtime=int(body.get("env_file_mtime", 0)),
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
