"""Service wiring for the admt CLI.

Lives outside ``cli.py`` so that the architectural size cap on CLI callbacks
is not strained by service instantiation. Called once by the top-level CLI
group callback to produce a fully populated ``Context``.
"""

from __future__ import annotations

import os
from pathlib import Path

from admt.adapters.yaml_adapter import YamlAdapter
from admt.context import Context
from admt.services.config import ConfigService
from admt.services.output import OutputService


def build_context(
    *,
    verbose: bool,
    quiet: bool,
    debug: bool,
    yes: bool,
    force: bool,
) -> Context:
    """Instantiate services and return a fully wired Context."""
    noninteractive = bool(os.environ.get("ADMT_NONINTERACTIVE"))
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
