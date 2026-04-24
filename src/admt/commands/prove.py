"""``admt prove`` -- forward to ``redo prove`` (SPARK formal verification)."""

from __future__ import annotations

from typing import ClassVar

from admt.commands.base import ContainerPassthroughCommand


class ProveCommand(ContainerPassthroughCommand):
    """Run SPARK proofs via ``redo prove``."""

    name: ClassVar[str] = "prove"
    help: ClassVar[str] = "Run SPARK formal verification (redo prove)."
    redo_target: ClassVar[str] = "prove"
