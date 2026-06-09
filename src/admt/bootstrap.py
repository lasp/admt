"""Service wiring for the admt CLI.

Lives outside ``cli.py`` so that the architectural size cap on CLI callbacks
is not strained by service instantiation. ``build_context`` is called once
by the top-level CLI group; ``build_container_service`` is called lazily by
``cli._run_command`` for commands that opt in via ``requires_container``.
"""

from __future__ import annotations

import os
from pathlib import Path

from admt.adapters.docker import DockerAdapter, resolve_compose_config
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
        compose_resolver=resolve_compose_config,
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


def build_container_service(
    context: Context,
) -> tuple[ContainerService, PathMapperService]:
    """Resolve the active project and build the ContainerService + PathMapper.

    Returns both objects as a tuple so the caller (``cli._run_command``)
    can populate ``context`` explicitly. The previous form mutated
    ``context.path_mapper`` as a side effect, which made the function's
    contract surprising: the return-type signature lied about what the
    function does. Now the contract is "given a context, build these two
    services" -- caller is responsible for wiring them onto the context.

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
    container = ContainerService(docker=docker, project=project, output=context.output)
    path_mapper = PathMapperService(project.volume_mounts)
    return container, path_mapper
