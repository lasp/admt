"""``admt publish`` -- forward to ``redo publish`` (``--all`` -> ``publish_all``)."""

from __future__ import annotations

from typing import ClassVar

from admt.commands.base import ContainerPassthroughCommand


class PublishCommand(ContainerPassthroughCommand):
    """Publish build artifacts via ``redo publish``."""

    name: ClassVar[str] = "publish"
    help: ClassVar[str] = "Publish build artifacts (redo publish; --all for publish_all)."
    redo_target: ClassVar[str] = "publish"
    supports_all: ClassVar[bool] = True
    status_verb: ClassVar[str | None] = "publishing"
