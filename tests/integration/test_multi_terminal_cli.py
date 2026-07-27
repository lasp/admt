"""Multi-session workflow tests through the real CLI.

The per-session behavior regressed once because unit tests exercised
``ConfigService`` directly while nothing replayed a real user workflow through
the CLI across several sessions (a terminal that only ever ran ``env list``
followed every later ``env use`` from other terminals). These tests simulate
terminals by patching ``admt.services.config._current_terminal`` -- and
headless sessions by clearing it and setting ``ADMT_SESSION_KEY`` -- driving
everything through ``CliRunner``: full arg parsing, command dispatch, and the
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
    # Setup: register both, then make wta the global from a setup terminal
    # (only a terminal's `env use` moves the global default).
    with _terminal(None):
        assert runner.invoke(cli, ["env", "init", str(root_a)], env=_env(tmp_path)).exit_code == 0
        assert runner.invoke(cli, ["env", "init", str(root_b)], env=_env(tmp_path)).exit_code == 0
    with _terminal("/dev/ttysSetup"):
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


# ----- headless sessions (ADMT_SESSION_KEY) -----


def _agent_env(tmp_path: Path, key: str) -> dict[str, str]:
    """Env for a headless caller identifying itself with a session key."""
    return {**_env(tmp_path), "ADMT_SESSION_KEY": key}


def test_concurrent_headless_sessions_hold_their_own_pins(tmp_path):
    """Two agents, two keys: neither `env use` retargets the other."""
    runner = CliRunner()
    root_a = _make_project(tmp_path, "wta")
    root_b = _make_project(tmp_path, "wtb")
    with _terminal(None):
        assert runner.invoke(cli, ["env", "init", str(root_a)], env=_env(tmp_path)).exit_code == 0
        assert runner.invoke(cli, ["env", "init", str(root_b)], env=_env(tmp_path)).exit_code == 0
        # Agent one takes wta, agent two takes wtb.
        assert (
            runner.invoke(cli, ["env", "use", "wta"], env=_agent_env(tmp_path, "a1")).exit_code == 0
        )
        assert (
            runner.invoke(cli, ["env", "use", "wtb"], env=_agent_env(tmp_path, "a2")).exit_code == 0
        )
        # Each agent still resolves to its own project.
        result = runner.invoke(cli, ["env", "list"], env=_agent_env(tmp_path, "a1"))
        assert _active_marker(result.output) == "wta"
        result = runner.invoke(cli, ["env", "list"], env=_agent_env(tmp_path, "a2"))
        assert _active_marker(result.output) == "wtb"
        # Keyed `env use` never moves the global: it still names the last
        # project a terminal-or-init path activated (wtb, from its init).
        result = runner.invoke(cli, ["env", "list"], env=_env(tmp_path))
        assert _active_marker(result.output) is None  # key-less: no session, no marker
        assert "active_project: wtb" in (tmp_path / ".admt" / "config.yml").read_text()


def test_headless_session_reports_its_source(tmp_path):
    runner = CliRunner()
    root_a = _make_project(tmp_path, "wta")
    with _terminal(None):
        assert runner.invoke(cli, ["env", "init", str(root_a)], env=_env(tmp_path)).exit_code == 0
        assert (
            runner.invoke(cli, ["env", "use", "wta"], env=_agent_env(tmp_path, "a1")).exit_code == 0
        )
        result = runner.invoke(cli, ["env", "list"], env=_agent_env(tmp_path, "a1"))
        assert _active_marker(result.output) == "wta"
        assert "session:a1" in (tmp_path / ".admt" / "sessions.yml").read_text()


def test_harness_session_id_pins_with_zero_configuration(tmp_path):
    """A recognized harness id (Claude Code) keys the session with no exports."""
    runner = CliRunner()
    root_a = _make_project(tmp_path, "wta")
    root_b = _make_project(tmp_path, "wtb")
    harness = {**_env(tmp_path), "CLAUDE_CODE_SESSION_ID": "16c716de-uuid"}
    with _terminal(None):
        assert runner.invoke(cli, ["env", "init", str(root_a)], env=_env(tmp_path)).exit_code == 0
        assert runner.invoke(cli, ["env", "init", str(root_b)], env=_env(tmp_path)).exit_code == 0
        assert runner.invoke(cli, ["env", "use", "wta"], env=harness).exit_code == 0
        result = runner.invoke(cli, ["env", "list"], env=harness)
        assert _active_marker(result.output) == "wta"
        assert "session:16c716de-uuid" in (tmp_path / ".admt" / "sessions.yml").read_text()
        # The global default still names the last init (wtb): untouched.
        assert "active_project: wtb" in (tmp_path / ".admt" / "config.yml").read_text()


def test_keyless_headless_env_use_is_refused(tmp_path):
    """No tty, no key: `env use` errors with both remedies instead of racing
    the shared global (exit 2, ConfigError).
    """
    runner = CliRunner()
    root_a = _make_project(tmp_path, "wta")
    with _terminal(None):
        assert runner.invoke(cli, ["env", "init", str(root_a)], env=_env(tmp_path)).exit_code == 0
        result = runner.invoke(cli, ["env", "use", "wta"], env=_env(tmp_path))
        expected_exit = 2
        assert result.exit_code == expected_exit, result.output
        assert "No tty and no session key" in result.output
        assert "ADMT_ENV=<project>" in result.output
        assert "ADMT_SESSION_KEY" in result.output


def test_terminal_pin_unaffected_by_exported_session_key(tmp_path):
    """A tty outranks the variable: an interactive shell stays tty-keyed."""
    runner = CliRunner()
    root_a = _make_project(tmp_path, "wta")
    root_b = _make_project(tmp_path, "wtb")
    with _terminal(None):
        assert runner.invoke(cli, ["env", "init", str(root_a)], env=_env(tmp_path)).exit_code == 0
        assert runner.invoke(cli, ["env", "init", str(root_b)], env=_env(tmp_path)).exit_code == 0
    with _terminal("/dev/ttysA"):
        env = _agent_env(tmp_path, "a1")
        assert runner.invoke(cli, ["env", "use", "wta"], env=env).exit_code == 0
        # The pin landed under the tty, not the key.
        stored = (tmp_path / ".admt" / "sessions.yml").read_text()
        assert "/dev/ttysA" in stored
        assert "session:a1" not in stored
    # Another terminal moves the global; the a1-keyed headless session is
    # unpinned (its earlier `use` landed under the tty) -> inherits it.
    with _terminal("/dev/ttysB"):
        assert runner.invoke(cli, ["env", "use", "wtb"], env=_env(tmp_path)).exit_code == 0
    with _terminal(None):
        result = runner.invoke(cli, ["env", "list"], env=_agent_env(tmp_path, "a1"))
        assert _active_marker(result.output) == "wtb"
    # The terminal keeps its own pin.
    with _terminal("/dev/ttysA"):
        result = runner.invoke(cli, ["env", "list"], env=_agent_env(tmp_path, "a1"))
        assert _active_marker(result.output) == "wta"
