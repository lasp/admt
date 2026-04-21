"""Container lifecycle service and env-snapshot proxy.

Wraps ``DockerAdapter`` with operations admt cares about -- ``start``/
``stop``/``restart``/``login``/``status``/``build_image``/``push_image``/
``pull_image``/``refresh``/``rm``/``exec`` -- plus ``ensure_env_snapshot``,
which materializes ``/tmp/admt/<project>/env_snapshot.sh`` and
``/tmp/admt/<project>/exec.sh`` inside the container per ARCHITECTURE.md
Environment Activation. The proxy script is what every subsequent
``docker compose exec`` runs, so the full ``env/activate`` is paid for
only once (or after ``admt env refresh``).
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import TYPE_CHECKING

from admt.exceptions import ContainerError

# A valid POSIX-ish shell variable name: leading alpha/underscore, then
# alphanumerics/underscores. ``env`` output occasionally contains stray lines
# (e.g., activate-script ``echo``s like ``Note:``) that split into a pseudo-
# ``KEY=VALUE`` where ``KEY`` is not a real identifier -- we skip those.
_VALID_SHELL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

if TYPE_CHECKING:
    from admt.adapters.docker import CommandResult, DockerAdapter
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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Bring the container up; auto-pull the image when missing locally."""
        image = self._docker.image_name()
        if image and not self._docker.image_exists_locally(image):
            self._output.info(f"Image '{image}' not present locally; pulling...")
            pull = self._docker.compose_pull()
            if pull.returncode != 0:
                msg = f"Pull failed for '{image}'. Build the image locally with 'admt env build'."
                raise ContainerError(msg)
        self._raise_on_failure("up", self._docker.compose_up())
        self.ensure_env_snapshot()
        self._output.success(f"Container '{self._project.container_name}' is running.")

    def stop(self) -> None:
        """Stop the container (does not remove it)."""
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
            f"docker compose exec -it -u user {self._project.service_name} /bin/bash"
        )
        return self._docker.compose_exec(["/bin/bash"], interactive=True).returncode

    def status(self) -> ContainerStatus:
        """Probe the compose service's runtime state."""
        result = self._docker.compose_ps()
        if result.returncode != 0:
            return ContainerStatus.UNKNOWN
        entries = self._parse_ps_json(result.stdout)
        entry = entries.get(self._project.service_name)
        if entry is None:
            return ContainerStatus.NOT_FOUND
        state = str(entry.get("State", "")).lower()
        if state == "running":
            return ContainerStatus.RUNNING
        return ContainerStatus.STOPPED

    def is_running(self) -> bool:
        """Boolean convenience around ``status``."""
        return self.status() is ContainerStatus.RUNNING

    # ------------------------------------------------------------------
    # Image management
    # ------------------------------------------------------------------

    def build_image(self) -> None:
        """``docker compose build`` for the active service."""
        self._raise_on_failure("build", self._docker.compose_build())

    def push_image(self) -> None:
        """``docker compose push`` for the active service."""
        self._raise_on_failure("push", self._docker.compose_push())

    def pull_image(self) -> None:
        """``docker compose pull`` for the active service."""
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
        self._raise_on_failure("down", self._docker.compose_down(remove_volumes=remove_volumes))
        if remove_image:
            image_result = self._docker.remove_image()
            if image_result.returncode != 0:
                self._output.warning(f"Failed to remove image: {image_result.stderr.strip()}")
        self._output.success("Container removed.")

    # ------------------------------------------------------------------
    # Exec + env snapshot
    # ------------------------------------------------------------------

    def exec(self, command: str, *, interactive: bool = False) -> int:
        """Run ``command`` inside the container via the admt env proxy."""
        self.ensure_env_snapshot()
        proxy = self._proxy_path()
        self._output.command_echo(
            f"docker compose exec -u user {self._project.service_name} {proxy} bash -c {command!r}"
        )
        return self._docker.compose_exec(
            [proxy, "bash", "-c", command], interactive=interactive
        ).returncode

    def refresh(self) -> None:
        """Delete the cached snapshot and regenerate it from ``env/activate``."""
        self._docker.compose_exec_captured(["rm", "-rf", self._project_tmp_dir()])
        self.ensure_env_snapshot()
        self._output.success("Environment snapshot regenerated.")

    def ensure_env_snapshot(self) -> None:
        """Write ``env_snapshot.sh`` and ``exec.sh`` into the container when missing."""
        proxy = self._proxy_path()
        check = self._docker.compose_exec_captured(["test", "-f", proxy])
        if check.returncode == 0:
            return
        baseline = self._capture_env(["env"])
        activate = str(self._project.activate_script)
        activated = self._capture_env(["bash", "-c", f"source {activate} && env"])
        self._write_container_file(self._snapshot_path(), self._build_snapshot(baseline, activated))
        self._write_container_file(proxy, self._build_proxy(), executable=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _project_tmp_dir(self) -> str:
        return f"/tmp/admt/{self._project.name}"  # noqa: S108 -- container-side path

    def _proxy_path(self) -> str:
        return f"{self._project_tmp_dir()}/exec.sh"

    def _snapshot_path(self) -> str:
        return f"{self._project_tmp_dir()}/env_snapshot.sh"

    def _capture_env(self, cmd: list[str]) -> dict[str, str]:
        result = self._docker.compose_exec_captured(cmd)
        if result.returncode != 0:
            msg = (
                f"Failed to capture container environment (exit {result.returncode}): "
                f"{result.stderr.strip() or '<no stderr>'}"
            )
            raise ContainerError(msg)
        return self._parse_env_output(result.stdout)

    @staticmethod
    def _parse_env_output(output: str) -> dict[str, str]:
        """Parse ``env``-style ``KEY=VALUE`` lines into a dict.

        Lines without ``=`` are skipped. Keys that are not valid shell
        identifiers are skipped -- prevents activate-script chatter
        (``Note:``, ``Warning:``, etc.) from leaking into the snapshot as
        invalid ``export`` statements.
        """
        env: dict[str, str] = {}
        for line in output.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if not _VALID_SHELL_NAME.match(key):
                continue
            env[key] = value
        return env

    def _build_snapshot(self, baseline: dict[str, str], activated: dict[str, str]) -> str:
        lines = [
            "#!/bin/bash",
            "# admt environment snapshot -- generated, do not edit",
            "# Only variables set or modified by env/activate",
        ]
        for key, value in activated.items():
            if baseline.get(key) != value:
                escaped = value.replace('"', '\\"')
                lines.append(f'export {key}="{escaped}"')
        lines.append("")
        return "\n".join(lines)

    def _build_proxy(self) -> str:
        return (
            f'#!/bin/bash\n# Written by admt -- do not edit\nsource {self._snapshot_path()}\n"$@"\n'
        )

    def _write_container_file(self, path: str, content: str, *, executable: bool = False) -> None:
        project_dir = self._project_tmp_dir()
        cmd = ["bash", "-c", f"mkdir -p {project_dir} && cat > {path}"]
        result = self._docker.compose_exec_with_stdin(cmd, content)
        if result.returncode != 0:
            msg = (
                f"Failed to write '{path}' in container (exit {result.returncode}): "
                f"{result.stderr.strip() or '<no stderr>'}"
            )
            raise ContainerError(msg)
        if executable:
            chmod = self._docker.compose_exec_captured(["chmod", "+x", path])
            if chmod.returncode != 0:
                msg = f"Failed to chmod '{path}' in container."
                raise ContainerError(msg)

    @staticmethod
    def _parse_ps_json(output: str) -> dict[str, dict[str, object]]:
        """Parse ``docker compose ps --format json``.

        v2 emits newline-delimited JSON (one object per line). Some older
        builds emit a single JSON array. Handle both.
        """
        text = output.strip()
        if not text:
            return {}
        services: dict[str, dict[str, object]] = {}
        if text.startswith("["):
            try:
                entries = json.loads(text)
            except json.JSONDecodeError:
                return {}
            for entry in entries:
                if isinstance(entry, dict):
                    services[str(entry.get("Service", ""))] = entry
            return services
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                services[str(entry.get("Service", ""))] = entry
        return services

    @staticmethod
    def _raise_on_failure(action: str, result: CommandResult) -> None:
        if result.returncode != 0:
            msg = (
                f"docker compose {action} failed (exit {result.returncode}): "
                f"{result.stderr.strip() or '<no stderr>'}"
            )
            raise ContainerError(msg)
