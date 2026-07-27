"""``admt templates`` -- run ``redo templates`` and optionally copy the stubs.

The command extends the redo passthrough with a post-exec stub-copy dance:

1. Run ``redo templates`` in the container (via the passthrough base).
2. Collect the Ada ``.ads``/``.adb`` files in ``<host_cwd>/build/template/``
   (the volume-mounted build dir on the host). The framework routes only
   hand-editable sources there -- implementation, tester, and main/test
   entry files (see ``redo/rules/build_templates.py`` and the template-
   destination rule in ``gen/generators/basic.py`` in adamant) -- so the
   directory's contents are the stub set.
3. Prompt ``Copy implementation stubs to source directory? [Y/n]``, honoring
   ``--yes``/``--force`` (auto-copy). ``ADMT_NONINTERACTIVE`` without either
   flag skips the copy and says so -- the redo itself still ran, which is
   the useful side effect for scripted contexts.
4. Back up any pre-existing destination files to ``tempfile.mkdtemp(prefix=
   "admt-backup-")`` before overwriting. Write a manifest into the backup
   dir recording the source directory + filenames, and store the backup
   dir's path in ``~/.admt/backup-latest``.
5. Copy the new stubs into ``<host_cwd>/`` (the source dir for the
   component).

``admt templates --undo`` short-circuits the redo run and restores the
most recent backup using the manifest. Only the last backup is
restorable -- no history stack.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from admt.commands.base import ContainerPassthroughCommand
from admt.context import Result
from admt.exceptions import ArgumentError

if TYPE_CHECKING:
    from admt.context import Context


_MANIFEST_NAME = "manifest.json"
_MARKER_NAME = "backup-latest"


def _latest_backup_marker() -> Path:
    """Path to the pointer file that records the last backup directory."""
    return Path.home() / ".admt" / _MARKER_NAME


def _find_stubs(build_template: Path) -> list[Path]:
    """Return generated stubs under ``build_template``, sorted.

    Everything the framework deposits in ``build/template/`` is a
    hand-editable stub by its own classification (implementation, tester,
    and main/test entry sources), so the directory's Ada files ARE the
    stub set -- admt does not re-derive the framework's naming rule.
    """
    if not build_template.is_dir():
        return []
    stubs: list[Path] = []
    stubs.extend(build_template.glob("*.ads"))
    stubs.extend(build_template.glob("*.adb"))
    return sorted(stubs)


class TemplatesCommand(ContainerPassthroughCommand):
    """Run ``redo templates`` and optionally copy generated implementation stubs."""

    name: ClassVar[str] = "templates"
    help: ClassVar[str] = "Run redo templates and optionally copy generated stubs."
    redo_target: ClassVar[str] = "templates"

    def __init__(self, *, undo: bool = False) -> None:
        """Capture whether this invocation is the ``--undo`` restore path."""
        self._undo = undo

    def execute(self, context: Context) -> Result:
        """Dispatch to either the redo-then-copy flow or the ``--undo`` restore."""
        if self._undo:
            return self._restore_backup(context)
        redo_result = super().execute(context)
        if redo_result.exit_code != 0:
            return redo_result
        return self._handle_stub_copy(context)

    # ------------------------------------------------------------------
    # Copy flow
    # ------------------------------------------------------------------

    def _handle_stub_copy(self, context: Context) -> Result:
        host_cwd = (context.path if context.path is not None else Path.cwd()).resolve(strict=False)
        stubs = _find_stubs(host_cwd / "build" / "template")
        if not stubs:
            context.output.info("No implementation stubs found; nothing to copy.")
            return Result(exit_code=0)
        self._print_found(context, stubs, host_cwd)
        if not self._should_copy(context):
            if context.noninteractive:
                context.output.info(
                    "Skipped stub copy (ADMT_NONINTERACTIVE without --yes). "
                    "Pass --yes to copy, or copy from build/template/ manually."
                )
            else:
                context.output.info("Skipped stub copy.")
            return Result(exit_code=0)
        backup_dir, backed_up = self._backup_existing(host_cwd, stubs)
        copied = self._copy_stubs(stubs, host_cwd)
        self._report(context, backup_dir, backed_up, copied)
        return Result(exit_code=0, files_created=copied, files_modified=backed_up)

    @staticmethod
    def _print_found(context: Context, stubs: list[Path], host_cwd: Path) -> None:
        """Print stub paths relative to host_cwd so they are copy-pastable.

        Absolute paths from ``glob`` are noisy and machine-specific. Relative
        paths (e.g., ``build/template/component-foo-implementation.ads``)
        resolve correctly when the user copy-pastes them from the cwd they
        ran ``admt templates`` in.

        ``_find_stubs`` only returns paths from ``host_cwd.glob(...)``, so
        ``relative_to(host_cwd)`` is guaranteed to succeed -- no fallback.
        """
        context.output.info("Generated stubs found:")
        for stub in stubs:
            context.output.info(f"  {stub.relative_to(host_cwd)}")

    @staticmethod
    def _should_copy(context: Context) -> bool:
        """Decide whether to proceed with the copy, honoring flags.

        ``--force`` and ``--yes`` both copy: the prompt's default is Yes,
        and ``--yes`` accepts defaults. ``ADMT_NONINTERACTIVE`` without an
        explicit opt-in skips the copy -- prompting is impossible there,
        and the redo run itself is the useful side effect for scripted
        contexts.
        """
        if context.force or context.yes:
            return True
        if context.noninteractive:
            return False
        return context.output.prompt("Copy implementation stubs to source directory?", default=True)

    def _backup_existing(self, host_cwd: Path, stubs: list[Path]) -> tuple[Path | None, list[Path]]:
        """Copy any pre-existing destination files to a fresh ``admt-backup-*`` dir."""
        existing = [host_cwd / stub.name for stub in stubs if (host_cwd / stub.name).exists()]
        if not existing:
            return None, []
        backup_dir = Path(tempfile.mkdtemp(prefix="admt-backup-"))
        for src in existing:
            shutil.copy2(src, backup_dir / src.name)
        self._write_manifest(backup_dir, host_cwd, [p.name for p in existing])
        marker = _latest_backup_marker()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{backup_dir}\n")
        return backup_dir, existing

    @staticmethod
    def _write_manifest(backup_dir: Path, source_dir: Path, filenames: list[str]) -> None:
        manifest = {"source_dir": str(source_dir), "files": filenames}
        (backup_dir / _MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")

    @staticmethod
    def _copy_stubs(stubs: list[Path], host_cwd: Path) -> list[Path]:
        copied: list[Path] = []
        for stub in stubs:
            dest = host_cwd / stub.name
            shutil.copy2(stub, dest)
            copied.append(dest)
        return copied

    @staticmethod
    def _report(
        context: Context,
        backup_dir: Path | None,
        backed_up: list[Path],
        copied: list[Path],
    ) -> None:
        if backup_dir is not None:
            context.output.info(f"Backed up existing files to {backup_dir}/")
            for path in backed_up:
                context.output.info(f"  {path.name}")
        context.output.success(f"Copied {len(copied)} file(s).")

    # ------------------------------------------------------------------
    # Undo flow
    # ------------------------------------------------------------------

    def _restore_backup(self, context: Context) -> Result:
        marker = _latest_backup_marker()
        if not marker.exists():
            msg = "No template backup found. Nothing to undo."
            raise ArgumentError(msg)
        backup_dir = Path(marker.read_text().strip())
        if not backup_dir.is_dir():
            msg = f"Backup directory {backup_dir} is missing. Nothing to undo."
            raise ArgumentError(msg)
        manifest_path = backup_dir / _MANIFEST_NAME
        if not manifest_path.exists():
            msg = f"Backup at {backup_dir} is missing its manifest."
            raise ArgumentError(msg)
        manifest = json.loads(manifest_path.read_text())
        source_dir = Path(manifest["source_dir"])
        restored: list[Path] = []
        for name in manifest["files"]:
            src = backup_dir / name
            dest = source_dir / name
            shutil.copy2(src, dest)
            restored.append(dest)
        context.output.info(f"Restored from {backup_dir}/:")
        for path in restored:
            context.output.info(f"  {path.name}")
        return Result(exit_code=0, files_modified=restored)
