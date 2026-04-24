"""Output service -- user-facing messages, prompts, choices, color, command echo.

``info``/``success`` route to stdout (suppressed by ``--quiet``),
``warning``/``error`` route to stderr, ``prompt``/``choose`` handle the
``--yes`` and ``ADMT_NONINTERACTIVE`` semantics, ``command_echo`` emits
``$ <cmd>`` lines when verbose, and success/warning/error wrap their
messages in ANSI color codes when the target stream is a TTY and
``NO_COLOR`` is unset (per https://no-color.org).
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from admt.exceptions import ArgumentError

if TYPE_CHECKING:
    from typing import TextIO


_ANSI_RESET = "\033[0m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RED = "\033[31m"
_ANSI_DIM = "\033[2m"
# admt's signature color: pale gold #CFB87C (24-bit truecolor RGB
# 207/184/124). Reads as a warm muted gold across common light and dark
# terminal themes without the saturation that makes plain ``\033[33m``
# or 256-color index 220 feel aggressive on long multi-line output.
# Used to mark output originated from admt itself (verbs, status
# framing, ``what`` listing) or relayed through admt (redo's multi-word
# status lines). Requires truecolor terminal support, which every
# modern terminal (xterm-256color-era and beyond) has.
_ANSI_GOLD_FG = "\033[38;2;207;184;124m"
# admt's emphatic styling = bold + gold. Emitted as two separate SGR
# sequences because some terminals strip the ``1`` (bold) attribute
# when it's combined with a 24-bit RGB color in a single
# ``\033[1;38;2;...m``; separating them guarantees the weight is
# applied regardless.
_ANSI_GOLD = "\033[1m" + _ANSI_GOLD_FG


class OutputService:
    """Routes CLI output and centralizes prompt/choice behavior."""

    def __init__(
        self,
        *,
        verbose: bool,
        quiet: bool,
        yes: bool,
        noninteractive: bool,
    ) -> None:
        """Capture the global flag state that drives output behavior."""
        self._verbose = verbose
        self._quiet = quiet
        self._yes = yes
        self._noninteractive = noninteractive

    @property
    def verbose(self) -> bool:
        """Whether the caller requested verbose output."""
        return self._verbose

    @property
    def quiet(self) -> bool:
        """Whether the caller requested quiet output."""
        return self._quiet

    @property
    def yes(self) -> bool:
        """Whether ``--yes`` was passed."""
        return self._yes

    @property
    def noninteractive(self) -> bool:
        """Whether ``ADMT_NONINTERACTIVE`` is active."""
        return self._noninteractive

    def info(self, message: str) -> None:
        """Print an informational message to stdout (suppressed when ``--quiet``)."""
        if self._quiet:
            return
        self._write(sys.stdout, message)

    def success(self, message: str) -> None:
        """Print a gold (non-bold) success message to stdout (suppressed when ``--quiet``).

        Admt's voice is gold; success announcements share the non-bold tier
        with closing ``done.`` lines and redo-relayed status phases --
        admt-authored text that isn't an emphatic verb or opening framing.
        """
        if self._quiet:
            return
        self._write(sys.stdout, self._colorize(sys.stdout, _ANSI_GOLD_FG, message))

    def warning(self, message: str) -> None:
        """Print a yellow warning message to stderr."""
        self._write(sys.stderr, self._colorize(sys.stderr, _ANSI_YELLOW, message))

    def error(self, message: str) -> None:
        """Print a red error message to stderr."""
        self._write(sys.stderr, self._colorize(sys.stderr, _ANSI_RED, message))

    def command_echo(self, command: str) -> None:
        """Print ``$ <command>`` to stdout when verbose mode is active.

        Intentionally ignores ``--quiet``: per ARCHITECTURE §TTY, ``-v -q``
        together means "print the underlying commands but suppress their
        output." The subprocess output is captured separately in capture
        mode -- the command echo is admt's own diagnostic.
        """
        if not self._verbose:
            return
        self._write(sys.stdout, self._colorize(sys.stdout, _ANSI_DIM, f"$ {command}"))

    def admt(self, message: str, *, bold: bool = True) -> str:
        """Wrap ``message`` in admt's signature gold when color is enabled.

        ``bold=True`` (default) applies bold + gold -- the full signature
        styling for admt verbs (``build`` in ``build foo.adb``), opening
        status (``building...``), and closing ``done.``.

        ``bold=False`` applies gold only, no bold weight. Used for
        admt-relayed tool messages like redo's multi-word status lines
        (``Compiling 13 objects...``) -- admt-originated in presentation
        (we forward them as the admt view of what's happening) but
        without the weight of a verb.

        Returns the plain message unchanged when stdout is not a TTY or
        ``NO_COLOR`` is set, so callers can wrap unconditionally.
        """
        code = _ANSI_GOLD if bold else _ANSI_GOLD_FG
        return self._colorize(sys.stdout, code, message)

    def emit_captured(self, content: str, *, to_stderr: bool = False) -> None:
        """Emit captured subprocess output verbatim, bypassing ``--quiet``.

        Used by the passthrough commands so that a failed redo invocation in
        ``--quiet`` mode still shows the captured output (otherwise the user
        sees only an exit code with no explanation).
        """
        if not content:
            return
        stream = sys.stderr if to_stderr else sys.stdout
        stream.write(content)
        if not content.endswith("\n"):
            stream.write("\n")

    def prompt(self, message: str, *, default: bool | None = True) -> bool:
        """Ask a yes/no question; return the answer.

        Honors ``--yes`` (auto-accept the default when one exists) and
        ``ADMT_NONINTERACTIVE`` (raise instead of prompting).

        Args:
            message: The question text, without a trailing suffix.
            default: The value returned when the user just presses Enter.
                ``None`` means there is no sensible default -- the user
                must type ``y`` or ``n``, and ``--yes`` cannot auto-accept.

        Returns:
            ``True`` for yes, ``False`` for no.

        Raises:
            ArgumentError: When ``ADMT_NONINTERACTIVE`` is set.
        """
        self._refuse_if_noninteractive(message)
        if self._yes and default is not None:
            return default
        suffix = {True: " [Y/n] ", False: " [y/N] ", None: " [y/n] "}[default]
        answer = input(message + suffix).strip().lower()
        if not answer and default is not None:
            return default
        return answer in ("y", "yes")

    def choose(self, message: str, choices: list[str]) -> str:
        """Ask the user to pick one item from ``choices`` and return it.

        A single-item list is returned directly -- no prompt is shown. When
        ``ADMT_NONINTERACTIVE`` is set, this always raises, since there is
        no meaningful default for an arbitrary list.
        """
        if not choices:
            msg = "choose() requires at least one option"
            raise ArgumentError(msg)
        if len(choices) == 1:
            return choices[0]
        self._refuse_if_noninteractive(message)
        self._write(sys.stdout, message)
        for idx, item in enumerate(choices, start=1):
            self._write(sys.stdout, f"  [{idx}] {item}")
        while True:
            raw = input("Select: ").strip()
            if raw.isdigit():
                n = int(raw)
                if 1 <= n <= len(choices):
                    return choices[n - 1]
            self._write(sys.stderr, f"Enter a number between 1 and {len(choices)}.")

    def _refuse_if_noninteractive(self, message: str) -> None:
        if self._noninteractive:
            msg = f"{message} (cannot prompt in ADMT_NONINTERACTIVE mode)"
            raise ArgumentError(msg)

    @staticmethod
    def _write(stream: TextIO, message: str) -> None:
        """Write ``message + newline`` and flush.

        Flushing matters when admt's own messages sit next to streamed
        subprocess output (e.g., the "Activating environment..." info
        printed just before we hand stdio off to ``docker exec`` sourcing
        ``env/activate``). Under block-buffered stdout (piped, redirected
        to a file, or captured by a wrapper), Python's own writes would
        otherwise stay buffered while the child process wrote directly to
        the fd, producing output in the wrong order.
        """
        stream.write(message + "\n")
        stream.flush()

    @staticmethod
    def _colorize(stream: TextIO, code: str, message: str) -> str:
        """Wrap ``message`` in ANSI ``code``/reset when ``stream`` is a color-friendly TTY."""
        if os.environ.get("NO_COLOR"):
            return message
        if not stream.isatty():
            return message
        return f"{code}{message}{_ANSI_RESET}"
