"""YAML adapter built on ruamel.yaml.

Provides two capabilities:

- Plain read/write of YAML files, preserving comments and structure on
  round-trips so that admt does not damage user-authored config.
- A targeted parser for Docker Compose files that extracts the subset
  admt cares about: top-level ``name``, service definitions, and volume
  mounts (both short ``./src:/dst`` form and long ``{type, source,
  target}`` form).

No other layer should import ``ruamel.yaml`` directly; go through here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML

from admt.exceptions import ConfigError


@dataclass
class ComposeService:
    """One service definition extracted from a Docker Compose file.

    Attributes:
        name: The service's key in the compose ``services`` map.
        container_name: The ``container_name`` field when present.
        volumes: Absolute host source path -> absolute container target path.
    """

    name: str
    container_name: str | None = None
    volumes: dict[Path, Path] = field(default_factory=dict)


@dataclass
class ComposeFile:
    """The subset of a Docker Compose file that admt consumes.

    Attributes:
        project_name: Value of the top-level ``name:`` field.
        services: Parsed services keyed by service name.
    """

    project_name: str | None
    services: dict[str, ComposeService]


class YamlAdapter:
    """Adapter around ruamel.yaml for reading, writing, and compose parsing."""

    def __init__(self) -> None:
        """Configure a single ruamel.yaml instance for round-trip safety."""
        self._yaml = YAML(typ="rt")
        self._yaml.preserve_quotes = True

    def load(self, path: Path) -> object:
        """Load a YAML file and return the parsed document.

        Callers must narrow the return type (typically via ``isinstance``).

        Args:
            path: Absolute path to the file.

        Returns:
            The parsed YAML document (typically a dict or list).

        Raises:
            ConfigError: If the file cannot be read or parsed.
        """
        try:
            with path.open("r", encoding="utf-8") as fh:
                return self._yaml.load(fh)
        except FileNotFoundError as exc:
            msg = f"YAML file not found: {path}"
            raise ConfigError(msg) from exc
        except OSError as exc:
            msg = f"Cannot read YAML file {path}: {exc}"
            raise ConfigError(msg) from exc

    def dump(self, data: object, path: Path) -> None:
        """Write ``data`` to ``path`` as YAML, creating parents as needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            self._yaml.dump(data, fh)

    def parse_compose(self, path: Path) -> ComposeFile:
        """Parse a Docker Compose file into a ``ComposeFile``.

        Resolves relative volume sources against the compose file's
        parent directory, so callers always see absolute host paths.

        Args:
            path: Absolute path to ``docker-compose.yml`` (or similar).

        Returns:
            A ``ComposeFile`` with the subset admt needs.

        Raises:
            ConfigError: If the file is unreadable, malformed, or missing
                the ``services`` key.
        """
        doc = self.load(path)
        if not isinstance(doc, dict):
            msg = f"Compose file {path} does not contain a YAML mapping."
            raise ConfigError(msg)
        services_raw = doc.get("services")
        if not isinstance(services_raw, dict):
            msg = f"Compose file {path} has no 'services' section."
            raise ConfigError(msg)
        compose_dir = path.parent
        services = {
            name: self._parse_service(name, body, compose_dir)
            for name, body in services_raw.items()
        }
        project_name = doc.get("name")
        return ComposeFile(
            project_name=project_name if isinstance(project_name, str) else None,
            services=services,
        )

    def _parse_service(self, name: str, body: object, compose_dir: Path) -> ComposeService:
        if not isinstance(body, dict):
            msg = f"Compose service '{name}' is not a mapping."
            raise ConfigError(msg)
        container_name_raw = body.get("container_name")
        container_name = container_name_raw if isinstance(container_name_raw, str) else None
        volumes = self._parse_volumes(name, body.get("volumes", []), compose_dir)
        return ComposeService(name=name, container_name=container_name, volumes=volumes)

    def _parse_volumes(self, service_name: str, raw: object, compose_dir: Path) -> dict[Path, Path]:
        if not isinstance(raw, list):
            msg = f"Compose service '{service_name}' has a non-list 'volumes' field."
            raise ConfigError(msg)
        result: dict[Path, Path] = {}
        for entry in raw:
            source, target = self._parse_volume_entry(service_name, entry)
            if source is None or target is None:
                continue
            host_path = self._resolve_source(source, compose_dir)
            result[host_path] = target
        return result

    def _parse_volume_entry(
        self, service_name: str, entry: object
    ) -> tuple[str | None, Path | None]:
        if isinstance(entry, str):
            return self._parse_short_volume(service_name, entry)
        if isinstance(entry, dict):
            return self._parse_long_volume(entry)
        msg = f"Compose service '{service_name}' has an unrecognized volume entry."
        raise ConfigError(msg)

    @staticmethod
    def _parse_short_volume(service_name: str, entry: str) -> tuple[str | None, Path | None]:
        # Short form: "src:dst" or "src:dst:mode". Skip named volumes (no leading . or /).
        parts = entry.split(":")
        short_form_min = 2
        if len(parts) < short_form_min:
            msg = f"Compose service '{service_name}' has a malformed short volume '{entry}'."
            raise ConfigError(msg)
        source, target = parts[0], parts[1]
        if not source.startswith((".", "/")):
            # Named volume; admt does not track these.
            return (None, None)
        return (source, Path(target))

    @staticmethod
    def _parse_long_volume(entry: dict[str, object]) -> tuple[str | None, Path | None]:
        if entry.get("type") and entry["type"] != "bind":
            return (None, None)
        source = entry.get("source")
        target = entry.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            return (None, None)
        return (source, Path(target))

    @staticmethod
    def _resolve_source(source: str, compose_dir: Path) -> Path:
        # Absolute sources are already fine; relative ones resolve against the compose dir.
        candidate = Path(source)
        if not candidate.is_absolute():
            candidate = compose_dir / candidate
        return candidate.resolve(strict=False)
