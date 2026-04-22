"""Rewrite raw ``redo`` output lines into admt-equivalent commands.

Two callers share this:

- ``admt what`` captures ``redo what`` output and prints the translated
  listing.
- Streaming passthrough commands (``admt build`` et al.) intercept redo's
  progress lines mid-build so users see admt vocabulary uniformly -- e.g.
  ``redo    build/src/foo.adb`` becomes ``admt build build/src/foo.adb``.

Redo's progress format uses indentation to signal nesting depth:
``redo  <target>`` (two spaces) is the top-level job, ``redo    <target>``
(four spaces) is the first level of dependency rebuilds, and so on. The
top-level line is dropped -- it's redundant with admt's ``<gerund>...``
status line (and with the command the user just typed in ``what``'s case).
The ``redo what`` listing uses a single space per entry; those are
transformed like any other.
"""

from __future__ import annotations

import re

# Redo's own target names -> admt CLI equivalents. Unknown targets are
# assumed to be file-ish paths (``build/src/foo.adb``, ``build/dot/x.dot``)
# and fall back to ``admt build <target>`` -- BuildCommand forwards
# arbitrary positional targets through to redo.
_TARGET_MAP: dict[str, str] = {
    "all": "admt build",
    "test": "admt test",
    "test_all": "admt test --all",
    "style": "admt style",
    "style_all": "admt style --all",
    "analyze": "admt analyze",
    "analyze_all": "admt analyze --all",
    "clean": "admt clean",
    "clean_all": "admt clean --all",
    "coverage": "admt coverage",
    "coverage_all": "admt coverage --all",
    "publish": "admt publish",
    "publish_all": "admt publish --all",
    "prove": "admt prove",
    "templates": "admt templates",
    "what": "admt what",
}

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_REDO_LINE = re.compile(r"^redo(?P<spaces> +)(?P<target>\S+)\s*$")
# Redo's top-level progress marker: ``redo  <target>`` with exactly two
# spaces. Nested dependency rebuilds use four or more spaces.
_OUTER_SPACES = 2
# Spaces after ``redo`` at the first visible nested level. Redo adds two
# spaces per level of recursion; we drop the outer marker (2), so the
# first line the user sees is at 4 spaces. Anything deeper than that
# gets a visual indent of ``spaces - _FIRST_NESTED_SPACES`` so the
# dependency tree stays readable.
_FIRST_NESTED_SPACES = 4


def rewrite_line(raw: str) -> str | None:
    """Return the admt-equivalent of ``raw``, or ``None`` to drop it.

    Semantics:

    * Lines that don't look like redo progress/listing lines return ``raw``
      unchanged (ANSI preserved -- compiler diagnostics keep their colors).
    * The top-level ``redo  <target>`` status line (exactly two spaces)
      returns ``None``. Callers should skip it.
    * ``redo what`` listings (one space) and first-level nested rebuilds
      (four spaces) return flush-left, e.g. ``admt build`` or
      ``admt build build/src/foo.adb``.
    * Deeper nested rebuilds keep their visual depth -- the rewritten line
      is prefixed with ``spaces - 4`` leading spaces so a six-level redo
      chain still looks like a six-level tree after rewriting.

    Input may or may not include a trailing newline -- callers that stream
    lines from ``Popen.stdout`` pass them through verbatim. ANSI escapes
    are stripped before pattern matching so redo's colored header is
    recognized.
    """
    clean = _ANSI_ESCAPE.sub("", raw).rstrip()
    match = _REDO_LINE.match(clean)
    if match is None:
        return raw
    spaces = len(match.group("spaces"))
    if spaces == _OUTER_SPACES:
        return None
    target = match.group("target")
    body = _TARGET_MAP.get(target, f"admt build {target}")
    indent = " " * max(0, spaces - _FIRST_NESTED_SPACES)
    return f"{indent}{body}"


def rewrite_line_terse(raw: str) -> str | None:
    """Like ``rewrite_line`` but without the leading ``admt `` prefix.

    Used by streaming passthrough commands where the user already typed
    ``admt <verb>`` on the command line -- repeating ``admt `` on every
    progress line is redundant visual noise. ``admt what`` keeps the full
    prefix via ``rewrite_line`` because its output is reference material
    that the user copy-pastes to run.

    Any leading indent produced by ``rewrite_line`` (preserved from
    redo's nested rebuild depth) is kept -- only the ``admt `` prefix that
    follows the indent is dropped. Pass-through lines don't carry the
    prefix, so ``removeprefix`` is a no-op on them.
    """
    result = rewrite_line(raw)
    if result is None:
        return None
    lstripped = result.lstrip(" ")
    indent = result[: len(result) - len(lstripped)]
    return indent + lstripped.removeprefix("admt ")
