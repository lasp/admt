"""Docker Compose adapter -- wraps ``docker compose`` (and legacy ``docker-compose``).

All ``subprocess`` calls go through here. Services and commands must not
invoke ``subprocess`` directly. This module exposes a small surface of
primitives matching the container lifecycle admt needs, plus the two I/O
modes the codebase uses:

- **Streaming**: stdin/stdout/stderr inherited, output flows straight to the
  user's terminal. No timeout (the call is user-bounded).
- **Captured**: ``capture_output=True`` for programmatic inspection of
  short commands (status probes, env snapshot generation). Times out.

The module also owns a tiny process registry so the SIGINT handler in
``main.py`` can report the PIDs of any in-flight ``docker compose`` calls
when the user hits Ctrl-C.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from admt.exceptions import ConfigError, ContainerError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import TextIO

    # Popen[Any] widens across byte-mode (``_spawn_tracked``) and text-mode
    # (``_run_streaming_transform``, which sets ``text=True``); the registry
    # only needs ``.pid`` and ``.poll()`` which are stream-type-agnostic.
    _Register = Callable[[subprocess.Popen[Any]], None]
    LineTransform = Callable[[str], str | None]


# Bounded calls -- status probes, env snapshot capture, short informational
# commands. Long-running streaming calls DO NOT use this timeout (see the
# comments on streaming methods for why).
_BOUNDED_TIMEOUT_SECS = 60


# Tracks active ``docker compose`` subprocesses so ``main.py``'s SIGINT
# handler can list their PIDs on Ctrl-C. Appended/removed by streaming
# methods; treat as module-private state.
_active_processes: list[subprocess.Popen[Any]] = []


def iter_active_pids() -> list[int]:
    """Return a snapshot of PIDs for in-flight ``docker compose`` subprocesses."""
    return [proc.pid for proc in _active_processes if proc.poll() is None]


@dataclass
class CommandResult:
    """Outcome of a single subprocess invocation."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


def _detect_compose_command() -> list[str]:
    """Return the argv prefix for docker compose (modern first, legacy fallback)."""
    if shutil.which("docker"):
        # Prefer ``docker compose`` (modern v2 plugin) over ``docker-compose``.
        return ["docker", "compose"]
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    msg = "Neither 'docker' nor 'docker-compose' is on PATH. Install Docker to continue."
    raise ContainerError(msg)


# ----------------------------------------------------------------------
# Resolved compose metadata (via ``docker compose config``).
#
# admt never reads a compose file as raw YAML to derive project metadata: a
# compose file may interpolate variables from a colocated ``.env`` (e.g.
# ``name: ${COMPOSE_PROJECT_NAME:-adamant_example}`` and parameterized host ports),
# and raw YAML would yield the literal ``${...}`` strings. Because the hot path
# targets the container directly by ``container_name``, an unresolved name
# breaks every exec. ``docker compose config`` loads ``.env``, resolves
# interpolation, and emits absolute volume sources -- the only correct source
# of truth. See ARCHITECTURE.md "Compose Parsing".
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedVolume:
    """A bind mount as resolved by ``docker compose config`` (absolute source)."""

    source: Path
    target: Path


@dataclass(frozen=True)
class ResolvedService:
    """A single service from resolved compose config."""

    name: str
    container_name: str | None
    volumes: list[ResolvedVolume]


@dataclass(frozen=True)
class ResolvedCompose:
    """Fully-resolved compose metadata, after ``.env`` interpolation."""

    project_name: str
    services: dict[str, ResolvedService]


def resolve_compose_config(
    compose_file: Path, *, compose_cmd: list[str] | None = None
) -> ResolvedCompose:
    """Resolve compose metadata via ``docker compose -f <file> config --format json``.

    Loads the colocated ``.env``, expands ``${VAR}`` interpolation, and emits
    absolute volume sources -- so a parameterized
    ``container_name: ${COMPOSE_PROJECT_NAME:-x}_container`` resolves to its real
    value. Runs only at registration/refresh, never on the per-command hot path.

    Args:
        compose_file: Absolute path to the compose file.
        compose_cmd: Override the detected compose argv prefix (for tests).

    Returns:
        The resolved project name and services.

    Raises:
        ConfigError: If ``docker compose config`` fails, times out, or returns
            output admt cannot parse.
        ContainerError: If neither ``docker`` nor ``docker-compose`` is on PATH.
    """
    prefix = compose_cmd if compose_cmd is not None else _detect_compose_command()
    cmd = [*prefix, "-f", str(compose_file), "config", "--format", "json"]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=_BOUNDED_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired as exc:
        msg = (
            f"'docker compose config' timed out after {_BOUNDED_TIMEOUT_SECS}s for {compose_file}."
        )
        raise ConfigError(msg) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "unknown error"
        msg = f"'docker compose config' failed for {compose_file}: {detail}"
        raise ConfigError(msg)
    return _parse_resolved_config(completed.stdout, compose_file)


