# admt MVP Implementation Plan

This document is the step-by-step plan for building the first working version of admt. The MVP delivers two capabilities:

1. **Container management** -- `admt env [init, use, list, start, stop, restart, build, push, pull, login, status, exec, refresh, rm]`
2. **Build passthrough** -- `admt [build, what, test [--all], style, clean [--all], prove, coverage [--all], publish, templates [--undo]] <optional_path>`

The MVP **must** adhere to the architecture defined in [ARCHITECTURE.md](ARCHITECTURE.md), the coding rules in [CODING_RULES.md](CODING_RULES.md), and the test plan in [TEST_PLAN.md](TEST_PLAN.md). This is not a prototype -- it is the foundation that all future features build on.

### Tests as you go

Every phase's unit and integration tests are written **alongside** the implementation in the same commit -- not as a follow-up. Per [TEST_PLAN.md](TEST_PLAN.md#test-fixture-strategy), tier 1 and tier 2 tests in this MVP use **synthetic fixtures** (project roots, docker compose files, and `~/.admt/config.yml` content constructed under `tmp_path`) -- never the real `adamant/` or `adamant_example/` directories. Tier 3 container tests are the only place real projects are referenced. Each phase below lists the specific unit and integration tests it adds; tier 3 runs are deferred until a container is available.

### Per-phase quality gate

Every phase ends with the same gate. **A phase is not complete until the gate passes; do not proceed to the next phase until it does.**

```bash
ruff format --check src/ tests/
ruff check src/ tests/
mypy src/
pytest --cov --cov-branch --cov-fail-under=100
```

Run this locally before committing each phase, and again as the **last** thing you do before opening or updating a PR.

