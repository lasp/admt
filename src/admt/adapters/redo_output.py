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
    * Mapped verbs (``redo all``, ``redo test``, ``redo test_all`` etc.)
      return just the verb -- there is no target text to attach spacing to.
    * File-ish ``redo <path>`` lines fall back to ``admt build <path>``
      with the depth encoded in the separator between the verb and the
      target: one space at the first nested level, two more for each
      extra depth. The verb stays flush-left so the column of the admt
      action is stable; the target drifts right with depth so the
      dependency tree is still legible.

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
    if target in _TARGET_MAP:
        return _TARGET_MAP[target]
    # Fallback: ``admt build <target>``. Encode redo's extra depth spaces
    # in the separator so the verb is flush-left and deeper rebuilds push
    # the target right.
    extra = max(0, spaces - _FIRST_NESTED_SPACES)
    separator = " " * (1 + extra)
    return f"admt build{separator}{target}"


def rewrite_line_terse(raw: str) -> str | None:
    """Like ``rewrite_line`` but without the leading ``admt `` prefix.

    Used by streaming passthrough commands where the user already typed
    ``admt <verb>`` on the command line -- repeating ``admt `` on every
    progress line is redundant visual noise. ``admt what`` keeps the full
    prefix via ``rewrite_line`` because its output is reference material
    that the user copy-pastes to run.

    Depth-encoded spacing between verb and target (from ``rewrite_line``)
    is preserved verbatim; only the ``admt `` prefix at the head of the
    line is dropped. Pass-through lines don't carry the prefix, so
    ``removeprefix`` is a no-op on them.
    """
    result = rewrite_line(raw)
    return None if result is None else result.removeprefix("admt ")
