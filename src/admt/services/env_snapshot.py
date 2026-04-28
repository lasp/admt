"""Env-snapshot proxy: materialize ``env_snapshot.sh`` and ``exec.sh`` in the container.

Encapsulates the per-project ``/tmp/admt/<project>/`` machinery from
ARCHITECTURE.md §Environment Activation:

  1. Capture the container's baseline env (a ``docker exec env``).
  2. Source ``env/activate`` with stdio inherited so the user sees its
     progress (first-run pip installs and gprbuild can take minutes), and
     redirect the resulting ``env`` dump to a container-side file. Read it
     back with a short bounded ``cat``.
  3. Diff baseline vs. activated; write only the changed/added vars to
     ``env_snapshot.sh`` so subsequent execs source a fast flat list of
     exports instead of re-running activate.
  4. Write ``exec.sh`` -- a short proxy script that sources the snapshot
     and execs ``"$@"``. Every passthrough exec runs through this proxy.

Lives outside ``container.py`` because it has its own bug surface (env
parsing, shell-quote escaping, container-side file paths) that is
orthogonal to lifecycle (start/stop/restart) and exec/recovery.
``ContainerService`` holds an instance and delegates ``ensure_env_snapshot``
and ``refresh`` to it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from admt.exceptions import ContainerError

if TYPE_CHECKING:
    from admt.adapters.docker import DockerAdapter
    from admt.services.config import ProjectConfig
    from admt.services.output import OutputService


# A valid POSIX-ish shell variable name: leading alpha/underscore, then
# alphanumerics/underscores. ``env`` output occasionally contains stray lines
# (e.g., activate-script ``echo``s like ``Note:``) that split into a pseudo-
# ``KEY=VALUE`` where ``KEY`` is not a real identifier -- we skip those.
_VALID_SHELL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvSnapshotService:
    """Owns the per-project env-snapshot proxy script materialization."""

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
    # Public surface (called by ContainerService).
    # ------------------------------------------------------------------

    def ensure(self) -> bool:
        """Write ``env_snapshot.sh`` and ``exec.sh`` into the container when missing.

        Returns True when the snapshot had to be (re)generated, False when
        it was already present and nothing was written. Used by
        ``ContainerService.exec`` to decide whether a retry is warranted
        after an exec failure.
        """
        proxy = self.proxy_path()
        check = self._docker.docker_exec_captured(["test", "-f", proxy])
        if check.returncode == 0:
            return False
        baseline = self._capture_env(["env"])
        activated = self._capture_activated_env()
        self._write_container_file(self.snapshot_path(), self._build_snapshot(baseline, activated))
        self._write_container_file(proxy, self._build_proxy(), executable=True)
        return True

    def refresh(self) -> None:
        """Delete the cached snapshot and regenerate it from ``env/activate``."""
        self._docker.docker_exec_captured(["rm", "-rf", self.project_tmp_dir()])
        self.ensure()
        self._output.success("Environment snapshot regenerated.")

    # ------------------------------------------------------------------
    # Container-side paths (also consumed by ContainerService.exec for
    # building the proxy invocation).
    # ------------------------------------------------------------------

    def project_tmp_dir(self) -> str:
        """The per-project container-side temp dir, e.g., ``/tmp/admt/myproj``."""
        return f"/tmp/admt/{self._project.name}"  # noqa: S108 -- container-side path

    def proxy_path(self) -> str:
        """Container-side path of the proxy script that every exec runs through."""
        return f"{self.project_tmp_dir()}/exec.sh"

    def snapshot_path(self) -> str:
        """Container-side path of the flat ``KEY=VALUE`` snapshot file."""
        return f"{self.project_tmp_dir()}/env_snapshot.sh"

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    def _capture_env(self, cmd: list[str]) -> dict[str, str]:
        """Capture short ``env``-style output; parse ``KEY=VALUE`` pairs.

        Uses the adapter's bounded timeout -- fine for the baseline ``env``
        snapshot. Long-running captures (sourcing ``env/activate``) go
        through ``_capture_activated_env`` instead, which streams output
        live and has no timeout.
        """
        result = self._docker.docker_exec_captured(cmd)
        if result.returncode != 0:
            msg = (
                f"Failed to capture container environment (exit {result.returncode}): "
                f"{result.stderr.strip() or '<no stderr>'}"
            )
            raise ContainerError(msg)
        return self._parse_env_output(result.stdout)

    def _capture_activated_env(self) -> dict[str, str]:
        """Source ``env/activate`` with its output streamed live to the user.

        First-run activation can take many minutes (pip installs, alr builds,
        wget+gprbuild of the Pico runtime). Running it through the
        ``capture_output`` path hides that progress behind a silent wall of
        waiting. This path inherits stdio so the user sees the activate
        script's own chatter (``Setting up...``, ``[Ada] ... [gprlib] ...``,
        ``Done.``) as it arrives, while the final ``env`` dump is redirected
        to a container-side file so it doesn't flood the terminal. We then
        read the file back in a short bounded ``cat`` to do the parse.
        """
        project_dir = self.project_tmp_dir()
        env_file = f"{project_dir}/env_activated"
        activate = str(self._project.activate_script)
        shell_cmd = f"mkdir -p {project_dir} && source {activate} && env > {env_file}"
        self._output.info(
            "Activating environment in container (first run can take several minutes)..."
        )
        result = self._docker.docker_exec(["bash", "-c", shell_cmd], merge_stderr=True)
        if result.returncode != 0:
            msg = f"env/activate failed in container (exit {result.returncode}); see output above."
            raise ContainerError(msg)
        cat = self._docker.docker_exec_captured(["cat", env_file])
        if cat.returncode != 0:
            msg = (
                f"Failed to read activated environment from '{env_file}' "
                f"(exit {cat.returncode}): {cat.stderr.strip() or '<no stderr>'}"
            )
            raise ContainerError(msg)
        return self._parse_env_output(cat.stdout)

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
        """Diff baseline vs. activated; emit a flat ``export`` list."""
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
        """The proxy script template: source the snapshot, exec the args."""
        return (
            f'#!/bin/bash\n# Written by admt -- do not edit\nsource {self.snapshot_path()}\n"$@"\n'
        )

    def _write_container_file(self, path: str, content: str, *, executable: bool = False) -> None:
        """Pipe ``content`` into a ``cat > <path>`` inside the container."""
        project_dir = self.project_tmp_dir()
        cmd = ["bash", "-c", f"mkdir -p {project_dir} && cat > {path}"]
        result = self._docker.docker_exec_with_stdin(cmd, content)
        if result.returncode != 0:
            msg = (
                f"Failed to write '{path}' in container (exit {result.returncode}): "
                f"{result.stderr.strip() or '<no stderr>'}"
            )
            raise ContainerError(msg)
        if executable:
            chmod = self._docker.docker_exec_captured(["chmod", "+x", path])
            if chmod.returncode != 0:
                msg = f"Failed to chmod '{path}' in container."
                raise ContainerError(msg)
