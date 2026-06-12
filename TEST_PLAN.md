# admt Test Plan

admt's test suite is the primary feedback loop that keeps development on rails. This document defines the testing strategy, test tiers, and quality gates that all code must pass.

admt is built primarily by AI agents. Agents are fast and capable, but they cut corners, introduce subtle bugs, and accumulate technical debt -- especially over many iterations without guardrails. The test suite is the guardrail.

---

## Table of Contents

- [Principles](#principles)
- [Test Fixture Strategy](#test-fixture-strategy)
- [Mocking Boundaries](#mocking-boundaries)
- [Quality Gate](#quality-gate)
- [Test Tiers](#test-tiers)
- [Architectural Enforcement Tests](#architectural-enforcement-tests)
- [What to Test](#what-to-test)
- [Coverage](#coverage)
- [Regression Policy](#regression-policy)
- [Test Code Quality](#test-code-quality)

---

## Principles

1. **The Adamant framework is the oracle.** If admt produces output that passes the framework's own build, style, and test checks, then admt is correct by construction.

2. **Test failure paths, not just success paths.** The happy path is easy. Edge cases (container not running, path not mapped, config missing, redo failure) are where real bugs hide -- and where agents most often skip writing tests.

3. **Every bug fix includes a regression test.** The test must fail before the fix and pass after. No exceptions.

4. **Tests run fast by default.** Unit and integration tests do not require Docker. Container tests are a separate tier that can be gated in CI.

5. **Untested code does not ship.** If a command or code path does not have a test, it is not done.

---

## Test Fixture Strategy

The tiers use different fixture sources by design:

- **Tiers 1 and 2 (unit and integration) use synthetic fixtures only.** Each test that needs a project root, a docker compose file, or a `~/.admt/config.yml` constructs one under `tmp_path`. This keeps tests hermetic (they do not break when upstream Adamant repos change), lets tests cover edge cases that real projects do not exhibit (multiple services, malformed YAML, missing markers, long-form vs short-form volume syntax), and keeps the suite runnable on any developer's machine with no external setup.

- **Tier 3 (container) uses the real Adamant project as ground truth.** Path mapping, environment activation, and `redo` invocation only truly work when exercised against a live container and real bind mounts. The tier 3 suite points at a configured real project (e.g., `adamant/` or `adamant_example/`) so that shape mismatches with the actual compose file, container layout, or redo build system are caught here.

Tests must not mix the two sources. A unit test that reads `../../adamant/docker/docker-compose.yml` is brittle and belongs in tier 3; a container test that mocks `docker compose exec` is not a container test and belongs in tier 1 or 2.

**Compose metadata comes from `docker compose config`, not raw YAML** (see [ARCHITECTURE.md Compose Parsing](ARCHITECTURE.md#compose-parsing)). `.env` interpolation therefore cannot be exercised by a synthetic YAML fixture in tiers 1/2 -- there is no docker there. So tiers 1/2 **mock the resolved-config adapter** and feed synthetic *resolved* structs (already-expanded `name`/`container_name`/absolute `volume_mounts`); only **tier 3** runs real `docker compose config` against a parameterized compose plus a `.env`. The TTY session store is pure logic and is unit-tested with a synthetic store plus a monkeypatched controlling-TTY/`getsid`.

---

## Mocking Boundaries

Mock at the right layer or tests pin themselves to implementation details. Use `unittest.mock` from the standard library -- do not add `pytest-mock` as a dependency.

- **Tier 1 (unit) -- mock at the adapter boundary.** Mock `DockerAdapter`, `RedoAdapter`, and `YamlAdapter` -- never `subprocess.run` directly. A unit test that asserts on the exact `subprocess.run` argument list breaks every time the adapter renames a flag, even when behavior is unchanged. Mocking the adapter lets the test assert on intent (`docker_adapter.compose_exec.assert_called_once_with(...)`) instead of mechanism.
- **Tier 2 (integration) -- mock at the service boundary.** Mock `ContainerService` (and other services where applicable) -- never `DockerAdapter`. This lets `CliRunner` exercise the real CLI parsing, command dispatch, flag handling, alias resolution, and Context wiring while skipping the container itself. Integration tests that mock at the adapter level end up re-testing what tier 1 already covers.
- **Tier 3 (container) -- mock nothing.** Real Docker, real adapters, real services, real `redo`. This is the ground-truth tier; mocking anything here defeats its purpose.

A test that finds itself patching `subprocess.run` is in the wrong tier or mocking at the wrong layer.

---

## Quality Gate

Every commit, every PR, and every CI run pass the same four checks:

```bash
ruff format --check src/ tests/
ruff check src/ tests/
mypy src/
pytest --cov --cov-branch --cov-fail-under=100
```

This is the **non-negotiable bar**. A PR is not ready unless all four succeed. There is no CI pipeline yet -- the local run is the gate. When the CI pipeline lands ([ROADMAP.md](ROADMAP.md)), the same four commands will run in GitHub Actions, and developers and agents will be able to rehearse the CI run locally with [`act`](https://github.com/nektos/act) before pushing.

`pytest --cov --cov-fail-under=100` enforces the 100% coverage threshold from [Coverage](#coverage); it is part of the gate, not an optional extra.

---

## Test Tiers

### Tier 1: Unit Tests

**Location:** `tests/unit/`

**Scope:** Individual functions, classes, and methods in isolation. Dependencies on external systems (Docker, filesystem, redo) are mocked.

**Speed:** Fast. All unit tests should complete in under 10 seconds.

**Docker required:** No.

**What they test:**

- **Commands:** Given mocked services, does the command produce the correct `Result` for given inputs? Does it construct the right redo command string? Does it handle missing arguments correctly?
- **Services:** Does the config service correctly parse and write YAML? Does the path mapper resolve paths correctly? Does the output service respect verbosity flags? Does the config service respect `ADMT_ENV` override?
- **Adapters:** Does the Docker adapter construct the correct `docker compose` command? Does the YAML adapter parse volume mounts correctly?
- **Environment snapshots:** The environment snapshot mechanism -- env var capture, `/tmp/admt/<project>/` script generation, and staleness detection -- must be unit tested with mock `subprocess` calls. Tests should verify that snapshot scripts are written with the correct content, that stale snapshots are detected and regenerated, and that missing snapshot directories are created on demand.

**Structure:**

```
tests/
  unit/
    commands/
      test_build.py
      test_env.py
      test_what.py
      test_templates.py
      ...
    services/
      test_config.py
      test_container.py
      test_path_mapper.py
      test_output.py
    adapters/
      test_docker.py
      test_yaml_adapter.py
      test_redo.py
    conftest.py              # Unit-specific fixtures (e.g., mock services)
```

**Example -- path mapper unit test:**

```python
"""Tests for the path mapper service."""
import pytest
from pathlib import Path
from admt.services.path_mapper import PathMapperService
from admt.exceptions import PathNotMappedError


@pytest.fixture
def mapper() -> PathMapperService:
    """Path mapper with typical Adamant project volume mounts."""
    return PathMapperService(volume_mounts={
        Path("/Users/dev/projects/adamant"): Path("/home/user/adamant"),
        Path("/Users/dev/projects/adamant_example"): Path("/home/user/adamant_example"),
        Path("/Users/dev/projects/xmera-components"): Path("/home/user/xmera-components"),
    })


def test_maps_file_in_mounted_directory(mapper: PathMapperService) -> None:
    host = Path("/Users/dev/projects/adamant/src/components/ccsds_router")
    expected = Path("/home/user/adamant/src/components/ccsds_router")
    assert mapper.host_to_container(host) == expected


def test_maps_project_root_exactly(mapper: PathMapperService) -> None:
    host = Path("/Users/dev/projects/adamant_example")
    expected = Path("/home/user/adamant_example")
    assert mapper.host_to_container(host) == expected


def test_raises_for_unmapped_path(mapper: PathMapperService) -> None:
    host = Path("/Users/dev/other-project/src/foo")
    with pytest.raises(PathNotMappedError) as exc_info:
        mapper.host_to_container(host)
    assert "not mapped" in str(exc_info.value).lower()


def test_longest_prefix_match(mapper: PathMapperService) -> None:
    """If a path matches multiple mounts, the longest prefix wins."""
    nested_mapper = PathMapperService(volume_mounts={
        Path("/Users/dev/projects"): Path("/home/user"),
        Path("/Users/dev/projects/adamant"): Path("/home/user/adamant"),
    })
    host = Path("/Users/dev/projects/adamant/src/foo")
    # Should match /Users/dev/projects/adamant, not /Users/dev/projects
    assert nested_mapper.host_to_container(host) == Path("/home/user/adamant/src/foo")
```

**Example -- config service unit test:**

```python
"""Tests for the config service."""
import pytest
from pathlib import Path
from admt.services.config import ConfigService
from admt.exceptions import ConfigError


def test_register_project_parses_compose_file(tmp_path: Path) -> None:
    """Registering a project extracts correct info from docker-compose.yml."""
    compose_content = """\
name: test-project
services:
    test-project:
        container_name: test-project_container
        volumes:
            - type: bind
              source: ../../adamant
              target: /home/user/adamant
            - type: bind
              source: ../../test-project
              target: /home/user/test-project
"""
    docker_dir = tmp_path / "test-project" / "docker"
    docker_dir.mkdir(parents=True)
    compose_file = docker_dir / "docker-compose.yml"
    compose_file.write_text(compose_content)

    # Create marker files
    project_root = tmp_path / "test-project"
    (project_root / "default.do").touch()
    (project_root / "env").mkdir()
    (project_root / "env" / "activate").touch()

    # Create sibling dirs so path resolution works
    (tmp_path / "adamant").mkdir()

    config_service = ConfigService(config_dir=tmp_path / ".admt")
    project = config_service.register_project(project_root)  # Takes project root, not compose file

    assert project.name == "test-project"
    assert project.service_name == "test-project"
    assert project.container_name == "test-project_container"
    assert len(project.volume_mounts) == 2


def test_no_active_project_raises(tmp_path: Path) -> None:
    """Getting active project when none is configured raises ConfigError."""
    config_service = ConfigService(config_dir=tmp_path / ".admt")
    with pytest.raises(ConfigError, match="No project configured"):
        config_service.get_active_project()  # Returns ProjectConfig, never None
```

### Tier 2: Integration Tests

**Location:** `tests/integration/`

**Scope:** Invoke admt as a CLI command (using Click's `CliRunner` and subprocess) and verify complete behavior -- exit codes, stdout/stderr output, file creation, config file changes.

**Speed:** Moderate. No Docker required but may touch the filesystem.

**Docker required:** No. Container service is mocked.

**Dual approach:**

1. **Click's CliRunner** -- programmatic invocation, no subprocess. Fast, catches argument parsing and routing issues.
2. **Subprocess invocation** -- calls `admt` as a real binary. Catches packaging, entry-point, and environment issues.

Both are needed. CliRunner alone misses packaging problems. Subprocess alone is slower and harder to debug.

**What they test:**

- End-to-end CLI invocation: correct args -> correct output + exit code
- Error cases: missing config, bad arguments, unmapped paths
- Flag behavior: `--verbose`, `--quiet`, `--debug`, `--yes`, `--force`
- Alias resolution: `admt b` works the same as `admt build`
- `ADMT_NONINTERACTIVE` behavior
- `ADMT_ENV` override behavior
- Config file creation and modification by `admt env init` / `admt env use`
- All `env` subcommands: `env start`, `env stop`, `env restart`, `env status`, `env login`, `env build`, `env push`, `env pull`, `env exec`, `env refresh`, `env list`, `env rm` (with `--volumes`, `--image`, `--remove-all` flags)

**Example -- integration test:**

```python
"""Integration tests for admt CLI."""
from click.testing import CliRunner
from admt.cli import cli


def test_no_config_prints_helpful_error() -> None:
    """Running admt build with no project configured gives a clear error."""
    runner = CliRunner()
    result = runner.invoke(cli, ["build"])
    assert result.exit_code == 2
    assert "admt env init" in result.output


def test_env_init_creates_config(tmp_path: Path, project_root: Path) -> None:
    """admt env init creates ~/.admt/config.yml with correct content."""
    runner = CliRunner(env={"HOME": str(tmp_path)})
    result = runner.invoke(cli, ["env", "init", str(project_root)])
    assert result.exit_code == 0
    assert "Registered project" in result.output
    config = (tmp_path / ".admt" / "config.yml").read_text()
    assert "active_project" in config


def test_env_init_rejects_missing_markers(tmp_path: Path) -> None:
    """admt env init with missing markers prints clear error."""
    bare_dir = tmp_path / "not-a-project"
    bare_dir.mkdir()
    runner = CliRunner(env={"HOME": str(tmp_path)})
    result = runner.invoke(cli, ["env", "init", str(bare_dir)])
    assert result.exit_code != 0
    assert "default.do" in result.output


def test_build_alias_works(mock_container: MagicMock) -> None:
    """'admt b' is equivalent to 'admt build'."""
    runner = CliRunner()
    result = runner.invoke(cli, ["b"])
    assert result.exit_code == 0
    mock_container.exec.assert_called_once()
    call_args = mock_container.exec.call_args[0][0]
    assert "redo all" in call_args


def test_verbose_shows_docker_command(mock_container: MagicMock) -> None:
    """--verbose prints the docker exec command."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--verbose", "build"])
    assert "docker compose" in result.output or "$ " in result.output


def test_quiet_suppresses_output_on_success(mock_container: MagicMock) -> None:
    """--quiet produces no output when command succeeds."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--quiet", "build"])
    assert result.output == ""
    assert result.exit_code == 0


def test_noninteractive_errors_on_prompt(mock_container_stopped: MagicMock) -> None:
    """ADMT_NONINTERACTIVE causes prompts to become errors."""
    runner = CliRunner(env={"ADMT_NONINTERACTIVE": "1"})
    result = runner.invoke(cli, ["build"])
    assert result.exit_code == 2
    assert "not running" in result.output.lower()


def test_admt_env_override(mock_container: MagicMock, configured_projects: Path) -> None:
    """ADMT_ENV overrides the active project for one invocation."""
    runner = CliRunner(env={"ADMT_ENV": "adamant-standalone"})
    result = runner.invoke(cli, ["build"])
    assert result.exit_code == 0
    # Verify the command was sent to the override project's container
    ...
```

### Tier 3: Container Tests

**Location:** `tests/container/`

**Scope:** Full pipeline tests that run against a real Adamant Docker container. These are the ground-truth tests.

**Speed:** Slow (seconds to minutes per test). Require Docker and a running Adamant container.

**Docker required:** Yes.

**What they test:**

- `admt env start` actually starts a container
- `admt env restart` cycles the container
- `admt env exec "echo hello"` returns "hello"
- `admt build` from a real component directory builds successfully
- `admt test` from a real component directory runs tests
- `admt style` from a real component directory passes style checks
- `admt what` lists real targets
- `admt templates` generates real stubs
- `admt templates --undo` restores overwritten files
- Path mapping works end-to-end with real bind mounts
- Environment activation works (redo commands can find the build system)
- Signal handling: Ctrl+C propagates and admt exits with 130

**Important:** These tests verify that admt actually works with Adamant, not just that it correctly constructs commands. They catch issues like incorrect path mapping, missing environment activation, or incompatibilities with the real container.

**Example:**

```python
"""Container tests -- require a running Adamant container."""
import subprocess
import pytest


@pytest.fixture(scope="session")
def container_running():
    """Ensure the test container is running."""
    result = subprocess.run(["admt", "env", "status"], capture_output=True, text=True)
    if "running" not in result.stdout.lower():
        subprocess.run(["admt", "env", "start"], check=True)
    yield
    # Don't stop -- let the developer manage container lifecycle


@pytest.mark.container
def test_build_real_component(container_running):
    """admt build succeeds on a real Adamant component."""
    result = subprocess.run(
        ["admt", "build"],
        capture_output=True, text=True,
        cwd="/path/to/real/component"  # Configure via env or fixture
    )
    assert result.returncode == 0


@pytest.mark.container
def test_what_lists_targets(container_running):
    """admt what returns buildable targets from a real directory."""
    result = subprocess.run(
        ["admt", "what"],
        capture_output=True, text=True,
        cwd="/path/to/real/component"
    )
    assert result.returncode == 0
    assert "all" in result.stdout
```

Container tests are marked with `@pytest.mark.container` so they can be run separately:

```bash
# Run only fast tests (no Docker)
pytest -m "not container"

# Run container tests
pytest -m container

# Run everything
pytest
```

---

## Architectural Enforcement Tests

These tests verify the structure of the codebase, not its behavior. They prevent architectural drift.

**Location:** `tests/unit/test_architecture.py`

### Import Linting

Enforces the dependency rules from [ARCHITECTURE.md](ARCHITECTURE.md):

```python
"""Tests that enforce architectural dependency rules."""
import ast
from pathlib import Path


def _get_imports(filepath: Path) -> set[str]:
    """Extract all import targets from a Python file."""
    tree = ast.parse(filepath.read_text())
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def test_commands_do_not_import_click() -> None:
    """Commands must not import Click -- it belongs in cli.py only."""
    commands_dir = Path("src/admt/commands")
    for py_file in commands_dir.glob("*.py"):
        imports = _get_imports(py_file)
        assert not any(imp == "click" or imp.startswith("click.") for imp in imports), (
            f"{py_file.name} imports click -- commands must not depend on the CLI layer"
        )


def test_services_do_not_import_commands() -> None:
    """Services must not import from commands or CLI."""
    services_dir = Path("src/admt/services")
    for py_file in services_dir.glob("*.py"):
        imports = _get_imports(py_file)
        assert not any(imp == "admt.commands" or imp.startswith("admt.commands.") for imp in imports), (
            f"{py_file.name} imports from commands -- services must not depend on commands"
        )
        assert not any(imp == "admt.cli" or imp.startswith("admt.cli.") for imp in imports), (
            f"{py_file.name} imports cli -- services must not depend on the CLI layer"
        )


def test_adapters_do_not_import_services_or_commands() -> None:
    """Adapters must not import from services, commands, or CLI."""
    adapters_dir = Path("src/admt/adapters")
    for py_file in adapters_dir.glob("*.py"):
        imports = _get_imports(py_file)
        for forbidden in ["admt.services", "admt.commands", "admt.cli"]:
            assert not any(imp == forbidden or imp.startswith(forbidden + ".") for imp in imports), (
                f"{py_file.name} imports {forbidden} -- adapters must only depend on externals"
            )


def test_only_adapters_import_subprocess() -> None:
    """Only adapters may import subprocess."""
    for layer in ["commands", "services"]:
        layer_dir = Path(f"src/admt/{layer}")
        for py_file in layer_dir.glob("*.py"):
            imports = _get_imports(py_file)
            assert "subprocess" not in imports, (
                f"{py_file.name} imports subprocess -- only adapters may use subprocess"
            )
```

### Command Contract Tests

Every `Command` subclass must declare required metadata:

```python
"""Tests that verify all Command subclasses have required metadata."""
from admt.commands.base import Command


def _all_command_subclasses() -> list[type[Command]]:
    """Discover all Command subclasses via recursive __subclasses__.

    IMPORTANT: ``__subclasses__()`` only returns classes that have already been
    imported into the current process.  Before calling this helper the test
    module (or a ``conftest.py``) must force-import every module under
    ``admt.commands`` so that all concrete Command classes are registered::

        import importlib, pkgutil, admt.commands
        for _importer, modname, _ispkg in pkgutil.walk_packages(
            admt.commands.__path__, prefix="admt.commands."
        ):
            importlib.import_module(modname)
    """
    result = []
    stack = list(Command.__subclasses__())
    while stack:
        cls = stack.pop()
        if not getattr(cls, "__abstractmethods__", set()):
            result.append(cls)
        stack.extend(cls.__subclasses__())
    return result


def test_all_commands_have_required_metadata() -> None:
    """Every concrete Command must declare name, help, and requires_project."""
    for cls in _all_command_subclasses():
        assert hasattr(cls, "name") and cls.name, f"{cls.__name__} missing 'name'"
        assert hasattr(cls, "help") and cls.help, f"{cls.__name__} missing 'help'"
        assert hasattr(cls, "requires_project"), f"{cls.__name__} missing 'requires_project'"
```

### CLI-Command Parity Tests

Every `Command` subclass has a Click entry, and vice versa:

```python
"""Tests that verify CLI and Command layers are in sync."""
import click
from admt.cli import cli
from admt.commands.base import Command


def _get_click_command_names(group: click.Group, prefix: str = "") -> set[str]:
    """Recursively collect all Click command names."""
    names = set()
    for name, cmd in group.commands.items():
        full_name = f"{prefix} {name}".strip()
        if isinstance(cmd, click.Group):
            names.update(_get_click_command_names(cmd, full_name))
        else:
            names.add(full_name)
    return names


def test_every_command_has_click_entry() -> None:
    """Every Command subclass must have a corresponding Click entry in cli.py."""
    click_names = _get_click_command_names(cli)
    for cls in _all_command_subclasses():
        assert cls.name in click_names, (
            f"Command '{cls.name}' ({cls.__name__}) has no Click entry in cli.py"
        )


def test_every_click_entry_has_command() -> None:
    """Every Click command must have a corresponding Command subclass."""
    click_names = _get_click_command_names(cli)
    command_names = {cls.name for cls in _all_command_subclasses()}
    for name in click_names:
        assert name in command_names, (
            f"Click command '{name}' has no corresponding Command subclass"
        )
```

### No-Logic-in-CLI Test

```python
"""Tests that cli.py remains thin."""
import ast
from pathlib import Path


def test_cli_functions_are_short() -> None:
    """No function body in cli.py should exceed 15 lines."""
    cli_path = Path("src/admt/cli.py")
    tree = ast.parse(cli_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body_lines = node.end_lineno - node.lineno  # type: ignore[operator]
            assert body_lines <= 15, (
                f"Function '{node.name}' in cli.py is {body_lines} lines -- "
                f"cli.py must stay thin. Move logic to a command class."
            )
```

### Circular Import Detection

```python
"""Test that all admt modules can be imported without circular import errors."""
import importlib
import pkgutil
import admt


def test_no_circular_imports() -> None:
    """All admt modules can be imported without circular import errors."""
    for importer, modname, ispkg in pkgutil.walk_packages(
        admt.__path__, prefix="admt."
    ):
        importlib.import_module(modname)
```

---

## What to Test

### Every Command Must Test

| Scenario | What to verify |
|----------|---------------|
| Happy path | Correct output, exit code 0, correct side effects |
| Missing config | Exit code 2, message suggests `admt env init` |
| Container not running | Exit code 2, message suggests `admt env start` |
| Invalid arguments | Exit code 3, message names the bad argument |
| Path not mapped | Exit code 4, message lists mapped directories |
| `--verbose` | Underlying command is printed |
| `--quiet` | No output on success |
| `--debug` | `DEBUG=1` is prepended to redo commands; verbose diagnostic output is shown |
| `--yes` | Prompts auto-accepted |
| `--force` | Files are overwritten without confirmation prompts |
| `ADMT_NONINTERACTIVE` | Prompts become errors |
| `ADMT_ENV` override | Correct project is used |
| `ADMT_NONINTERACTIVE=0` | Disables non-interactive mode (treated as off, same as unset) |
| `NO_COLOR` env var | All color output suppressed |
| Short alias | Same behavior as full command name |

### Error Path Tests

| Scenario | Exit | Expected behavior |
|----------|------|-------------------|
| Container not running | 2 | Message: "not running", suggest `admt env start` |
| Project not configured | 2 | Message: "no project configured", suggest `admt env init` |
| Invalid target path | 3 | Message: "path does not exist" |
| Path outside volume mounts | 4 | Message: list mapped directories |
| Missing markers for env init | 3 | Message: list which markers are missing |
| Unknown project for env use | 3 | Message: list available projects |
| Redo build failure | 1 | Show failed command, forward redo output |
| Redo test failure | 1 | Show failed command, forward test output |
| Container already started | 0 | Message: "already running" (idempotent) |
| Container already stopped | 0 | Message: "already stopped" (idempotent) |

### Idempotency Tests

- Running `admt env init` twice for the same project updates config, does not duplicate
- Running `admt env use` with the already-active project is a no-op
- Running `admt env start` when already running reports "already running" (exit 0)
- Running `admt env stop` when already stopped reports "already stopped" (exit 0)

### Worktree / Multi-Environment Tests

Covering the [Compose Parsing](ARCHITECTURE.md#compose-parsing) and [Active Project Resolution](ARCHITECTURE.md#active-project-resolution) amendments.

**Compose parsing (tier 1/2 mock the resolved-config adapter; tier 3 real):**

| Scenario | What to verify |
|----------|---------------|
| Parameterized compose + `.env` (tier 3) | Resolved `name`/`container_name`/mounts match `docker compose config` (e.g. `adamant_example-wt1`, not the literal `${...}`) |
| `.env` mtime change | Next command re-derives config; `container_name`/ports update; notice printed |
| `.env` removed after registration | Treated as a change; re-derives with no env file |
| Compose unchanged, `.env` unchanged | No `docker compose config` call (cached values used); only the two `stat()`s run |
| `docker` CLI absent on `env init` | Clear error naming the missing dependency |

**Active project resolution (pure logic, tier 1; monkeypatch TTY/`getsid`):**

| Scenario | What to verify |
|----------|---------------|
| `ADMT_ENV` set | Wins over session entry and global |
| Session entry for current TTY | Used when no `ADMT_ENV`; overrides global |
| `env use` writes both | Session entry for this TTY **and** global `active_project` updated |
| New terminal (different TTY) | No session entry; falls back to global = last used |
| Stale entry (`getsid` mismatch) | Ignored and pruned; falls back to global |
| No controlling TTY (piped / `ADMT_NONINTERACTIVE`) | Session layer skipped; uses `ADMT_ENV` then global |
| `env status` source | Reports active project **and** its source (`ADMT_ENV` / this terminal / global) |
| Two worktrees, two TTYs (tier 3) | `env use wt1` in one terminal and `wt2` in another target distinct containers concurrently |

---

## Coverage

- **Threshold:** 100% line coverage, enforced in CI. This is non-negotiable.
- **Branch coverage:** Enabled. Catches the "happy path only" failure mode where agents write tests for the success case but forget the error path.
- **New code must not decrease coverage.** Every line and branch must be covered. Lines that genuinely cannot be tested may use `# pragma: no cover`, but this must be rare and justified.

Configure in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "container: tests that require a running Docker container",
]

[tool.coverage.run]
source = ["admt"]
branch = true

[tool.coverage.report]
fail_under = 100
show_missing = true
exclude_lines = [
    "pragma: no cover",
    "if __name__ == .__main__.",
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
]
```

---

## Regression Policy

Every bug fix includes a test that:

1. **Reproduces the failure** -- the test fails on the code *before* the fix
2. **Verifies the fix** -- the test passes on the code *after* the fix
3. **Prevents recurrence** -- the test stays in the suite permanently

This is not optional. A bug fix without a regression test is not complete.

---

## Test Code Quality

Tests follow the same quality rules as production code (see [CODING_RULES.md](CODING_RULES.md)):

- Type-annotated
- Well-named (test name describes the scenario and expected behavior)
- No copy-paste test matrices -- use `@pytest.mark.parametrize` for variations
- No 500-line test files -- split by logical area
- No "it works, don't touch it" test helpers -- helpers are tested too (or trivially simple)
- Use fixtures for shared setup, not inheritance or base test classes
- Each test tests **one thing**. If a test name contains "and", it is probably two tests.

### Test Naming Convention

```python
# Good -- describes scenario and expectation
def test_path_mapper_raises_for_unmapped_directory() -> None: ...
def test_build_command_uses_redo_all_by_default() -> None: ...
def test_env_init_sets_active_project_on_registration() -> None: ...
def test_env_init_rejects_directory_missing_markers() -> None: ...

# Bad -- vague
def test_path_mapper() -> None: ...
def test_build() -> None: ...
def test_env_init_works() -> None: ...
```
