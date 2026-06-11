"""Integration tests for ``admt env init`` and ``admt env use``.

Uses Click's ``CliRunner`` with real ConfigService/OutputService/YamlAdapter
and synthetic project trees under ``tmp_path``. Redirects ``~/.admt`` via
the ``HOME`` environment variable.
"""

from __future__ import annotations

import os
import subprocess
from textwrap import dedent
from typing import TYPE_CHECKING

from click.testing import CliRunner

from admt.cli import cli
from admt.exceptions import ArgumentError

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_COMPOSE = dedent(
    """\
    name: myproj
    services:
      myproj:
        container_name: myproj_container
        volumes:
          - type: bind
            source: ../../adamant
            target: /home/user/adamant
          - type: bind
            source: ../../myproj
            target: /home/user/myproj
    """
)


def _make_project(base: Path, *, name: str = "myproj", compose: str | None = None):
    root = base / name
    (root / "docker").mkdir(parents=True)
    (root / "env").mkdir(parents=True)
    (root / "default.do").touch()
    (root / "env" / "activate").touch()
    content = compose if compose is not None else DEFAULT_COMPOSE.replace("myproj", name)
    (root / "docker" / "docker-compose.yml").write_text(content)
    (base / "adamant").mkdir(exist_ok=True)
    return root


def _env(tmp_path: Path, **overrides) -> dict[str, str]:
    env = {"HOME": str(tmp_path)}
    env.update({k: str(v) for k, v in overrides.items()})
    return env


def test_env_init_registers_project_and_creates_config(tmp_path):
    root = _make_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["env", "init", str(root)], env=_env(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Registered project 'myproj'" in result.output
    assert (tmp_path / ".admt" / "config.yml").exists()


def test_env_init_via_cwd_when_no_path(tmp_path, monkeypatch):
    root = _make_project(tmp_path)
    monkeypatch.chdir(root)
    runner = CliRunner()
    result = runner.invoke(cli, ["env", "init"], env=_env(tmp_path))
    assert result.exit_code == 0, result.output


def test_env_init_missing_markers_errors(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli, ["env", "init", str(bare)], env=_env(tmp_path))
    # Missing project-root markers is an argument-shape error (exit 3),
    # not an environment failure (exit 2): the user pointed admt at a
    # path that doesn't look like an Adamant project.
    assert result.exit_code == ArgumentError.exit_code
    assert "default.do" in result.output
    assert "env/activate" in result.output


def test_env_init_multi_compose_noninteractive_errors(tmp_path):
    root = _make_project(tmp_path)
    (root / "docker" / "alternate.yaml").write_text(DEFAULT_COMPOSE)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["env", "init", str(root)],
        env=_env(tmp_path, ADMT_NONINTERACTIVE="1"),
    )
    assert result.exit_code == ArgumentError.exit_code
    assert "ADMT_NONINTERACTIVE" in result.output


def test_env_init_multi_compose_interactive_selects(tmp_path):
    root = _make_project(tmp_path)
    (root / "docker" / "alternate.yaml").write_text(DEFAULT_COMPOSE)
    runner = CliRunner()
    # Two sorted files: alternate.yaml, docker-compose.yml. Choose 2.
    result = runner.invoke(cli, ["env", "init", str(root)], env=_env(tmp_path), input="2\n")
    assert result.exit_code == 0, result.output


def test_env_init_re_register_interactive_declines_exits_zero(tmp_path):
    root = _make_project(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ["env", "init", str(root)], env=_env(tmp_path))
    # Re-register; default prompt is No, press Enter to decline.
    result = runner.invoke(cli, ["env", "init", str(root)], env=_env(tmp_path), input="\n")
    assert result.exit_code == 0
    assert "not overwriting" in result.output


def test_env_init_re_register_with_force_overwrites(tmp_path):
    root = _make_project(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ["env", "init", str(root)], env=_env(tmp_path))
    # Modify compose file so a change is observable after --force re-register.
    (root / "docker" / "docker-compose.yml").write_text(
        DEFAULT_COMPOSE.replace("myproj_container", "myproj_renamed")
    )
    result = runner.invoke(cli, ["-f", "env", "init", str(root)], env=_env(tmp_path))
    assert result.exit_code == 0
    # Read config directly to verify update.
    config_file = (tmp_path / ".admt" / "config.yml").read_text()
    assert "myproj_renamed" in config_file


def test_env_init_re_register_with_yes_declines(tmp_path):
    root = _make_project(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ["env", "init", str(root)], env=_env(tmp_path))
    # --yes accepts the default (No) so overwrite is declined.
    result = runner.invoke(cli, ["-y", "env", "init", str(root)], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "not overwriting" in result.output


def test_env_init_re_register_noninteractive_errors(tmp_path):
    root = _make_project(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ["env", "init", str(root)], env=_env(tmp_path))
    result = runner.invoke(
        cli,
        ["env", "init", str(root)],
        env=_env(tmp_path, ADMT_NONINTERACTIVE="1"),
    )
    assert result.exit_code == ArgumentError.exit_code
    assert "--force" in result.output


def test_env_init_noninteractive_with_force_succeeds(tmp_path):
    root = _make_project(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ["env", "init", str(root)], env=_env(tmp_path))
    result = runner.invoke(
        cli,
        ["-f", "env", "init", str(root)],
        env=_env(tmp_path, ADMT_NONINTERACTIVE="1"),
    )
    assert result.exit_code == 0


def test_env_use_switches_active_project(tmp_path):
    root_a = _make_project(tmp_path, name="proj_a")
    alt = tmp_path / "alt"
    alt.mkdir()
    root_b = _make_project(alt, name="proj_b")
    runner = CliRunner()
    runner.invoke(cli, ["env", "init", str(root_a)], env=_env(tmp_path))
    runner.invoke(cli, ["env", "init", str(root_b)], env=_env(tmp_path))
    result = runner.invoke(cli, ["env", "use", "proj_a"], env=_env(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Active project: proj_a" in result.output


def test_env_use_unknown_project_errors(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["env", "use", "ghost"], env=_env(tmp_path))
    # Unknown project name is an argument-shape error (exit 3): the user
    # passed a name that isn't in the registry, not an environment
    # failure (exit 2, which means "registry missing or unreadable").
    assert result.exit_code == ArgumentError.exit_code
    assert "ghost" in result.output


def test_e_alias_resolves_to_env_group(tmp_path):
    root = _make_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["e", "init", str(root)], env=_env(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Registered project 'myproj'" in result.output


def test_admt_env_override_is_not_a_prompt(tmp_path):
    # ADMT_ENV is consumed by get_active_project(); no Phase 1 command calls
    # that path, so this test just confirms setting the var does not break
    # env init / env use. (Full semantics are exercised in Phase 3.)
    root = _make_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["env", "init", str(root)], env=_env(tmp_path, ADMT_ENV="myproj"))
    assert result.exit_code == 0


def test_env_init_quiet_suppresses_success_output(tmp_path):
    root = _make_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["-q", "env", "init", str(root)], env=_env(tmp_path))
    assert result.exit_code == 0
    assert "Registered project" not in result.output
    assert "Active project" not in result.output


def test_env_init_subprocess_invocation(tmp_path):
    """Shell-level round trip -- catches packaging/entry-point regressions."""
    root = _make_project(tmp_path)
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"]}
    # Fixed argv (no shell), no untrusted input; uv on PATH is the exact
    # entry-point we want to exercise.
    result = subprocess.run(  # noqa: S603
        ["uv", "run", "admt", "env", "init", str(root)],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=str(root),
        timeout=60,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "Registered project 'myproj'" in result.stdout
