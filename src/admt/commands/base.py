"""Base classes for admt commands.

``Command`` is the fundamental unit of extensibility: every admt operation
is a subclass that declares its metadata and implements ``execute``.
``ContainerPassthroughCommand`` is a Phase 0 stub -- its ``execute`` method
is completed in Phase 3 once the container and path-mapper services exist.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from admt.context import Context, Result


class Command(ABC):
    """Base class for all admt commands.

    Every concrete subclass must declare ``name``, ``help``, and
    ``requires_project`` as class attributes. Contract tests enforce this.
    """

    name: ClassVar[str]
    help: ClassVar[str]
    requires_project: ClassVar[bool]

    @abstractmethod
    def execute(self, context: Context) -> Result:
        """Run the command and return a structured result."""


class ContainerPassthroughCommand(Command):
    """Base for commands that forward to redo in the container.

    Phase 0 stub: ``execute`` is intentionally not implemented here. It is
    completed in Phase 3 once ``ContainerService`` and ``PathMapperService``
    are available. Individual passthrough commands subclass this and
    override ``resolve_target`` when their target differs from the default.
    """

    requires_project: ClassVar[bool] = True
    redo_target: ClassVar[str] = ""

    def resolve_target(self, context: Context) -> str:
        """Return the redo target for this command."""
        del context
        return self.redo_target
