"""``admt what`` -- forward to ``redo what`` (list buildable targets)."""

from __future__ import annotations

from typing import ClassVar

from admt.commands.base import ContainerPassthroughCommand


class WhatCommand(ContainerPassthroughCommand):
    """List buildable targets via ``redo what``."""

    name: ClassVar[str] = "what"
    help: ClassVar[str] = "List buildable targets (redo what)."
    redo_target: ClassVar[str] = "what"
