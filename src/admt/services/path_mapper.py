"""Maps between host filesystem paths and container filesystem paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

from admt.exceptions import PathNotMappedError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


class PathMapperService:
    """Maps between host filesystem paths and container paths.

    The mount table is sorted once at construction by descending host-path
    length, so that ``host_to_container`` picks the longest-prefix match
    when two mounts overlap (e.g., ``/projects`` and ``/projects/adamant``).
    """

    def __init__(self, volume_mounts: Mapping[Path, Path]) -> None:
        """Canonicalize host keys and order by descending prefix length."""
        resolved = {
            host.resolve(strict=False): container for host, container in volume_mounts.items()
        }
        self._mounts: dict[Path, Path] = dict(
            sorted(resolved.items(), key=lambda kv: len(str(kv[0])), reverse=True)
        )

    @property
    def mounts(self) -> dict[Path, Path]:
        """A copy of the volume mount table, ordered longest-prefix first."""
        return dict(self._mounts)

    def host_to_container(self, host_path: Path) -> Path:
        """Map a host path to its container equivalent.

        Args:
            host_path: Any path on the host; need not exist.

        Returns:
            The container-side path under the longest-matching mount.

        Raises:
            PathNotMappedError: If ``host_path`` is not under any mount.
        """
        resolved = host_path.resolve(strict=False)
        for host_mount, container_mount in self._mounts.items():
            try:
                relative = resolved.relative_to(host_mount)
            except ValueError:
                continue
            return container_mount / relative
        msg = self._unmapped_message(resolved, side="Host")
        raise PathNotMappedError(msg)

    def container_to_host(self, container_path: Path) -> Path:
        """Map a container path back to its host equivalent (longest prefix wins)."""
        ordered = sorted(self._mounts.items(), key=lambda kv: len(str(kv[1])), reverse=True)
        for host_mount, container_mount in ordered:
            try:
                relative = container_path.relative_to(container_mount)
            except ValueError:
                continue
            return host_mount / relative
        msg = self._unmapped_message(container_path, side="Container")
        raise PathNotMappedError(msg)

    def is_mapped(self, host_path: Path) -> bool:
        """Return ``True`` when ``host_path`` falls under one of the mounts."""
        try:
            self.host_to_container(host_path)
        except PathNotMappedError:
            return False
        return True

    def _unmapped_message(self, path: Path, *, side: str) -> str:
        mapped_lines = "\n".join(f"  {h} -> {c}" for h, c in self._mounts.items())
        if not mapped_lines:
            mapped_lines = "  (no mounts configured)"
        return (
            f"{side} path '{path}' is not under any volume mount.\n"
            f"Mapped directories:\n{mapped_lines}"
        )
