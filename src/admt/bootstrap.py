"""Service wiring for the admt CLI.

Lives outside ``cli.py`` so that the architectural size cap on CLI callbacks
is not strained by service instantiation. ``build_context`` is called once
by the top-level CLI group; ``build_container_service`` is called lazily by
``cli._run_command`` for commands that opt in via ``requires_container``.
"""

from __future__ import annotations

import os
from pathlib import Path

from admt.adapters.docker import DockerAdapter
from admt.adapters.yaml_adapter import YamlAdapter
from admt.context import Context
from admt.services.config import ConfigService
from admt.services.container import ContainerService
from admt.services.output import OutputService
from admt.services.path_mapper import PathMapperService


def build_context(
    *,
    verbose: bool,
    quiet: bool,
    debug: bool,
    yes: bool,
    force: bool,
) -> Context:
    """Instantiate services and return a fully wired Context."""
    # ADMT_NONINTERACTIVE: unset or "0" -> off; any other value (e.g., "1",
    # "true", "yes", "on") -> on. This matches POSIX-shell-style boolean
    # conventions and lets users with ``ADMT_NONINTERACTIVE=0`` in their
    # environment treat that as "explicitly off."
    raw = os.environ.get("ADMT_NONINTERACTIVE", "")
    noninteractive = raw not in ("", "0")
    output = OutputService(
        verbose=verbose,
        quiet=quiet,
        yes=yes,
        noninteractive=noninteractive,
    )
    config_service = ConfigService(
        config_dir=Path.home() / ".admt",
        output=output,
        yaml_adapter=YamlAdapter(),
    )
    return Context(
        config_service=config_service,
        output=output,
        verbose=verbose,
        quiet=quiet,
        debug=debug,
        yes=yes,
        force=force,
        noninteractive=noninteractive,
    )


def build_container_service(context: Context) -> ContainerService:
    """Resolve the active project and wire a ContainerService for it.

    Also attaches a ``PathMapperService`` to ``context.path_mapper`` so
    passthrough commands can resolve host paths to container paths without
    another project lookup.

    Raises ``ConfigError`` via ``get_active_project`` when no project is
    configured -- callers should let that propagate so the CLI adapter
    formats the standard "run 'admt env init'" error.
    """
    project = context.config_service.get_active_project()
    docker = DockerAdapter(
        compose_file=project.compose_file,
        service_name=project.service_name,
        container_name=project.container_name,
    )
    context.path_mapper = PathMapperService(project.volume_mounts)
    return ContainerService(docker=docker, project=project, output=context.output)
