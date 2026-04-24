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

import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from admt.exceptions import ContainerError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    _Register = Callable[[subprocess.Popen[bytes]], None]


# Bounded calls -- status probes, env snapshot capture, short informational
# commands. Long-running streaming calls DO NOT use this timeout (see the
# comments on streaming methods for why).
_BOUNDED_TIMEOUT_SECS = 60


# Tracks active ``docker compose`` subprocesses so ``main.py``'s SIGINT
# handler can list their PIDs on Ctrl-C. Appended/removed by streaming
# methods; treat as module-private state.
_active_processes: list[subprocess.Popen[bytes]] = []


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


@dataclass
class DockerAdapter:
    """Thin wrapper over ``docker compose`` for a single project's compose file."""

    compose_file: Path
    service_name: str
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
    # Exec variants.
    # ------------------------------------------------------------------

    def compose_exec(
        self,
        command: list[str],
        *,
        interactive: bool = False,
        user: str = "user",
    ) -> CommandResult:
        """Run ``command`` inside the container; streams stdio to the terminal.

        ``interactive=True`` allocates a TTY and forwards stdin (for
        ``admt env login`` and ``admt env exec`` when stdin is a terminal).
        """
        args = ["exec", "-u", user]
        args.append("-it" if interactive else "-T")
        args.append(self.service_name)
        args.extend(command)
        return self._run_streaming(args)

    def compose_exec_captured(self, command: list[str], *, user: str = "user") -> CommandResult:
        """Run ``command`` with captured stdout/stderr; bounded timeout.

        Use for status probes and env-snapshot generation, not for long-running
        user-facing commands.
        """
        args = ["exec", "-T", "-u", user, self.service_name, *command]
        return self._run_captured(args)

    def compose_exec_with_stdin(
        self, command: list[str], stdin_data: str, *, user: str = "user"
    ) -> CommandResult:
        """Run ``command`` with ``stdin_data`` piped in; captured output."""
        args = ["exec", "-T", "-u", user, self.service_name, *command]
        return self._run_with_stdin(args, stdin_data)

    # ------------------------------------------------------------------
    # Status and image management.
    # ------------------------------------------------------------------

    def compose_ps(self) -> CommandResult:
        """``docker compose ps --format json`` (captured)."""
        return self._run_captured(["ps", "--format", "json"])

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
    def _spawn_tracked(cmd: list[str]) -> int:
        """Spawn ``cmd`` with inherited stdio, track it for SIGINT reporting."""
        with _track_subprocess() as register:
            proc = subprocess.Popen(cmd)
            register(proc)
            return proc.wait()

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

    def _run_with_stdin(self, compose_args: list[str], stdin_data: str) -> CommandResult:
        """Pipe ``stdin_data`` into ``docker compose ...`` with captured output."""
        cmd = [*self.compose_cmd, "-f", str(self.compose_file), *compose_args]
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
