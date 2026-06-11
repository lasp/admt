"""Container lifecycle service.

Wraps ``DockerAdapter`` with the operations admt cares about -- ``start``/
``stop``/``restart``/``login``/``status``/``build_image``/``push_image``/
``pull_image``/``rm``/``exec``/``refresh``. Env-snapshot proxy materialization
(per ARCHITECTURE.md §Environment Activation) lives in
``admt.services.env_snapshot.EnvSnapshotService`` -- this service holds an
instance and delegates ``ensure_env_snapshot`` and ``refresh`` to it.

Lifecycle calls (``start``, ``stop``, ``rm``, image ops) go through
``docker compose`` because they need the compose file. Status probes,
``exec``, and snapshot I/O target the container by name via plain
``docker`` -- ~20x faster than the compose equivalent on Docker Desktop
for Mac, where compose-plugin startup dominates each invocation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from admt.exceptions import ContainerError
from admt.services.env_snapshot import EnvSnapshotService

if TYPE_CHECKING:
    from admt.adapters.docker import CommandResult, DockerAdapter, LineTransform
    from admt.services.config import ProjectConfig
    from admt.services.output import OutputService


class ContainerStatus(StrEnum):
    """Outcome of a status probe against a compose service."""

    RUNNING = "running"
    STOPPED = "stopped"
    NOT_FOUND = "not-found"
    UNKNOWN = "unknown"


class ContainerService:
    """High-level operations on a single project's compose service."""

    def __init__(
        self,
        docker: DockerAdapter,
        project: ProjectConfig,
        output: OutputService,
    ) -> None:
        """Bind the docker adapter, project config, and output service."""
        self._docker = docker
        self._project = project
        self._output = output
        self._snapshot = EnvSnapshotService(docker=docker, project=project, output=output)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Bring the container up; auto-pull the image when missing locally.

        Idempotent: if the container is already running, emit a notice and
        return without invoking ``docker compose up`` (which would be a
        ~3s no-op on Docker Desktop for Mac).
        """
        if self.is_running():
            self._output.info(f"Container '{self._project.container_name}' is already running.")
            return
        image = self._docker.image_name()
        if image and not self._docker.image_exists_locally(image):
            self._output.info(f"Image '{image}' not present locally; pulling...")
            self._echo_compose("pull")
            pull = self._docker.compose_pull()
            if pull.returncode != 0:
                msg = f"Pull failed for '{image}'. Build the image locally with 'admt env build'."
                raise ContainerError(msg)
        self._echo_compose("up -d")
        self._raise_on_failure("up", self._docker.compose_up())
        self.ensure_env_snapshot()
        self._output.success(f"Container '{self._project.container_name}' is running.")

    def stop(self) -> None:
        """Stop the container (does not remove it).

        Idempotent: if the container isn't running (stopped or absent),
        emit a notice and return -- ``docker compose stop`` on a stopped
        container is a no-op but still costs the compose-plugin overhead.
        """
        if not self.is_running():
            self._output.info(f"Container '{self._project.container_name}' is already stopped.")
            return
        self._echo_compose("stop")
        self._raise_on_failure("stop", self._docker.compose_stop())
        self._output.success(f"Container '{self._project.container_name}' stopped.")

    def restart(self) -> None:
        """Stop then start; abort the restart if ``stop`` fails."""
        self.stop()
        self.start()

    def login(self) -> int:
        """Open an interactive bash shell in the container.

        Bypasses the admt proxy script; the container's ``.bashrc`` already
        sources ``env/activate`` (or the admt snapshot) on login.
        """
        self._output.command_echo(
            f"docker exec -it -u user {self._project.container_name} /bin/bash"
        )
        return self._docker.docker_exec(["/bin/bash"], interactive=True).returncode

    def status(self) -> ContainerStatus:
        """Probe the container's runtime state via ``docker inspect``.

        Distinguishes NOT_FOUND (container doesn't exist) from STOPPED
        (exists but isn't running) by the ``No such object`` marker docker
        emits on stderr when the name doesn't resolve.
        """
        result = self._docker.docker_inspect_state()
        if result.returncode != 0:
            if "no such object" in result.stderr.lower():
                return ContainerStatus.NOT_FOUND
            return ContainerStatus.UNKNOWN
        state = result.stdout.strip().lower()
        if state == "running":
            return ContainerStatus.RUNNING
        if not state:
            return ContainerStatus.NOT_FOUND
        return ContainerStatus.STOPPED

    def is_running(self) -> bool:
        """Boolean convenience around ``status``."""
        return self.status() is ContainerStatus.RUNNING

    # ------------------------------------------------------------------
    # Image management
    # ------------------------------------------------------------------

    def build_image(self) -> None:
        """``docker compose build`` for the active service."""
        self._echo_compose("build")
        self._raise_on_failure("build", self._docker.compose_build())

    def push_image(self) -> None:
        """``docker compose push`` for the active service."""
        self._echo_compose("push")
        self._raise_on_failure("push", self._docker.compose_push())

    def pull_image(self) -> None:
        """``docker compose pull`` for the active service."""
        self._echo_compose("pull")
        self._raise_on_failure("pull", self._docker.compose_pull())

    def rm(
        self,
        *,
        remove_volumes: bool = False,
        remove_image: bool = False,
        remove_all: bool = False,
    ) -> None:
        """Remove the container; optional volume/image removal."""
        if remove_all:
            remove_volumes = True
            remove_image = True
        down_subcommand = "down -v" if remove_volumes else "down"
        self._echo_compose(down_subcommand)
        self._raise_on_failure("down", self._docker.compose_down(remove_volumes=remove_volumes))
        if remove_image:
            image_result = self._docker.remove_image()
            if image_result.returncode != 0:
                self._output.warning(f"Failed to remove image: {image_result.stderr.strip()}")
        self._output.success("Container removed.")

    # ------------------------------------------------------------------
    # Exec + env snapshot
    # ------------------------------------------------------------------

    def ensure_running(self) -> None:
        """Bring the container up if it's not already running.

        Honors the Output flags: ``--yes`` auto-starts without prompting,
        ``ADMT_NONINTERACTIVE`` raises a clear error instead of prompting,
        interactive mode prompts with default "yes" (start). An explicit
        "no" raises ``ContainerError`` so the command aborts cleanly.
        """
        if self.is_running():
            return
        not_running = f"Container '{self._project.container_name}' is not running."
        if self._output.noninteractive:
            msg = f"{not_running} Run 'admt env start' first, or pass '--yes' to auto-start."
            raise ContainerError(msg)
        if self._output.yes or self._output.prompt(f"{not_running} Start it?", default=True):
            self.start()
            return
        msg = f"{not_running} Declined to start; cannot proceed."
        raise ContainerError(msg)

    def exec(
        self,
        command: str,
        *,
        interactive: bool = False,
        merge_stderr: bool = False,
        capture_output: bool = False,
        line_transform: LineTransform | None = None,
    ) -> int:
        """Run ``command`` inside the container via the admt env proxy.

        Optimistic path: invoke the proxy directly and recover on failure.
        Pre-flighting ``is_running`` and ``ensure_env_snapshot`` on every
        call is expensive (each is a full ``docker compose`` round-trip on
        Docker Desktop for Mac -- ~3s apiece). On the steady-state happy
        path -- container up, snapshot present -- this saves two of three
        docker calls.

        If the initial exec fails, ``_recover_infrastructure`` diagnoses
        whether the container is down (prompt / auto-start / error per
        ``--yes`` / ``ADMT_NONINTERACTIVE``) or whether the snapshot was
        wiped by an external container restart (transparent regenerate).
        If neither applies, the failure is the user's command -- propagate
        as-is without a spurious retry.

        ``merge_stderr`` / ``capture_output`` / ``line_transform`` are
        forwarded to the DockerAdapter -- see its docstring for semantics.
        In quiet mode (``capture_output=True``), the buffered output is
        emitted verbatim on failure via ``OutputService.emit_captured`` so
        the user still sees what went wrong. ``line_transform`` is ignored
        under ``capture_output`` or ``interactive`` (both conflict with
        line-level streaming).
        """
        proxy = self._proxy_path()
        echo_str = f"docker exec -u user {self._project.container_name} {proxy} bash -c {command!r}"
        self._output.command_echo(echo_str)
        result = self._docker.docker_exec(
            [proxy, "bash", "-c", command],
            interactive=interactive,
            merge_stderr=merge_stderr,
            capture_output=capture_output,
            line_transform=line_transform,
        )
        if result.returncode != 0 and self._recover_infrastructure():
            self._output.command_echo(echo_str)
            result = self._docker.docker_exec(
                [proxy, "bash", "-c", command],
                interactive=interactive,
                merge_stderr=merge_stderr,
                capture_output=capture_output,
                line_transform=line_transform,
            )
        if capture_output and result.returncode != 0:
            if result.stdout:
                self._output.emit_captured(result.stdout)
            if result.stderr:
                self._output.emit_captured(result.stderr, to_stderr=True)
        # Per ARCHITECTURE R11: "the underlying command is always shown, even
        # without --verbose. Every container-forwarded operation reports what
        # ran when it fails." Already shown on the verbose path before exec;
        # echo again here for non-verbose users so they don't have to re-run.
        if result.returncode != 0 and not self._output.verbose:
            self._output.error(f"Failed (exit {result.returncode}): {echo_str}")
        return result.returncode

    def exec_captured(self, command: str, *, merge_stderr: bool = True) -> CommandResult:
        """Run ``command`` and return the captured ``CommandResult``.

        Like ``exec`` but always captures output and returns the full result
        so callers can post-process it. Used by ``admt what`` to translate
        the redo target listing into admt-command equivalents.
        """
        proxy = self._proxy_path()
        echo_str = f"docker exec -u user {self._project.container_name} {proxy} bash -c {command!r}"
        self._output.command_echo(echo_str)
        result = self._docker.docker_exec(
            [proxy, "bash", "-c", command],
            interactive=False,
            merge_stderr=merge_stderr,
            capture_output=True,
        )
        if result.returncode != 0 and self._recover_infrastructure():
            self._output.command_echo(echo_str)
            result = self._docker.docker_exec(
                [proxy, "bash", "-c", command],
                interactive=False,
                merge_stderr=merge_stderr,
                capture_output=True,
            )
        if result.returncode != 0 and not self._output.verbose:
            self._output.error(f"Failed (exit {result.returncode}): {echo_str}")
        return result

    def _recover_infrastructure(self) -> bool:
        """Diagnose a failed exec and fix recoverable infrastructure state.

        Returns True when the caller should retry -- either the container
        was down and we started it, or ``/tmp/admt/<project>/`` was wiped
        and we regenerated the snapshot. Returns False when everything
        was already fine, meaning the failure was the user's command and
        there is nothing to retry.
        """
        if not self.is_running():
            # Honors --yes / ADMT_NONINTERACTIVE / interactive prompt; raises
            # ContainerError on decline or in non-interactive mode. start()
            # regenerates the snapshot as part of its flow.
            self.ensure_running()
            return True
        return self.ensure_env_snapshot()

    def refresh(self) -> None:
        """Delete the cached snapshot and regenerate it from ``env/activate``."""
        self._snapshot.refresh()

    def ensure_env_snapshot(self) -> bool:
        """Write ``env_snapshot.sh`` and ``exec.sh`` into the container when missing.

        Returns True when the snapshot had to be (re)generated, False when
        it was already present and nothing was written. Used by ``exec``
        to decide whether a retry is warranted.
        """
        return self._snapshot.ensure()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _echo_compose(self, subcommand: str) -> None:
        """Emit ``$ docker compose -f <compose_file> <subcommand>`` when verbose."""
        self._output.command_echo(f"docker compose -f {self._project.compose_file} {subcommand}")

    def _proxy_path(self) -> str:
        """Container-side path of the snapshot proxy script."""
        return self._snapshot.proxy_path()

    @staticmethod
    def _raise_on_failure(action: str, result: CommandResult) -> None:
        if result.returncode != 0:
            msg = (
                f"docker compose {action} failed (exit {result.returncode}): "
                f"{result.stderr.strip() or '<no stderr>'}"
            )
            raise ContainerError(msg)
