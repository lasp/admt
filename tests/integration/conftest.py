"""Integration-tier fixtures.

``admt env init``/``env use`` exercise the real ConfigService end-to-end, which
in production derives compose metadata via ``docker compose config``. Tier 2
must not require Docker (see TEST_PLAN.md), so an autouse fixture patches
``admt.bootstrap.resolve_compose_config`` with a hermetic stand-in that reads
the synthetic compose YAML and resolves relative volume sources -- mirroring
what ``docker compose config`` would emit for these fixtures.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from ruamel.yaml import YAML

from admt.adapters.docker import ResolvedCompose, ResolvedService, ResolvedVolume

# Short-form volume entries are "src:dst[:mode]"; need at least source + target.
_SHORT_VOLUME_MIN_PARTS = 2


def _fake_resolve_compose_config(compose_file: Path, **_kwargs: object) -> ResolvedCompose:
    """Resolve a synthetic compose file the way ``docker compose config`` would.

    Reads ``name``, services, and bind volumes; resolves relative sources
    against the compose file's directory to absolute host paths.
    """
    doc = YAML(typ="safe").load(compose_file.read_text())
    compose_dir = compose_file.parent
    services: dict[str, ResolvedService] = {}
    for svc_name, raw_body in doc["services"].items():
        body = raw_body or {}
        volumes = [
            ResolvedVolume(source=source, target=target)
            for entry in (body.get("volumes") or [])
            for source, target in (_resolve_volume_entry(entry, compose_dir),)
            if source is not None and target is not None
        ]
        services[svc_name] = ResolvedService(
            name=svc_name,
            container_name=body.get("container_name"),
            volumes=volumes,
        )
    name = doc.get("name") or compose_dir.parent.name
    return ResolvedCompose(project_name=name, services=services)


def _resolve_volume_entry(entry: object, compose_dir: Path) -> tuple[Path | None, Path | None]:
    if isinstance(entry, dict):
        if entry.get("type", "bind") != "bind":
            return (None, None)
        raw_source, raw_target = entry.get("source"), entry.get("target")
    elif isinstance(entry, str):
        parts = entry.split(":")
        if len(parts) < _SHORT_VOLUME_MIN_PARTS or not parts[0].startswith((".", "/")):
            return (None, None)
        raw_source, raw_target = parts[0], parts[1]
    else:
        return (None, None)
    if not isinstance(raw_source, str) or not isinstance(raw_target, str):
        return (None, None)
    source = Path(raw_source)
    if not source.is_absolute():
        source = (compose_dir / source).resolve(strict=False)
    return (source, Path(raw_target))


@pytest.fixture(autouse=True)
def _fake_compose_resolver():
    """Replace the docker-backed compose resolver for all integration tests."""
    with patch("admt.bootstrap.resolve_compose_config", _fake_resolve_compose_config):
        yield
