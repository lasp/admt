"""Multi-terminal workflow tests through the real CLI.

The per-terminal session behavior regressed once because unit tests exercised
``ConfigService`` directly while nothing replayed a real user workflow through
the CLI across several terminals (a terminal that only ever ran ``env list``
followed every later ``env use`` from other terminals). These tests simulate
terminals by patching ``admt.services.config._current_terminal`` and drive
everything through ``CliRunner`` -- full arg parsing, command dispatch, and the
real ConfigService against a synthetic ``~/.admt``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from textwrap import dedent
from typing import TYPE_CHECKING
from unittest.mock import patch

from click.testing import CliRunner

from admt.cli import cli

if TYPE_CHECKING:
    from pathlib import Path

COMPOSE_TEMPLATE = dedent(
    """\
    name: {name}
    services:
      {name}:
        container_name: {name}_container
        volumes:
          - type: bind
            source: ../../adamant
            target: /home/user/adamant
          - type: bind
            source: ../../{name}
            target: /home/user/{name}
    """
)


def _make_project(base: Path, name: str) -> Path:
    root = base / name
    (root / "docker").mkdir(parents=True)
    (root / "env").mkdir(parents=True)
    (root / "default.do").touch()
    (root / "env" / "activate").touch()
    (root / "docker" / "docker-compose.yml").write_text(COMPOSE_TEMPLATE.format(name=name))
    (base / "adamant").mkdir(exist_ok=True)
    return root


def _env(tmp_path: Path) -> dict[str, str]:
    return {"HOME": str(tmp_path)}


@contextmanager
def _terminal(tty: str | None):
    """Simulate running subsequent CLI calls from a given terminal (or none).

    The sid is this test process's live PID so the prune-on-write liveness
    check keeps the simulated terminals' entries alive.
    """
    value = (tty, os.getpid()) if tty is not None else None
    with patch("admt.services.config._current_terminal", lambda: value):
        yield


def _active_marker(output: str) -> str | None:
    """Return the project name marked active ('*') in ``env list`` output."""
    for line in output.splitlines():
        if line.startswith("*"):
            return line.split()[1]
    return None


def test_env_list_only_terminal_keeps_its_project(tmp_path):
    """The user-reported leak, end to end: terminal A only ever runs `env list`;
    terminal B's later `env use` must not move A.
    """
    runner = CliRunner()
    root_a = _make_project(tmp_path, "wta")
    root_b = _make_project(tmp_path, "wtb")
    # Setup from a no-terminal context: register both, make wta the global.
    with _terminal(None):
        assert runner.invoke(cli, ["env", "init", str(root_a)], env=_env(tmp_path)).exit_code == 0
        assert runner.invoke(cli, ["env", "init", str(root_b)], env=_env(tmp_path)).exit_code == 0
        assert runner.invoke(cli, ["env", "use", "wta"], env=_env(tmp_path)).exit_code == 0
    # Terminal A: only ever lists. Sees wta; this is a commitment.
    with _terminal("/dev/ttysA"):
        result = runner.invoke(cli, ["env", "list"], env=_env(tmp_path))
        assert _active_marker(result.output) == "wta"
    # Terminal B: switches to wtb (moves the global).
    with _terminal("/dev/ttysB"):
        assert runner.invoke(cli, ["env", "use", "wtb"], env=_env(tmp_path)).exit_code == 0
        result = runner.invoke(cli, ["env", "list"], env=_env(tmp_path))
        assert _active_marker(result.output) == "wtb"
    # Terminal A again: must still be wta.
    with _terminal("/dev/ttysA"):
        result = runner.invoke(cli, ["env", "list"], env=_env(tmp_path))
        assert _active_marker(result.output) == "wta"
    # A brand-new terminal inherits the last-used global (wtb).
    with _terminal("/dev/ttysC"):
        result = runner.invoke(cli, ["env", "list"], env=_env(tmp_path))
        assert _active_marker(result.output) == "wtb"


def test_env_init_repins_the_registering_terminal(tmp_path):
    """`env init` prints 'Active project: <new>'; the terminal must follow it
    even when previously pinned elsewhere.
    """
    runner = CliRunner()
    root_a = _make_project(tmp_path, "wta")
    root_c = _make_project(tmp_path, "wtc")
    with _terminal("/dev/ttysA"):
        assert runner.invoke(cli, ["env", "init", str(root_a)], env=_env(tmp_path)).exit_code == 0
        assert runner.invoke(cli, ["env", "use", "wta"], env=_env(tmp_path)).exit_code == 0
        result = runner.invoke(cli, ["env", "init", str(root_c)], env=_env(tmp_path))
        assert result.exit_code == 0
        assert "Active project: wtc" in result.output
        # The terminal's next resolution must agree with what init just said.
        result = runner.invoke(cli, ["env", "list"], env=_env(tmp_path))
        assert _active_marker(result.output) == "wtc"


def test_admt_env_override_does_not_disturb_terminal_pin(tmp_path):
    """A one-shot ADMT_ENV override is ephemeral: the pin survives it."""
    runner = CliRunner()
    root_a = _make_project(tmp_path, "wta")
    root_b = _make_project(tmp_path, "wtb")
    with _terminal("/dev/ttysA"):
        assert runner.invoke(cli, ["env", "init", str(root_a)], env=_env(tmp_path)).exit_code == 0
        assert runner.invoke(cli, ["env", "init", str(root_b)], env=_env(tmp_path)).exit_code == 0
        assert runner.invoke(cli, ["env", "use", "wta"], env=_env(tmp_path)).exit_code == 0
        env = {**_env(tmp_path), "ADMT_ENV": "wtb"}
        result = runner.invoke(cli, ["env", "list"], env=env)
        assert _active_marker(result.output) == "wtb"  # override wins for this call
        result = runner.invoke(cli, ["env", "list"], env=_env(tmp_path))
        assert _active_marker(result.output) == "wta"  # pin untouched