**During the MVP itself there is no CI** -- CI is explicitly deferred to post-MVP (see [Next Up: CI Pipeline and Packaging](#next-up-ci-pipeline-and-packaging)). For MVP work, the local gate above **is** the gate. Once the post-MVP CI pipeline lands, the same four commands will run in GitHub Actions, and developers and agents will be able to rehearse the CI run locally with [`act`](https://github.com/nektos/act) against the workflow file before pushing.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Phase 0: Project Skeleton](#phase-0-project-skeleton)
- [Phase 1: Configuration and Discovery](#phase-1-configuration-and-discovery)
- [Phase 2: Container Management](#phase-2-container-management)
- [Phase 3: Build Passthrough](#phase-3-build-passthrough)
- [Phase 4: Templates Command](#phase-4-templates-command)
- [Phase 5: Global Flags and Polish](#phase-5-global-flags-and-polish)
- [Command Reference](#command-reference)
- [Roadmap (Post-MVP)](#roadmap-post-mvp)

---

## Prerequisites

Before implementation begins, the following must be available:

- Python 3.14+ on the host machine
- Docker (or Podman) installed and functional
- An Adamant project with the standard layout (see [ARCHITECTURE.md](ARCHITECTURE.md) for layout details)
- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

---

## Phase 0: Project Skeleton

**Goal:** A pip-installable Python package with the correct directory structure, tooling, and a single working command (`admt --help`).

### Steps

1. **Hand-craft the project skeleton.** Do **not** run `uv init` -- it scaffolds artifacts we would have to rewrite or delete (`hello.py`, a generic `README.md`, a minimal `pyproject.toml`). Instead, create the files listed below directly:
   - `pyproject.toml` (hand-written to match [CODING_RULES.md](CODING_RULES.md) ruff/mypy/pytest/coverage config exactly)
   - `.python-version` containing `3.14`
   - `.gitignore` covering `__pycache__/`, `*.pyc`, `.venv/`, `build/`, `dist/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `htmlcov/`, `.coverage`, `*.egg-info/`

   After the files are in place, run `uv sync --dev` to build the venv from `pyproject.toml`.

2. **Create directory structure** per [ARCHITECTURE.md](ARCHITECTURE.md):
   ```
   src/
     admt/
       __init__.py
       main.py
       cli.py
       context.py
       exceptions.py
       commands/
         __init__.py
         base.py
       services/
         __init__.py
       adapters/
         __init__.py
   tests/
     unit/
       commands/
       services/
       adapters/
     integration/
     container/
     conftest.py
   pyproject.toml
   ```

3. **Implement `__init__.py`** with the version string:
   ```python
   """admt -- The Adamant Multitool."""
   __version__ = "0.1.0"
   ```
   `pyproject.toml` reads the version dynamically from this file.

4. **Configure `pyproject.toml`:**
   - Project metadata (name, version dynamic from `__init__.py`, description, license, Python requires)
   - Dependencies: `click`, `ruamel.yaml`
   - Dev dependencies: `pytest`, `pytest-cov`, `mypy`, `ruff`
   - Entry point: `[project.scripts] admt = "admt.main:main"`
   - Ruff config (see [CODING_RULES.md](CODING_RULES.md))
   - Mypy config (see [CODING_RULES.md](CODING_RULES.md))
   - Pytest config: `testpaths = ["tests"]`, coverage settings

5. **Implement `main.py`** -- the entry point:
   ```python
   """admt entry point."""
   from admt.cli import cli

   def main() -> None:
       cli()

   if __name__ == "__main__":
       main()
   ```

6. **Implement `cli.py`** -- minimal Click app with `AliasedGroup` support:
   ```python
   """Click CLI adapter -- thin, no logic."""
   import click
   from typing import Any


   class AliasedGroup(click.Group):
       """Click group that supports command aliases (e.g., 'admt b' for 'admt build')."""

       def __init__(self, *args: Any, **kwargs: Any) -> None:
           super().__init__(*args, **kwargs)
           self._aliases: dict[str, str] = {}

       def add_alias(self, alias: str, command_name: str) -> None:
           self._aliases[alias] = command_name

       def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
           if cmd_name in self._aliases:
               cmd_name = self._aliases[cmd_name]
           return super().get_command(ctx, cmd_name)


   @click.group(cls=AliasedGroup)
   @click.version_option()
   @click.option("--verbose", "-v", is_flag=True, help="Show underlying commands")
   @click.option("--quiet", "-q", is_flag=True, help="Minimal output")
   @click.option("--debug", "-d", is_flag=True, help="Verbose + redo DEBUG=1")
   @click.option("--yes", "-y", is_flag=True, help="Auto-accept prompts")
   @click.option("--force", "-f", is_flag=True, help="Overwrite existing files")
   @click.pass_context
   def cli(ctx: click.Context, verbose: bool, quiet: bool, debug: bool, yes: bool, force: bool) -> None:
       """admt -- The Adamant Multitool."""
       ctx.ensure_object(dict)
       ctx.obj["verbose"] = verbose or debug  # --debug implies --verbose
       ctx.obj["quiet"] = quiet
       ctx.obj["debug"] = debug
       ctx.obj["yes"] = yes
       ctx.obj["force"] = force
   ```

   `AliasedGroup` is introduced here in Phase 0 because Phase 1 needs it (for the `env` -> `e` group alias). Top-level aliases (`b`, `t`, etc.) are wired in Phase 3 when the corresponding commands are implemented.

7. **Implement `context.py`** -- the Context and Result dataclasses per [ARCHITECTURE.md](ARCHITECTURE.md) Command Structure section. Note: `Context.run_all` (not `all`, to avoid shadowing the Python builtin).

8. **Implement `exceptions.py`** -- the admt exception hierarchy (`AdmtError`, `ContainerError`, `ConfigError`, `ArgumentError`, `PathNotMappedError`).

9. **Implement `commands/base.py`** -- the `Command` base class (fully) and `ContainerPassthroughCommand` as a stub/interface. The full `ContainerPassthroughCommand` implementation depends on services from Phase 1-2 and is completed in Phase 3.

10. **Write unit tests** for Context, Result, Command base class, and exceptions. Write the import linting test (enforces dependency rules from [ARCHITECTURE.md](ARCHITECTURE.md)) and the command contract test (both will be trivially passing with no commands yet, but the infrastructure exists).

11. **Verify toolchain:** `ruff check`, `ruff format --check`, `mypy src/`, `pytest` all pass. `uv run admt --help` prints help text.

### Deliverable

`admt --help` works. All tooling passes. The architecture is in place and enforced by tests.

> **Gate:** Phase 0 is complete only when the [per-phase quality gate](#per-phase-quality-gate) passes. Do not start Phase 1 until it does.

---

## Phase 1: Configuration and Discovery

**Goal:** `admt env init` and `admt env use` work. admt can detect a project root, parse a docker compose file, and store/retrieve project configuration.

### Steps

1. **Implement `adapters/yaml_adapter.py`:**
   - Load YAML files using `ruamel.yaml` (preserves comments and structure)
   - Parse docker compose files specifically: extract `name`, `services`, `container_name`, `volumes`

2. **Implement `services/config.py`:**
   - `ConfigService` manages `~/.admt/config.yml`
   - `register_project(project_root: Path)`: Verify all three markers are present (`default.do`, any `.yml`/`.yaml` file in `docker/`, `env/activate`). If multiple compose files found in `docker/`, prompt user to select one (error with exit code 3 if `ADMT_NONINTERACTIVE`). Parse the compose file and extract:
     - Project name (from the top-level `name:` field in the compose file)
     - Service name (see below for multi-service handling)
     - Container name (from `container_name:` field if present; otherwise derived from the service name and verified against `docker compose ps`)
     - All volume mounts (builds the host <-> container path map)
     - `container_home` (e.g., `/home/user`)
     - `activate_script` path (derived: `<container_project_root>/env/activate`)
   - Store all fields in `~/.admt/config.yml` with `version: 1` schema version. Also record `compose_file_mtime` (the Unix timestamp of the compose file at the time of registration) for change detection.
   - `get_active_project()`: Return the active project config. Checks `ADMT_ENV` environment variable first, then falls back to `active_project` in config. Raises `ConfigError` if no project is configured. **Before returning the project config, call `check_and_refresh_project()` to detect and auto-apply changes to the compose file.**
   - `check_and_refresh_project(name)`: Stat the compose file. If its mtime is newer than the stored `compose_file_mtime`, silently re-parse the compose file, update the project's `service_name`, `container_name`, `volume_mounts`, and other derived fields, update `compose_file_mtime`, and print a concise notice to stdout describing changes (e.g., `Updated config: added mount ../../new-repo -> /home/user/new-repo`). Happens regardless of `--yes` / `ADMT_NONINTERACTIVE` (it is not a prompt). If the compose file is missing or unreadable, raise `ConfigError`.
   - `set_active_project(name)`: Update the active project
   - `list_projects()`: Return all registered projects

   **Volume mount parsing detail:** The compose file at `/path/to/project/docker/docker-compose.yml` has relative source paths like `../../adamant`. These must be resolved to absolute paths: the compose file's directory is `/path/to/project/docker/`, so `../../adamant` resolves to `/path/to/adamant/` (using `Path.resolve()`). The target paths are already absolute (e.g., `/home/user/adamant`).

   **Service name resolution:** Most Adamant compose files define a single service. If multiple services are present, admt selects the service that bind-mounts the `adamant/` directory (i.e., has a volume mount whose container target path ends with `/adamant`). This is the development service. If no service mounts `adamant/` or multiple do, admt errors with a message listing the available services.

3. **Implement `services/path_mapper.py`:**
   - `PathMapperService` takes a volume mount dict and maps host paths to container paths
   - Uses longest-prefix matching: if `/Users/dev/adamant` is mounted at `/home/user/adamant`, then `/Users/dev/adamant/src/foo` maps to `/home/user/adamant/src/foo`
   - Raises `PathNotMappedError` with a helpful message listing all mapped directories

4. **Implement `commands/env.py`:**
   - `EnvInitCommand`: Calls `ConfigService.register_project()`. If path argument provided, use that as project root. Otherwise, use cwd. Validates all three markers are present. If markers missing, exits with clear error listing which are missing. Set as active project. **If the project is already registered**, follow the re-init behavior in [ARCHITECTURE.md §Re-running `admt env init` on a registered project](ARCHITECTURE.md#re-running-admt-env-init-on-a-registered-project): interactive prompt with default `No`, `--force` to overwrite, `ADMT_NONINTERACTIVE` errors without `--force`.
   - `EnvUseCommand`: Calls `ConfigService.set_active_project()`. Validates the project name exists in config.

5. **Wire into `cli.py`** using the `AliasedGroup` from Phase 0:
   ```python
   @cli.group(name="env", cls=AliasedGroup)
   def env_group() -> None:
       """Manage the Adamant development environment."""

   # Register the env -> e alias on the top-level cli group
   cli.add_alias("e", "env")

   @env_group.command(name="init")
   @click.argument("path", required=False, type=click.Path(exists=True))
   @click.pass_context
   def env_init(ctx: click.Context, path: str | None) -> None:
       """Register a project with admt."""
       ...

   @env_group.command(name="use")
   @click.argument("project_name")
   @click.pass_context
   def env_use(ctx: click.Context, project_name: str) -> None:
       """Switch the active project."""
       ...
   ```

6. **Add short alias** for the `env` group: `e`.

7. **Write unit tests:**
   - Config service: register, retrieve, set active, missing config file, corrupt config, ADMT_ENV override
   - Path mapper: basic mapping, nested paths, unmapped paths, longest-prefix matching
   - YAML adapter: parse real docker-compose.yml structure, relative path resolution
   - Env init command: with explicit path, with cwd, no markers found, partial markers found, multiple compose files
   - Env use command: valid project, unknown project

8. **Write integration tests:**
   - `admt env init` from a project root creates `~/.admt/config.yml` with correct content
   - `admt env init /path/to/project/` with explicit path works
   - `admt env init` with missing markers prints clear error listing missing markers
   - `admt env use <name>` switches active project
   - `ADMT_ENV` override works for subsequent commands

### Deliverable

```bash
$ cd ~/projects/adamant_example/
$ admt env init
Registered project 'adamant_example' with 7 volume mounts.
Active project: adamant_example

$ admt env use adamant-standalone
Active project: adamant-standalone
```

> **Gate:** Phase 1 is complete only when the [per-phase quality gate](#per-phase-quality-gate) passes. Do not start Phase 2 until it does.

---

## Phase 2: Container Management

**Goal:** `admt env [start, stop, restart, login, status, build, push, pull, exec, refresh, rm, list]` all work.

### Steps

1. **Implement `adapters/docker.py`:**
   - Wraps `docker compose` CLI calls using `subprocess`
   - Methods: `compose_up()`, `compose_stop()`, `compose_down()`, `compose_exec()`, `compose_build()`, `compose_push()`, `compose_pull()`, `is_container_running()`, `get_container_status()`, `compose_ps()`, `remove_image()`
   - Uses the compose file path and service name from the active project config
   - Handles both `docker compose` (modern) and `docker-compose` (legacy) detection, preferring `docker compose`
   - **Container state detection:** Use `docker compose ps --format json` for structured status output. Fall back to exit code of `docker compose exec <service> true` if `--format json` is not available.
   - **MVP targets Docker only.** Do not add Podman support yet, but keep docker interaction behind the adapter interface so Podman can be added later without changing services or commands.
   - **TTY and color passthrough:** When spawning `docker compose exec` and other subprocess calls, allocate a pseudo-TTY so that redo, docker compose, and other tools preserve their color output and interactive formatting. Use `subprocess.Popen` with `sys.stdout`/`sys.stderr` inherited (not captured) for streaming commands. For commands where admt needs to inspect output (e.g., `env` variable capture), use `subprocess.PIPE` instead.

2. **Implement `services/container.py`:**
   - `ContainerService` wraps the Docker adapter with higher-level operations
   - `start()`: If the Docker image does not exist locally, auto-runs `docker compose pull`. If the pull fails (e.g., image not in registry, network error), admt errors with an actionable message: "Pull failed: <reason>. Build the image locally with `admt env build`." Then runs `docker compose up -d`. Check the return code; on failure, forward stderr output to the caller. After the container starts, runs the environment activation and generates the admt snapshot (see [ARCHITECTURE.md](ARCHITECTURE.md) Environment Activation).
   - `stop()`: Runs `docker compose stop`
   - `restart()`: Runs `stop()` then `start()`. If `stop()` fails, `restart()` aborts -- it does not attempt `start()`.
   - `login()`: Runs `docker compose exec -it -u user <service> /bin/bash` (no proxy script needed -- the container's `.bashrc` sources `env/activate` automatically)
   - `status()`: Returns running/stopped/not-found
   - `build_image()`: Runs `docker compose build`
   - `push_image()`: Runs `docker compose push`
   - `pull_image()`: Runs `docker compose pull`
   - `rm()`: Removes the container (`docker compose down`). Flags: `--volumes` (also remove volumes), `--image` (also remove Docker image), `--remove-all` (remove container, volumes, and image). Note: use `remove_all` not `all` to avoid shadowing the Python builtin. Prompts for confirmation unless `--yes`.
   - `refresh()`: Re-runs full `env/activate` and regenerates the admt environment snapshot (see [ARCHITECTURE.md](ARCHITECTURE.md))
   - `ensure_env_snapshot()`: Writes `/tmp/admt/<project>/env_snapshot.sh` and `/tmp/admt/<project>/exec.sh` to the container (see [ARCHITECTURE.md](ARCHITECTURE.md) for details). Checks if scripts exist and are current; only regenerates if needed.
   - `exec(command, context)`: Executes a command through `/tmp/admt/<project>/exec.sh`. Output is streamed directly to the terminal (not captured in `Result`). `Result` carries the exit code only.

3. **Implement `services/output.py`:**
   - `OutputService` handles verbosity levels and prompt behavior
   - `command_echo(cmd)`: Prints `$ <cmd>` in verbose mode
   - `info()`, `error()`, `warning()`, `success()`: Formatted output
   - Constructor takes `verbose`, `quiet`, `yes`, and `noninteractive` flags.
   - `prompt(message, default)`: Interactive prompt. Respects `--yes` (auto-accept with default; still asks when `default=None`) and `ADMT_NONINTERACTIVE` (error instead of prompt).
   - Color output via ANSI codes when TTY is detected. Plain text when piped or when `NO_COLOR` is set. ANSI codes from redo output are also stripped when not connected to a TTY.

4. **Add remaining `env` subcommands to `commands/env.py`:**
   - `EnvStartCommand`, `EnvStopCommand`, `EnvRestartCommand`, `EnvLoginCommand`, `EnvStatusCommand`
   - `EnvBuildCommand`, `EnvPushCommand`, `EnvPullCommand`
   - `EnvExecCommand`: goes through proxy script; detects TTY via `os.isatty(0)` and adds `-it` flags if interactive (see [ARCHITECTURE.md](ARCHITECTURE.md) TTY and Stdin Handling)
   - `EnvRefreshCommand`, `EnvRmCommand`, `EnvListCommand`

5. **Wire all into `cli.py`** with appropriate Click decorators.

6. **Implement signal handling:**
   - Register SIGINT handler that sends SIGINT to the active subprocess
   - Print the container PID (if known) so the user can manually kill it if needed
   - Exit with code 130 after cleanup

7. **Write unit tests:**
   - Container service: start, stop, restart, status, rm, exec (with mocked Docker adapter)
   - Output service: verbose mode, quiet mode, prompt with --yes, prompt with ADMT_NONINTERACTIVE, color/no-color
   - Docker adapter: command construction (verify correct args passed to subprocess)
   - Signal handling: SIGINT propagation

8. **Write integration tests:**
   - `admt env status` reports correct state
   - `admt env start` starts the container
   - `admt env stop` stops it
   - `admt env restart` restarts it
   - `admt env exec "echo hello"` returns "hello"
   - `admt env login` opens a shell (test that it invokes correctly)
   - `admt env pull` pulls the image
   - `admt env rm` removes the container (prompts for confirmation)
   - `admt env rm --image` removes container and image
   - `admt env list` shows registered projects
   - All commands with no active project print helpful error

### Deliverable

```bash
$ admt env status
Project: adamant_example
Container: adamant_example_container
Status: running

$ admt env exec "echo hello from container"
hello from container

$ admt env stop
Stopping adamant_example... done.

$ admt env restart
Stopping adamant_example... done.
Starting adamant_example... done.
```

> **Gate:** Phase 2 is complete only when the [per-phase quality gate](#per-phase-quality-gate) passes. Do not start Phase 3 until it does.

---

## Phase 3: Build Passthrough

**Goal:** All redo passthrough commands work: `build`, `what`, `test [--all]`, `style`, `clean [--all]`, `prove`, `coverage [--all]`, `publish`.

### Steps

1. **Implement `adapters/redo.py`:**
   - Constructs redo command strings
   - Maps admt commands to redo targets:
     - `build` -> `redo all` (or `redo <target>`)
     - `what` -> `redo what`
     - `test` -> `redo test`, `test --all` -> `redo test_all`
     - `style` -> `redo style`, `style --all` -> `redo style_all`
     - `analyze` -> `redo analyze`, `analyze --all` -> `redo analyze_all`
     - `clean` -> `redo clean`, `clean --all` -> `redo clean_all`
     - `prove` -> `redo prove`
     - `coverage` -> `redo coverage`, `coverage --all` -> `redo coverage_all`
     - `publish` -> `redo publish`, `publish --all` -> `redo publish_all`
   - When `--debug` is set, prepend `DEBUG=1` to the redo command (e.g., `DEBUG=1 redo all`)

2. **Implement `ContainerPassthroughCommand` in `commands/base.py`** (if not already done in Phase 0):
   - Standard flow: load config -> map path -> check container -> ensure env snapshot -> exec redo -> stream output -> return result
   - Handles the optional positional argument: if it's a directory, populates `Context.path` (changes working directory); if it's a file or target name, populates `Context.target` (passed to redo). Paths are resolved via `Path.resolve(strict=False)` and mapped to container paths. See [ARCHITECTURE.md](ARCHITECTURE.md) Passthrough with Optional Path.
   - **Output routing and color passthrough:** redo sends human output to stderr. admt merges this into stdout using `stderr=subprocess.STDOUT` at the subprocess level (see [ARCHITECTURE.md](ARCHITECTURE.md) Output Routing). This preserves ANSI color codes because the merged stream still passes through the terminal's TTY. In `--quiet` mode, capture both streams with `subprocess.PIPE` and only print on failure.

3. **Implement individual commands** -- each is a thin subclass of `ContainerPassthroughCommand`:
   - `commands/build.py` -- `BuildCommand`: target defaults to `all`, accepts optional target arg
   - `commands/what.py` -- `WhatCommand`: target is `what`, accepts optional dir arg
   - `commands/test_cmd.py` -- `TestCommand`: target is `test`, `--all` / `-a` flag switches to `test_all`
   - `commands/style.py` -- `StyleCommand`: target is `style`, `--all` / `-a` flag switches to `style_all`
   - `commands/analyze.py` -- `AnalyzeCommand`: target is `analyze`, `--all` / `-a` flag switches to `analyze_all`
   - `commands/clean.py` -- `CleanCommand`: target is `clean`, `--all` / `-a` flag switches to `clean_all`
   - `commands/prove.py` -- `ProveCommand`: target is `prove`
   - `commands/coverage.py` -- `CoverageCommand`: target is `coverage`, `--all` / `-a` flag switches to `coverage_all`
   - `commands/publish.py` -- `PublishCommand`: target is `publish`, `--all` / `-a` flag switches to `publish_all`

4. **Wire all into `cli.py`** with short aliases:

   | Command | Click name | Alias |
   |---------|-----------|-------|
   | `admt build` | `build` | `b` |
   | `admt what` | `what` | `w` |
   | `admt test` | `test` | `t` |
   | `admt style` | `style` | `s` |
   | `admt analyze` | `analyze` | `an` |
   | `admt clean` | `clean` | `cl` |
   | `admt prove` | `prove` | `p` |
   | `admt coverage` | `coverage` | `cov` |
   | `admt publish` | `publish` | `pub` |

   Register aliases on the `AliasedGroup` defined in Phase 0 (e.g., `cli.add_alias("b", "build")`). The `format_commands` method on `AliasedGroup` should also be extended to show aliases in `--help` output.

5. **Handle the "container not running" case:**
   - If container is not running, prompt: "Container not running. Start it? [Y/n]"
   - With `--yes`: auto-start without prompting
   - With `ADMT_NONINTERACTIVE`: error with exit code 2 and message suggesting `admt env start`

6. **Handle the "no project configured" case:**
   - If no `~/.admt/config.yml` or no active project: error with exit code 2 and message suggesting `admt env init`

7. **Write unit tests:**
   - Each command: verify correct redo target is constructed
   - Path mapping: cwd mapped correctly, optional path argument resolved correctly
   - Container not running: prompt behavior with different flag combinations
   - No project configured: error behavior
   - Debug mode: verify `DEBUG=1` is prepended

8. **Write integration tests:**
   - `admt build` from a component directory builds successfully
   - `admt build ../other_component/` builds the other component
   - `admt what` lists targets
   - `admt test` runs tests
   - `admt style` checks style
   - `admt clean` cleans
   - Each short alias works (`admt b`, `admt t`, etc.)
   - Running from a directory not in any volume mount gives clear error

### Deliverable

```bash
$ cd ~/projects/adamant_example/src/components/ccsds_router/
$ admt build
redo  all
redo    build/obj/Linux/...
...

$ admt what
build/src/component-ccsds_router.ads
build/src/component-ccsds_router.adb
test
style
all

$ admt -v build
$ docker compose -f .../docker-compose.yml exec -u user adamant_example /tmp/admt/adamant_example/exec.sh bash -c "cd /home/user/adamant_example/src/components/ccsds_router && redo all"
redo  all
...

$ admt b  # short alias
redo  all
...
```

> **Gate:** Phase 3 is complete only when the [per-phase quality gate](#per-phase-quality-gate) passes. Do not start Phase 4 until it does.

---

## Phase 4: Templates Command

**Goal:** `admt templates` runs `redo templates` and optionally copies generated stubs. `admt templates --undo` restores overwritten files.

### Steps

1. **Implement `commands/templates.py`:**
   - Extends `ContainerPassthroughCommand` with post-exec behavior
   - After `redo templates` succeeds:
     a. Identify generated stub files in the `build/src/` directory on the host (accessible via the volume mount). Look for Ada `.ads` and `.adb` files matching the `component-<name>-implementation.*` pattern.
     b. Prompt user: "Copy implementation stubs to source directory? [Y/n]"
        - With `--yes`: auto-copy
        - With `ADMT_NONINTERACTIVE`: skip copy (just run redo templates)
     c. If copying:
        - Check if implementation files already exist in the source directory
        - If they exist, back them up to `/tmp/admt-backup-XXXX/`
        - Print the backup location clearly so the user can restore if needed
        - Copy the generated stubs from `build/` to the source directory
        - Record the backup path in `/tmp/admt-backup-latest` (a plain text file containing the absolute path)
     d. Report what was done

   - `--undo` mode:
     a. Read the backup path from `/tmp/admt-backup-latest`
     b. If no backup exists, error: "No template backup found. Nothing to undo."
     c. Copy backed-up files back to their original locations
     d. Report what was restored

2. **Wire into `cli.py`** with alias `tmpl`.

3. **Write unit tests:**
   - Template detection logic (finding stubs in build/)
   - Backup logic (existing files backed up correctly)
   - Copy logic (files end up in the right place)
   - Undo logic (files restored from backup)
   - Prompt behavior with different flags
   - No backup exists for --undo

4. **Write integration tests:**
   - `admt templates` in a component directory runs redo templates
   - With `--yes`, stubs are copied automatically
   - Existing files are backed up before overwrite
   - `admt templates --undo` restores backed-up files

### Deliverable

```bash
$ cd ~/projects/adamant_example/src/components/my_component/
$ admt templates
redo  templates
...
Generated stubs found:
  build/src/component-my_component-implementation.ads
  build/src/component-my_component-implementation.adb

Copy to source directory? [Y/n] y
Backed up existing files to /tmp/admt-backup-a1b2c3/
  component-my_component-implementation.ads
  component-my_component-implementation.adb
Copied 2 files.

$ admt templates --undo
Restored from /tmp/admt-backup-a1b2c3/:
  component-my_component-implementation.ads
  component-my_component-implementation.adb
```

> **Gate:** Phase 4 is complete only when the [per-phase quality gate](#per-phase-quality-gate) passes. Do not start Phase 5 until it does.

---

## Phase 5: Global Flags and Polish

**Goal:** All global flags work correctly across all commands. Error messages are helpful. Edge cases are handled.

### Steps

1. **`--verbose` (`-v`):**
   - Passthrough commands print the full `docker compose exec ...` command before executing
   - Prefixed with `$ ` to distinguish from output
   - Verify across all commands

2. **`--quiet` (`-q`):**
   - Successful commands produce no output -- only exit code
   - Failed commands still print error information
   - Verify across all commands

3. **`--debug` (`-d`):**
   - Implies `--verbose` (all verbose behavior applies)
   - Prepends `DEBUG=1` to redo commands in the container, e.g., `DEBUG=1 redo all`
   - This enables Adamant's redo-level debug output for diagnosing build system issues
   - Verify that debug output from redo is streamed correctly

4. **`--yes` (`-y`):**
   - Prompts with a default value auto-accept that default
   - Still asks when there is no sensible default (e.g., "Which compose file?" when multiple found)
   - Container auto-starts if needed
   - Templates auto-copy

5. **`--force` (`-f`):**
   - For `templates` command: skips the confirmation prompt but still creates the backup
   - Future: for create commands, overwrite existing files
   - Not applicable to most passthrough commands (but the flag is accepted and ignored gracefully)

6. **`ADMT_NONINTERACTIVE` environment variable:**
   - All prompts become errors with specific messages about what flag to pass
   - Test with and without the variable set

7. **`ADMT_ENV` environment variable:**
   - Overrides active project for a single invocation
   - Error if project name not found in config

8. **Error message review:**
   - Every error path produces a message that names the problem and suggests a fix
   - No generic "Error: command failed" messages
   - On redo failure, print the failed command even without `-v`

9. **Shell completion:**
   - Click provides this for free with `_ADMT_COMPLETE` environment variable
   - Add instructions to `admt --help` or `admt env init` output for how to enable
   - Test that completion works for commands, subcommands, and aliases

10. **Write tests** for every flag combination across representative commands.

### Deliverable

All flags work consistently. Error messages are helpful. Shell completion is available.

> **Gate:** Phase 5 is complete -- and the MVP itself is complete -- only when the [per-phase quality gate](#per-phase-quality-gate) passes. This is the final phase before post-MVP work begins.

---

## Command Reference

Complete list of MVP commands with their redo equivalents:

| admt Command | Alias | Redo Equivalent | Container? | Notes |
|-------------|-------|-----------------|------------|-------|
| `admt env init [path]` | `admt e init` | N/A | No | Register project, verify markers, parse compose file |
| `admt env use <name>` | `admt e use` | N/A | No | Switch active project |
| `admt env start` | `admt e start` | N/A | No | Auto-pull if needed, `docker compose up -d` + activate |
| `admt env stop` | `admt e stop` | N/A | No | `docker compose stop` |
| `admt env restart` | `admt e restart` | N/A | No | stop + start |
| `admt env login` | `admt e login` | N/A | Yes | Interactive bash shell (env activated via .bashrc) |
| `admt env status` | `admt e status` | N/A | No | Check container state |
| `admt env build` | `admt e build` | N/A | No | `docker compose build` |
| `admt env push` | `admt e push` | N/A | No | `docker compose push` |
| `admt env pull` | `admt e pull` | N/A | No | `docker compose pull` |
| `admt env exec <cmd>` | `admt e exec` | N/A | Yes | Exec through proxy script; TTY auto-detected |
| `admt env refresh` | `admt e refresh` | N/A | Yes | Re-run activate + rebuild snapshot |
| `admt env list` | `admt e list` | N/A | No | List registered projects |
| `admt env rm` | `admt e rm` | N/A | No | Remove container (`--volumes`, `--image`, `--remove-all`) |
| `admt build [path]` | `admt b` | `redo all` or `redo <target>` | Yes | Default: all in cwd |
| `admt what [path]` | `admt w` | `redo what` | Yes | List buildable targets |
| `admt test [path]` | `admt t` | `redo test` | Yes | `--all` / `-a` for `redo test_all` |
| `admt style [path]` | `admt s` | `redo style` | Yes | `--all` / `-a` for `redo style_all` |
| `admt analyze [path]` | `admt an` | `redo analyze` | Yes | `--all` / `-a` for `redo analyze_all` |
| `admt clean [path]` | `admt cl` | `redo clean` | Yes | `--all` / `-a` for `redo clean_all` |
| `admt prove [path]` | `admt p` | `redo prove` | Yes | SPARK formal verification |
| `admt coverage [path]` | `admt cov` | `redo coverage` | Yes | `--all` / `-a` for `redo coverage_all` |
| `admt publish [path]` | `admt pub` | `redo publish` | Yes | `--all` / `-a` for `redo publish_all` |
| `admt templates [path]` | `admt tmpl` | `redo templates` | Yes | + optional stub copy |
| `admt templates --undo` | `admt tmpl --undo` | N/A | No | Restore from last backup |

---

## Roadmap (Post-MVP)

These capabilities build on the MVP foundation. The architecture supports them without refactoring.

### Next Up: CI Pipeline and Packaging

Immediately after MVP, before feature work:

- **CI pipeline (GitHub Actions):** the same four commands listed in the [Per-phase quality gate](#per-phase-quality-gate) (`ruff format --check`, `ruff check`, `mypy src/`, `pytest --cov --cov-branch --cov-fail-under=100`) run on every push. Container smoke tests on PR merge. The workflow is structured so that developers and agents can rehearse it locally with [`act`](https://github.com/nektos/act) before pushing -- no surprises at the PR boundary. (`act` is post-MVP: it runs the workflow file that is added in this step. During MVP, the local gate is the only gate.)
- **Documentation and packaging:** README finalization, shell completion setup scripts, `CLAUDE.md` agent configuration, publishable package.
- **Upstream contract tests:** Weekly verification of `docker compose` output format, `redo what` output format, compose file structure.

### Tier 2: Creation and Introspection

- `admt create component <name>` -- Schema-driven component creation (YAML + directory structure)
- `admt create record <name>`, `admt create array <name>`, `admt create enum <name>` -- Type creation
- `admt create test`, `admt create doc` -- Scaffolding for component subdirectories
- `admt info <name>` -- Inspect an entity's structure (read YAML, display summary)
- `admt validate [file]` -- Host-side or container-side schema validation
- `admt init [path]` -- Generate and copy implementation stubs (promoted from templates flow)
- Schema service and filesystem service (staging/commit/rollback)
- Interactive wizard mode for creation commands
- Per-project `.admt.yml` configuration file

### Tier 3: Mutation

- `admt add event`, `admt add command`, `admt add connector`, `admt add data-product`
- `admt add` auto-detects context from cwd (component, test, assembly)
- `--dry-run` for all commands (preview changes)

### Tier 4: Assembly and Project

- `admt create assembly <name>` -- Assembly scaffolding with wiring
- `admt add connection` -- Wire components in an assembly
- `admt create project <name>` -- Full project from scratch
- Cross-model validation (events have matching connectors, etc.)

### Tier 5: Advanced

- Plugin system via Python entry points
- Shell completion for component names and paths
- Batch operations (`admt build` on multiple directories)
- Build-path awareness (`.all_path` markers, `BUILD_PATH`, `BUILD_ROOTS`)
- COSMOS plugin generation
