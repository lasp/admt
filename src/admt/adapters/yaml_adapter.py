"""YAML adapter built on ruamel.yaml.

Plain read/write of YAML files, preserving comments and structure on
round-trips so that admt does not damage user-authored config. Used for the
admt config store (``~/.admt/config.yml``).

Compose-file metadata is NOT parsed here: it is derived from
``docker compose config`` (see ``adapters.docker.resolve_compose_config``),
which resolves ``.env`` interpolation and emits absolute volume sources.

No other layer should import ``ruamel.yaml`` directly; go through here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from admt.exceptions import ConfigError

if TYPE_CHECKING:
    from pathlib import Path


class YamlAdapter:
    """Adapter around ruamel.yaml for reading and writing YAML files."""

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
        except YAMLError as exc:
            # Malformed YAML must surface as a clean admt error (exit 2), not a
            # raw ruamel traceback that the CLI's AdmtError handler can't catch.
            msg = f"Cannot parse YAML file {path}: {exc}"
            raise ConfigError(msg) from exc

    def dump(self, data: object, path: Path) -> None:
        """Write ``data`` to ``path`` as YAML, creating parents as needed.

        Raises:
            ConfigError: If the file or its parent directory cannot be written
                (permissions, read-only filesystem, disk full). Mirrors
                ``load`` -- raw ``OSError`` must not escape the adapter, since
                the CLI converts only ``AdmtError`` into clean exits.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                self._yaml.dump(data, fh)
        except OSError as exc:
            msg = f"Cannot write YAML file {path}: {exc}"
            raise ConfigError(msg) from exc
