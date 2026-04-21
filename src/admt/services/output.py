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
_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RED = "\033[31m"
_ANSI_DIM = "\033[2m"


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
        """Print a green success message to stdout (suppressed when ``--quiet``)."""
        if self._quiet:
            return
        self._write(sys.stdout, self._colorize(sys.stdout, _ANSI_GREEN, message))

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
        stream.write(message + "\n")

    @staticmethod
    def _colorize(stream: TextIO, code: str, message: str) -> str:
        """Wrap ``message`` in ANSI ``code``/reset when ``stream`` is a color-friendly TTY."""
        if os.environ.get("NO_COLOR"):
            return message
        if not stream.isatty():
            return message
        return f"{code}{message}{_ANSI_RESET}"