def _parse_resolved_config(raw_json: str, compose_file: Path) -> ResolvedCompose:
    try:
        doc = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        msg = f"'docker compose config' returned invalid JSON for {compose_file}: {exc}"
        raise ConfigError(msg) from exc
    name = doc.get("name") if isinstance(doc, dict) else None
    services_raw = doc.get("services") if isinstance(doc, dict) else None
    if not isinstance(name, str) or not isinstance(services_raw, dict):
        msg = f"'docker compose config' for {compose_file} is missing 'name' or 'services'."
        raise ConfigError(msg)
    services = {
        svc_name: _parse_resolved_service(svc_name, body) for svc_name, body in services_raw.items()
    }
    return ResolvedCompose(project_name=name, services=services)


def _parse_resolved_service(name: str, body: object) -> ResolvedService:
    if not isinstance(body, dict):
        msg = f"Service '{name}' in resolved compose config is not an object."
        raise ConfigError(msg)
    container_name = body.get("container_name")
    volumes = [
        ResolvedVolume(source=Path(entry["source"]), target=Path(entry["target"]))
        for entry in body.get("volumes", [])
        if isinstance(entry, dict)
        and entry.get("type") == "bind"
        and isinstance(entry.get("source"), str)
        and isinstance(entry.get("target"), str)
    ]
    return ResolvedService(
        name=name,
        container_name=container_name if isinstance(container_name, str) else None,
        volumes=volumes,
    )


