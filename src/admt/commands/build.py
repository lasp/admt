"""``admt build`` -- forward to ``redo all`` (or explicit targets)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from admt.commands.base import ContainerPassthroughCommand
from admt.exceptions import ArgumentError, ConfigError

if TYPE_CHECKING:
    from admt.context import Context, Result


class BuildCommand(ContainerPassthroughCommand):
    """Build via redo; defaults to ``redo all`` when no target is supplied."""

    name: ClassVar[str] = "build"
    help: ClassVar[str] = "Build via redo (default target: all)."
    redo_target: ClassVar[str] = "all"
    status_verb: ClassVar[str | None] = "building"

    def resolve_targets(self, context: Context) -> list[str]:
        """Return the supplied targets (absolute host paths mapped), else ``[all]``.

        All targets forward in one redo invocation, in order. A directory
        is only legal as the sole positional -- among multiple arguments
        the intended working directory would be ambiguous, so a target
        naming an existing directory is an argument error. An absolute
        target is a host path and maps through the volume mounts exactly
        like directory arguments (``PathNotMappedError``, exit 4, when
        under no mount); relative targets are forwarded verbatim -- they
        resolve against the mapped working directory identically on both
        sides of the mount, and plain target names (``all``,
        ``foo_type_ranges.elf``) are relative by construction.
        """
        if not context.targets:
            return [self.redo_target]
        self._reject_directories(context)
        return [self._map_target(context, target) for target in context.targets]

    @staticmethod
    def _reject_directories(context: Context) -> None:
        """Error when a directory sits among multiple positional targets."""
        if len(context.targets) <= 1:
            return
        for target in context.targets:
            if (Path.cwd() / target).resolve(strict=False).is_dir():
                msg = (
                    f"'{target}' is a directory. A directory argument must be the "
                    f"sole positional; multiple arguments are all redo targets."
                )
                raise ArgumentError(msg)

    @staticmethod
    def _map_target(context: Context, target: str) -> str:
        """Map an absolute host target through the mounts; forward relative verbatim."""
        target_path = Path(target)
        if not target_path.is_absolute():
            return target
        if context.path_mapper is None:
            msg = "This command requires an active project. Run 'admt env init' to set one up."
            raise ConfigError(msg)
        return str(context.path_mapper.host_to_container(target_path))

    def execute(self, context: Context) -> Result:
        """Run the passthrough build; on failure, hint at generated-source targets.

        Adamant generates sources into each directory's ``build/src/``, so
        a source-relative name (``src/types/foo.ads``) has no redo rule and
        redo's error does not redirect. When the build fails, each target
        that names an Ada source outside ``build/`` and has a ``build/src/``
        twin on the host gets a hint pointing at it. Advisory only: the
        exit code stays redo's, and no hint fires for absent candidates.
        """
        result = super().execute(context)
        if result.exit_code != 0:
            for hint in self._generated_source_hints(context):
                context.output.info(hint)
        return result

    @staticmethod
    def _generated_source_hints(context: Context) -> list[str]:
        """Return ``build/src/`` suggestions for targets whose twins exist."""
        host_cwd = (context.path if context.path is not None else Path.cwd()).resolve(strict=False)
        hints: list[str] = []
        for target in context.targets:
            if not target.endswith((".ads", ".adb")):
                continue
            rel = Path(target)
            if rel.is_absolute() or "build" in rel.parts:
                continue
            candidate = rel.parent / "build" / "src" / rel.name
            if (host_cwd / candidate).exists():
                hints.append(
                    f"Generated sources are built under build/src/; try 'admt build {candidate}'."
                )
        return hints
