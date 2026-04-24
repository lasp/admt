"""Base classes for admt commands.

``Command`` is the fundamental unit of extensibility: every admt operation
is a subclass that declares its metadata and implements ``execute``.
``ContainerPassthroughCommand`` is the base for every command that forwards
a redo target into the container -- build/what/test/style/analyze/clean/
prove/coverage/publish all share the same flow.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from admt.adapters.redo import RedoAdapter
from admt.context import Result
from admt.exceptions import ContainerError

if TYPE_CHECKING:
    from admt.context import Context


class Command(ABC):
    """Base class for all admt commands.

    Every concrete subclass must declare ``name``, ``help``, and
    ``requires_project`` as class attributes. Contract tests enforce this.
    ``requires_container`` defaults to ``False``; commands that need a
    live ``ContainerService`` on ``context`` set it to ``True`` so the CLI
    adapter can wire one before ``execute`` runs.
    """

    name: ClassVar[str]
    help: ClassVar[str]
    requires_project: ClassVar[bool]
    requires_container: ClassVar[bool] = False

    @abstractmethod
    def execute(self, context: Context) -> Result:
        """Run the command and return a structured result."""


class ContainerPassthroughCommand(Command):
    """Base for commands that forward a redo target into the container.

    The standard flow:

    1. ``Context.resolve_container_path`` maps the host cwd (or the
       explicit ``context.path``) to the container-side path.
    2. ``resolve_target(context)`` selects the redo target (defaults to
       ``redo_target``; ``--all`` subclasses override).
    3. ``RedoAdapter.build_command`` assembles ``cd <path> && [DEBUG=1
       ]redo <target>``.
    4. ``ContainerService.exec`` ensures the container is running and
       the env snapshot is materialized, then forwards through the proxy
       with ``merge_stderr=True`` (redo writes human output to stderr;
       admt translates it to stdout) and ``capture_output=context.quiet``
       (quiet mode suppresses output on success, emits captured output
       on failure).
    """

    requires_project: ClassVar[bool] = True
    requires_container: ClassVar[bool] = True
    redo_target: ClassVar[str] = ""
    supports_all: ClassVar[bool] = False

    def resolve_target(self, context: Context) -> str:
        """Return the redo target for this command.

        Commands that set ``supports_all = True`` pick up ``--all``/``-a``
        automatically: ``context.run_all`` switches the target from
        ``<target>`` to ``<target>_all`` (e.g., ``test`` -> ``test_all``).
        """
        target = self.redo_target
        if self.supports_all and context.run_all:
            target = f"{target}_all"
        return target

    def execute(self, context: Context) -> Result:
        """Map path, assemble redo command, forward through the container."""
        if context.container_service is None:
            msg = "ContainerService was not wired for this command (CLI bug)."
            raise ContainerError(msg)
        container_path = context.resolve_container_path()
        target = self.resolve_target(context)
        redo_cmd = RedoAdapter.build_command(target, cwd=container_path, debug=context.debug)
        exit_code = context.container_service.exec(
            redo_cmd,
            interactive=False,
            merge_stderr=True,
            capture_output=context.quiet,
        )
        return Result(exit_code=exit_code)
