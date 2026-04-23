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
from admt.adapters.redo_output import rewrite_line_terse, split_verb
from admt.context import Result
from admt.exceptions import ContainerError

if TYPE_CHECKING:
    from admt.adapters.docker import LineTransform
    from admt.context import Context
    from admt.services.output import OutputService


def _colored_streaming_transform(output: OutputService) -> LineTransform:
    """Build a streaming transform that tints the admt verb gold.

    Lines that ``rewrite_line_terse`` actually rewrote (``redo    foo`` ->
    ``build foo``) get their verb head tinted via ``output.admt``; the
    target and any trailing content stay in the terminal's default color
    so paths remain readable and compiler-style tool output isn't
    overwritten. Pass-through lines (rewrite returned the input unchanged)
    are forwarded verbatim -- they carry their own ANSI and aren't
    admt's words.
    """

    def transform(raw: str) -> str | None:
        result = rewrite_line_terse(raw)
        if result is None:
            return None
        if result.rstrip() == raw.rstrip():
            return result
        verb, rest = split_verb(result)
        # Rewrites always produce one of the known verbs; ``split_verb``
        # returns the tail as ``rest``. Tint the verb, keep the rest plain.
        return output.admt(verb) + rest

    return transform


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
    # Gerund for a static "admt <status_verb>..." line printed before
    # long-running commands so the user sees admt has started. ``None``
    # (the default) skips the line -- used for ``what`` (fast, output
    # speaks for itself) and ``templates`` (has its own output flow).
    status_verb: ClassVar[str | None] = None

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
        """Map path, assemble redo command, forward through the container.

        Streaming mode threads ``rewrite_line_terse`` as the per-line
        transform so redo's progress output (``redo    build/src/foo.adb``)
        is rewritten to ``build build/src/foo.adb`` as it arrives -- the
        ``admt `` prefix is dropped because the user already typed the verb.
        In quiet mode the transform is omitted; captured output stays raw
        and is only shown on failure via ``emit_captured``.

        ``status_verb`` frames the streaming output on success: ``<verb>...``
        goes out before exec starts, then ``done.`` after exec returns 0.
        On failure the closing line is suppressed -- the adapter's
        ``Failed (exit N): <cmd>`` diagnostic is the signal there.
        """
        if context.container_service is None:
            msg = "ContainerService was not wired for this command (CLI bug)."
            raise ContainerError(msg)
        container_path = context.resolve_container_path()
        target = self.resolve_target(context)
        if self.status_verb:
            context.output.info(context.output.admt(f"{self.status_verb}..."))
        redo_cmd = RedoAdapter.build_command(target, cwd=container_path, debug=context.debug)
        exit_code = context.container_service.exec(
            redo_cmd,
            interactive=False,
            merge_stderr=True,
            capture_output=context.quiet,
            line_transform=None if context.quiet else _colored_streaming_transform(context.output),
        )
        if exit_code == 0 and self.status_verb:
            context.output.info(context.output.admt("done."))
        return Result(exit_code=exit_code)