@dataclass
class DockerAdapter:
    """Thin wrapper over ``docker`` and ``docker compose``.

    Two families live here for a reason:

    - ``compose_*`` methods drive lifecycle operations (``up``, ``stop``,
      ``down``, ``build``, ``push``, ``pull``) that need the compose file --
      volumes, env, service definitions. These are one-shot, user-initiated
      calls where the compose-plugin overhead doesn't matter.

    - ``docker_*`` methods target the already-running container directly by
      name, bypassing compose entirely. Every ``docker compose`` invocation
      on Docker Desktop for Mac eats ~3s parsing the compose file before it
      even reaches the daemon, so the hot path (status probes, exec) uses
      plain ``docker exec`` / ``docker inspect`` against ``container_name``
      for roughly 20x lower overhead.
    """

    compose_file: Path
    service_name: str
    container_name: str
    compose_cmd: list[str] = field(default_factory=_detect_compose_command)

    # ------------------------------------------------------------------
    # High-level lifecycle operations -- stream output to the terminal.
    # ------------------------------------------------------------------

    def compose_up(self) -> CommandResult:
        """``docker compose up -d``."""
        return self._run_streaming(["up", "-d"])

    def compose_stop(self) -> CommandResult:
        """``docker compose stop``."""
        return self._run_streaming(["stop"])

    def compose_down(self, *, remove_volumes: bool = False) -> CommandResult:
        """``docker compose down`` (optionally with ``-v``)."""
        args = ["down"]
        if remove_volumes:
            args.append("-v")
        return self._run_streaming(args)

    def compose_build(self) -> CommandResult:
        """``docker compose build``."""
        return self._run_streaming(["build"])

    def compose_push(self) -> CommandResult:
        """``docker compose push``."""
        return self._run_streaming(["push"])

    def compose_pull(self) -> CommandResult:
        """``docker compose pull``."""
        return self._run_streaming(["pull"])

    # ------------------------------------------------------------------
    # Exec and inspect: target the container directly, bypass compose.
    # ------------------------------------------------------------------

    def docker_exec(  # noqa: PLR0913 -- 6 orthogonal I/O knobs (mode + routing); bundling hurts clarity
        self,
        command: list[str],
        *,
        interactive: bool = False,
        user: str = "user",
        merge_stderr: bool = False,
        capture_output: bool = False,
        line_transform: LineTransform | None = None,
    ) -> CommandResult:
        """Run ``command`` inside the container via ``docker exec``.

        Bypasses ``docker compose`` -- target the container by name
        directly. ~20x lower overhead than ``compose_exec`` on Docker
        Desktop for Mac where the compose-plugin startup dominates.

        Semantics otherwise mirror ``compose_exec``: ``interactive`` for
        ``-it``, ``merge_stderr`` routes child stderr to stdout at the
        subprocess boundary, ``capture_output`` swaps streaming for
        buffered capture (used by ``--quiet``). No timeout on streaming
        or capture variants -- user-bounded builds can run for minutes.

        ``line_transform`` (streaming mode only) intercepts each stdout
        line: the returned string is written to ``sys.stdout`` in place
        of the original, or ``None`` drops the line entirely. Used to
        rewrite redo's progress lines into admt-vocabulary equivalents.
        Ignored when ``interactive`` (can't pipe a shell) or when
        ``capture_output`` (the caller post-processes the buffer).
        """
        args = ["exec", "-u", user]
        args.append("-it" if interactive else "-i")
        args.append(self.container_name)
        args.extend(command)
        cmd = ["docker", *args]
        if capture_output:
            # ``capture_output=True`` on subprocess.run is shorthand for
            # stdout=PIPE + stderr=PIPE (separate streams). To honor
            # ``merge_stderr`` in capture mode we have to plumb stderr to
            # STDOUT ourselves -- otherwise redo (which writes to stderr)
            # lands in ``result.stderr`` and a caller inspecting stdout
            # (like WhatCommand) sees nothing.
            stderr_target = subprocess.STDOUT if merge_stderr else subprocess.PIPE
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=stderr_target,
                text=True,
                check=False,
            )
            return CommandResult(
                returncode=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
            )
        if line_transform is not None and not interactive:
            return self._run_streaming_transform(cmd, line_transform, merge_stderr=merge_stderr)
        stderr = subprocess.STDOUT if merge_stderr else None
        return CommandResult(returncode=self._spawn_tracked(cmd, stderr=stderr))

    def docker_exec_captured(
        self,
        command: list[str],
        *,
        user: str = "user",
        timeout: int | None = _BOUNDED_TIMEOUT_SECS,
    ) -> CommandResult:
        """Run ``command`` via ``docker exec`` with captured output.

        Use for status probes and env-snapshot generation, not for
        long-running user-facing commands. ``timeout=None`` disables the
        bounded cap -- reserved for calls whose duration is user-bounded
        (e.g., sourcing ``env/activate``, which pip-installs and alr-builds
        on first run).
        """
        cmd = ["docker", "exec", "-i", "-u", user, self.container_name, *command]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"docker exec timed out after {timeout}s."
            raise ContainerError(msg) from exc
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def docker_exec_with_stdin(
        self, command: list[str], stdin_data: str, *, user: str = "user"
    ) -> CommandResult:
        """Pipe ``stdin_data`` into ``docker exec``; captured output."""
        cmd = ["docker", "exec", "-i", "-u", user, self.container_name, *command]
        try:
            completed = subprocess.run(
                cmd,
                input=stdin_data,
                capture_output=True,
                text=True,
                check=False,
                timeout=_BOUNDED_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"docker exec timed out after {_BOUNDED_TIMEOUT_SECS}s."
            raise ContainerError(msg) from exc
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def docker_inspect_state(self) -> CommandResult:
        """Fetch the container's runtime state via ``docker inspect``.

        Stdout is the status string (``running``, ``exited``, ``paused``,
        ``restarting``, ``dead``, ``created``) plus a trailing newline.
        Non-zero exit with ``No such object`` in stderr means the container
        does not exist.
        """
        cmd = [
            "docker",
            "inspect",
            "-f",
            "{{.State.Status}}",
            self.container_name,
        ]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=_BOUNDED_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"docker inspect timed out after {_BOUNDED_TIMEOUT_SECS}s."
            raise ContainerError(msg) from exc
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    # ------------------------------------------------------------------
    # Image management.
    # ------------------------------------------------------------------

    def image_name(self) -> str | None:
        """Return the image tag for ``service_name`` via ``docker compose config``.

        Returns ``None`` when the compose file does not expose an image for
        this service (e.g., the service is built but never tagged).
        """
        result = self._run_captured(["config", "--images", self.service_name])
        if result.returncode != 0:
            return None
        first_line = result.stdout.strip().splitlines()
        return first_line[0] if first_line else None

    def image_exists_locally(self, image: str) -> bool:
        """``True`` when Docker has ``image`` in its local cache."""
        try:
            completed = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                check=False,
                timeout=_BOUNDED_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"docker image inspect timed out after {_BOUNDED_TIMEOUT_SECS}s."
            raise ContainerError(msg) from exc
        return completed.returncode == 0

    def remove_image(self) -> CommandResult:
        """Remove the compose service's image via ``docker image rm``."""
        image = self.image_name()
        if not image:
            return CommandResult(returncode=0, stdout="", stderr="no image to remove")
        return CommandResult(returncode=self._spawn_tracked(["docker", "image", "rm", "-f", image]))

    # ------------------------------------------------------------------
    # Internal: the two subprocess modes.
    # ------------------------------------------------------------------

    @staticmethod
    def _spawn_tracked(cmd: list[str], *, stderr: int | None = None) -> int:
        """Spawn ``cmd`` with inherited stdio, track it for SIGINT reporting.

        ``stderr`` follows ``subprocess.Popen`` semantics: ``None`` inherits
        the parent's stderr, ``subprocess.STDOUT`` merges it into stdout.
        """
        with _track_subprocess() as register:
            proc = subprocess.Popen(cmd, stderr=stderr)
            register(proc)
            return proc.wait()

    @staticmethod
    def _run_streaming_transform(
        cmd: list[str],
        transform: LineTransform,
        *,
        merge_stderr: bool,
    ) -> CommandResult:
        """Feed each stdout line of ``cmd`` through ``transform`` in real time.

        The child's stdout is captured through a pipe (so we can intercept
        lines) but we stream them out as they arrive -- no full-buffer
        capture. ``bufsize=1`` makes Python line-buffer its read side; the
        child process may still block-buffer under piping since its stdout
        is no longer a TTY. For MVP the tradeoff favors simplicity over a
        pseudo-terminal; redo's bash-based progress lines line-buffer
        naturally and arrive promptly in practice.

        ``transform`` returns the rewritten line (written with a trailing
        newline added if missing), or ``None`` to drop the line entirely
        -- used to suppress redo's redundant top-level status marker.

        ``errors='replace'`` on decoding keeps one malformed byte from
        killing the whole stream -- important because build output is
        untrusted.
        """
        stderr_arg = subprocess.STDOUT if merge_stderr else None
        with _track_subprocess() as register:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=stderr_arg,
                text=True,
                bufsize=1,
                errors="replace",
            )
            register(proc)
            # ``stdout=subprocess.PIPE`` guarantees proc.stdout is non-None;
            # cast narrows for mypy without a runtime check (which would be
            # an unreachable branch for coverage purposes).
            stdout = cast("TextIO", proc.stdout)
            for line in stdout:
                result = transform(line)
                if result is None:
                    continue
                sys.stdout.write(result if result.endswith("\n") else result + "\n")
                sys.stdout.flush()
            return CommandResult(returncode=proc.wait())

    def _run_streaming(self, compose_args: list[str]) -> CommandResult:
        """Spawn ``docker compose ...`` with inherited stdio; no timeout.

        Long-running commands (``up``, ``down``, ``exec redo all``,
        interactive ``bash``) can legitimately run for many minutes. A
        timeout would kill legitimate work, so streaming calls do not set
        one -- the user's Ctrl-C is the intended interrupt.
        """
        cmd = [*self.compose_cmd, "-f", str(self.compose_file), *compose_args]
        return CommandResult(returncode=self._spawn_tracked(cmd))

    def _run_captured(self, compose_args: list[str]) -> CommandResult:
        """Spawn ``docker compose ...`` with captured stdout/stderr; bounded timeout."""
        cmd = [*self.compose_cmd, "-f", str(self.compose_file), *compose_args]
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=_BOUNDED_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"docker compose {compose_args[0]!r} timed out after {_BOUNDED_TIMEOUT_SECS}s."
            raise ContainerError(msg) from exc
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )


@contextmanager
def _track_subprocess() -> Iterator[_Register]:
    """Context manager that registers the yielded subprocess in the active list."""
    tracked: list[subprocess.Popen[bytes]] = []

    def register(proc: subprocess.Popen[bytes]) -> None:
        tracked.append(proc)
        _active_processes.append(proc)

    try:
        yield register
    finally:
        for proc in tracked:
            if proc in _active_processes:
                _active_processes.remove(proc)
