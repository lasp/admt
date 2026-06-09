# admt Architecture

This document defines the internal architecture of admt. It is the authoritative reference for how the system is structured, how components interact, and what rules govern dependencies. Code examples are illustrative starting points -- not gospel -- and should be adapted as implementation proceeds. The architectural *principles* and *dependency rules* are non-negotiable.

Together with CODING_RULES.md, MVP_PLAN.md, and TEST_PLAN.md, it defines what admt does and how it is built. Implementation must trace to these documents. Changes to behavior, interfaces, or structure require a design amendment here first -- not just a code change.

## Table of Contents

- [Requirements](#requirements)
- [Layers](#layers)
- [Directory Structure](#directory-structure)
- [Dependency Rules](#dependency-rules)
- [Project Discovery and Configuration](#project-discovery-and-configuration)
- [Container Passthrough](#container-passthrough)
- [Output Routing](#output-routing)
- [Command Structure](#command-structure)
- [Services](#services)
- [Global Flags](#global-flags)
- [Exit Codes](#exit-codes)
- [Signal Handling](#signal-handling)
- [ANSI Color Handling](#ansi-color-handling)
- [TTY and Stdin Handling](#tty-and-stdin-handling)
- [Behavioral Details](#behavioral-details)
- [Non-Goals](#non-goals)
- [Plugin System (Roadmap)](#plugin-system-roadmap)
- [How This Scales](#how-this-scales)

---

## Requirements

These are the constraints that every feature must satisfy. They are not aspirations -- they are invariants. Every design decision, implementation choice, and command must trace to one or more of these.

### R1. Correct by Construction

All YAML output produced by admt is valid against Adamant's pykwalify schemas before it is written. Files that pass admt's own validation also pass Adamant's validators. **Every `admt create` output builds and passes tests immediately** -- a scaffolded component compiles and passes its empty test harness out of the box. If it doesn't, that's a bug in admt, not a user error.

### R2. Schema-Driven, Never Invents Semantics

admt derives its knowledge of Adamant model types from Adamant's own pykwalify schemas (`gen/schemas/*.yaml`), not from hardcoded field lists. Future wizard prompts, CLI flags, and validation are all generated from schema structure. When Adamant adds a new schema or model field, admt picks it up with minimal or zero changes. **admt is a thin layer, not an alternative implementation.** It never invents Adamant semantics.

**Exception:** Submodel relationships (which model types coexist in a directory -- e.g., `.events.yaml` alongside `.component.yaml`) are not expressed in schemas. admt maintains a small hardcoded mapping of these relationships. File contents are always schema-driven; file relationships require explicit knowledge.

### R3. Thin Wrapper

admt composes existing tools -- it does not reimplement them:
- **redo** does all building, testing, and code generation
- **pykwalify** does all schema validation
- **Docker/docker-compose** does all container management
- **Adamant's Jinja2-based generators** do all code generation (inside the container, not invoked by admt directly)

admt adds value through orchestration, context detection, output formatting, and encoding of idiomatic patterns. If admt finds itself reimplementing something redo or the generators already do, that's a design error. **admt never runs concurrent redo processes** -- it respects the single-redo constraint that protects build system integrity.

### R4. Host-Only, Container-Forwarding

admt runs on the host machine only. It is never executed inside the Adamant container. Commands that need the container (build, test, style, etc.) transparently forward to the container via `docker exec` (or `docker compose <subcommand>` for lifecycle ops -- see §"Compose vs. plain Docker"). Commands that do not (future: create, validate) run directly on the host. admt fails with a clear, actionable error when a container-required command cannot reach the container.

### R5. Non-Interactive Parity

Every choice that can be made through an interactive wizard must also be expressible as a CLI flag. No capability is wizard-only. A command run interactively and the equivalent command with explicit flags must produce identical output. admt is interactive by default; when `ADMT_NONINTERACTIVE` is set, missing arguments produce errors with non-zero exit codes instead of prompts. `--yes` auto-accepts prompts with their default values.

### R6. Atomic Operations

Commands that produce or modify files succeed or fail as a unit. All file writes go to a temporary staging directory (e.g., `/tmp/admt-xxx/`). Files are moved to their final destination only after all generation and validation succeeds. If any step fails, nothing is written and the working directory is left unchanged.

### R7. Build-Path Aware (Post-MVP)

admt respects Adamant's build path conventions (`.all_path` marker files, `BUILD_PATH`, `BUILD_ROOTS`) and the directory structure that redo and the generators expect. For the MVP, these are handled entirely by the container. Post-MVP `admt create` commands will create `.all_path` markers in generated directories.

### R8. Convention-Conforming

admt follows Adamant's existing naming patterns (`<name>.<type>.yaml` for models, `component-<name>-implementation.ads/.adb` for Ada files), directory structures, and organizational expectations. Output from admt is indistinguishable from output produced by an experienced Adamant developer.

### R9. Self-Documenting

Every command and subcommand has a `--help` that fully describes its behavior, options, and defaults, with concrete examples. An agent or new user can discover admt's full capabilities by walking the help tree from `admt --help`.

### R10. Composable

Commands do one thing and are chainable. `admt create component` creates the skeleton; `admt add event` adds to it. Commands document their preconditions explicitly.

### R11. Observable and Predictable Failure

admt clearly reports what it does. Every file created, modified, or validated is printed to stdout. There is no hidden state and no silent side effects. On failure:

- **Exit codes are meaningful and distinct.** 0 is success. Different non-zero codes distinguish user error, environment error, and command failure.
- **Error messages name the problem and the fix** -- not "operation failed", but specific diagnostics with actionable guidance.
- **The underlying command is always shown**, even without `--verbose`. Every container-forwarded operation reports what ran when it fails.
- **Partial writes do not persist.** Failed commands clean up after themselves.

### R12. Idempotent Where Possible

Running `admt create component foo` when `foo/` already exists does not silently overwrite it. Destructive operations require `--force`. Re-running the same command should produce the same result where it makes sense (e.g., `admt env use <already-active>` is a no-op).

### R13. Dual Audience

admt is designed to serve three audiences with distinct needs from the same codebase:

- **Humans** get terse, informative, colored terminal output by default
- **Scripts and CI** get meaningful exit codes, composable stdout, and `--quiet` mode
- **Agents** get non-interactive error mode via `ADMT_NONINTERACTIVE`

### R14. Extensible

New commands can be added without modifying the core. A plugin/entry-point system allows teams to register project-specific commands under the `admt` namespace.

---

## Layers

admt is a four-layer system with strict dependency direction: outer layers depend on inner layers, never the reverse.

```
+-----------------------------------------------------------+
|                       CLI Adapter                         |
|               (Click decorators -- thin)                  |
|          Translates CLI args -> Command calls             |
+-----------------------------------------------------------+
|                        Commands                           |
|           (One class per operation -- the core)           |
|     build, test, style, env, what, clean, prove, ...     |
+-----------------------------------------------------------+
|                        Services                           |
|             (Shared capabilities -- reusable)             |
|    container, schema, filesystem, output, config, ...     |
+-----------------------------------------------------------+
|                        Adapters                           |
|           (External system interfaces -- swappable)       |
|         Docker, pykwalify, redo, ruamel.yaml              |
+-----------------------------------------------------------+
```

### CLI Adapter

The thinnest possible layer. Uses [Click](https://click.palletsprojects.com/) for:

- **Nested command groups** that map to admt's structure (`admt env start`, `admt build`, etc.)
- **Shell completion** for bash, zsh, and fish (built in to Click)
- **Help generation** from docstrings and option metadata
- **Type validation** on options (Choice, Path, IntRange, etc.)

The CLI adapter defines Click groups and commands, parses arguments, creates the `Context` with services wired up (see [Service Wiring](#service-wiring)), and calls the corresponding command class. **It contains no logic** -- no validation, no file I/O, no container detection. Commands and services never import Click. If admt ever needs a different frontend (Python API, web UI), only this layer changes.

### Commands

The core of the system. Each command is a class that declares what it needs (which services), validates its inputs, orchestrates the operation, and returns a structured result. Adding a new command means adding one class -- no touching the CLI adapter, no touching services.

### Services

Shared capabilities that multiple commands use. Each service encapsulates a single responsibility (container management, schema validation, filesystem operations) and exposes a clean interface. Services depend on adapters, not on commands or the CLI.

Services may depend on other services, but the dependency graph must remain a DAG (no cycles). Prefer injecting data from a service over injecting the service itself. Services do not use module globals or singletons. Per-invocation state (e.g., a staging area) is acceptable but must be scoped to the current command.

Only adapters may import `subprocess`. Services interact with external tools through adapters.

### Adapters

Wrap external tools and libraries (Docker, pykwalify, redo, ruamel.yaml) behind stable interfaces. If Adamant switches from pykwalify to another validator, or from Docker to Podman, only the adapter changes.

---

## Directory Structure

```
src/
  admt/
    __init__.py
    main.py                    # Entry point + SIGINT handler
    cli.py                     # Click adapter -- thin, no logic
    cli_utils.py               # AliasedGroup (Click subclass with aliases)
    bootstrap.py               # Service wiring (Context + ContainerService)
    context.py                 # Context and Result classes
    exceptions.py              # All admt-specific exceptions

    commands/                  # One file per command or command group
      __init__.py              # Command registry, discovery
      base.py                  # Command + ContainerPassthroughCommand bases
      build.py                 # admt build
      test_cmd.py              # admt test (avoid shadowing pytest)
      style.py                 # admt style
      analyze.py               # admt analyze
      clean.py                 # admt clean
      prove.py                 # admt prove
      coverage.py              # admt coverage
      publish.py               # admt publish
      templates.py             # admt templates
      what.py                  # admt what
      env.py                   # admt env {init, use, start, stop, ...}

    services/                  # Shared capabilities
      __init__.py
      container.py             # Container lifecycle + exec + recovery
      env_snapshot.py          # /tmp/admt/<project>/ snapshot proxy
      config.py                # Project registry, active project, ~/.admt/
      path_mapper.py           # Host <-> container path mapping
      output.py                # Structured output formatting
      schema.py                # Schema discovery, validation (post-MVP)
      filesystem.py            # Staged atomic file operations (post-MVP)

    adapters/                  # External system wrappers
      __init__.py
      docker.py                # docker / docker compose interaction
      redo.py                  # redo command-string builder
      redo_output.py           # Rewrite redo output into admt vocabulary
      yaml_adapter.py          # YAML reading/writing (ruamel.yaml)
      pykwalify_adapter.py     # Schema validation (post-MVP)

tests/
  unit/                        # Fast, no Docker required
    commands/
    services/
    adapters/
    test_architecture.py       # Layer rules + Context-mutation lint
  integration/                 # Shell calls to admt CLI
  container/                   # Full pipeline against real container (post-MVP)
  conftest.py                  # Shared fixtures

pyproject.toml
```

A few notes on the layout:

- **`bootstrap.py` and `cli_utils.py`** live outside `cli.py` so the architectural cap on CLI callback length (15 lines per function body, enforced by `tests/unit/test_architecture.py::test_cli_functions_are_short`) is not strained by service instantiation or `AliasedGroup` machinery. `cli.py` imports from them and stays a thin adapter.
- **`adapters/redo_output.py`** is the line-rewriter described in [§Output Rewriting](#output-rewriting). It legitimately belongs in `adapters/` (it translates output from an external tool); pulling it into `services/` would force a service to depend on string-rewrite plumbing that is fundamentally about how redo formats lines.
- **`services/env_snapshot.py`** holds the `/tmp/admt/<project>/` snapshot machinery (capture baseline + activated env, diff, write `env_snapshot.sh` + `exec.sh`). Split out from `container.py` because env-parsing, shell-quote escaping, and container-side file paths are an orthogonal concern from lifecycle and exec/recovery.

---

## Dependency Rules

These rules prevent the architecture from degrading as the codebase grows. They are enforced by automated tests (see [TEST_PLAN.md](TEST_PLAN.md)).

1. **CLI adapter** depends on **commands**. Nothing else depends on the CLI adapter.
2. **Commands** depend on **services**. Commands never depend on other commands. Commands never import Click.
3. **Services** depend on **adapters**. Services never depend on commands or the CLI. Services may depend on other services (DAG only, no cycles).
4. **Adapters** depend on external libraries only. Adapters never depend on services, commands, or the CLI.
5. **No circular dependencies.** The dependency graph is a strict DAG: CLI -> Commands -> Services -> Adapters.

If a developer or agent finds themselves needing to violate these rules, it is a signal that something belongs in a different layer, not that the rules should be relaxed.

---

## Project Discovery and Configuration

### The Problem

An Adamant project typically consists of multiple git repositories laid out as siblings under a common parent directory:

```
~/projects/
  adamant_example/                 # "Master" project repo (has docker-compose.yml)
    docker/
      docker-compose.yml       # Defines container, mounts all siblings
      adamant_env.sh           # Legacy environment script (admt replaces this)
    env/
      activate                 # Environment activation script
      activate_from_snapshot   # Cached fast activation
      container_run.sh         # Legacy exec wrapper
    src/
      components/
      assembly/
    gen/
      generators/
      templates/
  adamant/                     # The Adamant framework
    gen/
      schemas/                 # THE schema definitions (21 .yaml files)
      models/
      generators/
    src/
      components/              # Framework components
  adamant-xmera-components/    # Shared component library (no docker dir)
    src/
      components/
  other-dependency/            # Other repos mounted into container
```

Key observations from real Adamant projects:

- The master project's `docker-compose.yml` mounts all sibling repos as bind volumes
- Volume mounts use relative paths: `../../adamant` -> `/home/user/adamant`
- The container's home directory is `/home/user/`
- Each repo in the container lives at `/home/user/<repo-name>`
- `adamant/` has its own simpler `docker-compose.yml` (only mounts itself) for standalone development
- Some repos (like `adamant-xmera-components/`) have no docker dir at all
- The `env/activate` script in the project sets `ADAMANT_DIR`, `PROJECT_DIR`, and other environment variables

A developer may be working in `adamant/src/components/foo/` but want to build using `adamant_example`'s container (not adamant's standalone container). admt must know which project context to use.

### Project Root Markers

admt identifies an Adamant project root by the presence of **all three** of the following markers:

1. **`default.do`** -- Identifies an Adamant redo project.
2. **A docker compose YAML file in `docker/`** -- Any `.yml` or `.yaml` file in the `docker/` subdirectory. The filename is flexible (e.g., `docker-compose.yml`, `docker-compose.yaml`, `compose.yml`).
3. **`env/activate`** -- The environment activation script.

All three markers must be present in the same directory for admt to recognize it as a valid project root.

### Configuration Store: `~/.admt/config.yml`

admt stores project configuration centrally at `~/.admt/config.yml`. This file is created and managed by `admt env init` and `admt env use`.

```yaml
# ~/.admt/config.yml
version: 1                         # Config schema version for future migration
active_project: adamant_example

projects:
  adamant_example:
    compose_file: /Users/dev/projects/adamant_example/docker/docker-compose.yml
    compose_file_mtime: 1744646400123456789  # st_mtime_ns of compose file when config last derived
    env_file: /Users/dev/projects/adamant_example/docker/.env  # colocated .env, if present (else null)
    env_file_mtime: 1744646400123456789      # st_mtime_ns of .env when config last derived
    service_name: adamant_example
    container_name: adamant_example_container  # RESOLVED value (see "Compose Parsing" below)
    project_root: /Users/dev/projects/adamant_example
    container_home: /home/user
    volume_mounts:
      /Users/dev/projects/adamant: /home/user/adamant
      /Users/dev/projects/adamant_example: /home/user/adamant_example
      /Users/dev/projects/adamant-xmera-components: /home/user/adamant-xmera-components
      # ... all other mounts from docker-compose.yml
    activate_script: /home/user/adamant_example/env/activate

  adamant-standalone:
    compose_file: /Users/dev/projects/adamant/docker/docker-compose.yml
    compose_file_mtime: 1744560000
    service_name: adamant
    container_name: adamant_container
    project_root: /Users/dev/projects/adamant
    container_home: /home/user
    volume_mounts:
      /Users/dev/projects/adamant: /home/user/adamant
    activate_script: /home/user/adamant/env/activate
```

The stored `service_name`, `container_name`, and `volume_mounts` are **resolved** values (see [Compose Parsing](#compose-parsing)), not raw compose-file text -- so a parameterized compose like `container_name: ${COMPOSE_PROJECT_NAME:-adamant_example}_container` is stored as its fully-expanded result (e.g., `adamant_example-wt1_container`).

Per-shell active-project state is **not** stored here. It lives in a separate per-terminal store (see [Active Project Resolution](#active-project-resolution)).

### Compose Parsing

admt never reads the compose file as raw YAML to derive project metadata: a compose file may interpolate variables from a colocated `.env` (e.g. `name: ${COMPOSE_PROJECT_NAME:-adamant_example}`, parameterized host ports), and raw YAML would store the literal `${...}` strings. Because the container is targeted directly by `container_name` on the hot path ([Container Passthrough](#container-passthrough)), an unresolved name breaks every `build`/`test`/`exec`.

Instead, admt derives `name`, `service_name`, `container_name`, and `volume_mounts` (with absolute host sources) from **`docker compose -f <compose_file> config --format json`**, which loads `.env`, resolves interpolation, and canonicalizes relative volume sources. This is the only correct source of truth and keeps admt a thin wrapper (R2, R3) rather than reimplementing Docker's interpolation grammar.

This invocation runs only at registration, on `env refresh`, and on the first command after the compose file or `.env` changes (see [Config Auto-Update](#config-auto-update)) -- never on the per-command hot path, which reads the cached resolved values from `~/.admt/config.yml`. Because admt shells out to `docker compose config`, the docker CLI must be installed (not necessarily running) for `env init`/`refresh`.

Caveat (Docker semantics, not admt's): variables exported in the calling shell outrank the `.env` file during interpolation. A globally-exported `COMPOSE_PROJECT_NAME` would make every project resolve to that name. admt does not work around this -- the resolved values faithfully reflect what `docker compose up` would actually do in that shell.

### Config Auto-Update

admt records both `compose_file_mtime` and `env_file_mtime` (the **nanosecond** modification timestamps, `st_mtime_ns`, of `docker-compose.yml` and its colocated `.env`, at the time the project config was last derived) in `~/.admt/config.yml` for each project. Nanosecond granularity means two edits within the same second cannot slip past the check. On every admt command that uses an active project, admt stats both files and compares their current mtimes to the stored values; any mismatch triggers a re-derive. These two `stat()` calls are the only per-command cost; they are negligible.

- **If neither file is newer**, admt proceeds normally using the cached resolved values.
- **If either is newer**, admt re-derives the project via `docker compose config` (see [Compose Parsing](#compose-parsing)), updates the stored `service_name`, `container_name`, `volume_mounts`, `compose_file_mtime`, and `env_file_mtime`, and prints a concise notice to stdout describing the change (e.g., `Updated config: added mount ../../new-repo -> /home/user/new-repo`). admt then continues with the original command.

The `.env` is tracked because it drives interpolation: changing `COMPOSE_PROJECT_NAME` or a host port in `.env` changes the resolved `container_name`/mounts even though the compose file itself is untouched. The auto-update means users never re-run `admt env init` after editing either file. The update is non-interactive (just a one-line notice) and happens regardless of `--yes` or `ADMT_NONINTERACTIVE`. If the compose file is deleted or unreadable, admt errors clearly. A `.env` that was present at registration but later removed is treated as a change (re-derive with no env file).

### `admt env init`

Registers a new project with admt. Must be run from the project root directory, or with an explicit path to a project root. admt verifies that all three project root markers are present, then derives project metadata via `docker compose config` ([Compose Parsing](#compose-parsing)) to extract:

- Project name (the resolved top-level `name:`, after `.env` interpolation)
- Service name (if multiple services, selects the one that bind-mounts `adamant/`)
- Container name (the resolved `container_name:`)
- All volume mounts, with absolute host sources (builds the host <-> container path map)
- Project root (the directory containing the markers)

If multiple docker compose YAML files are found in `docker/`, admt prompts the user to select one. In `ADMT_NONINTERACTIVE` mode, this is an error with exit code 3.

```bash
# Run from project root (auto-discovers docker compose file in docker/)
cd ~/projects/adamant_example/
admt env init

# Explicit path to project root
admt env init ~/projects/adamant_example/
```

If any marker is missing, admt exits with a clear error:

```
Error: Not a recognized Adamant project root: /path/to/directory
Missing markers:
  - env/activate (not found)
Run 'admt env init' from a directory with default.do, docker/*.yml, and env/activate.
```

The `activate_script` path is derived by convention: `<container_project_root>/env/activate`. If this file does not exist in the container, admt warns but still functions (exec commands just won't have the environment activated).

After init, the new project becomes the active project automatically.

#### Re-running `admt env init` on a registered project

If the project root is already registered in `~/.admt/config.yml`, `admt env init` re-parses the compose file and updates the existing entry in place (rather than failing or duplicating). The behavior depends on flags:

- **Interactive (default):** prompt `Project '<name>' is already registered. Overwrite? [y/N]` with default **No**.
- **`--force` (`-f`):** replace the entry without prompting.
- **`--yes` (`-y`):** the default of the prompt is `No`, so `--yes` declines the overwrite and exits cleanly with a notice (`Project already registered; not overwriting. Re-run with --force to replace.`). This is consistent with `--yes` meaning "accept the default", not "do the dangerous thing".
- **`ADMT_NONINTERACTIVE` without `--force`:** error with exit code 3 and message `Project '<name>' is already registered. Re-run with --force to overwrite.`
- **`ADMT_NONINTERACTIVE` with `--force`:** replace without prompting.

Note: passive compose-file changes (added volume mounts, renamed services) are picked up automatically on every command via the `compose_file_mtime` mechanism described in [Config Auto-Update](#config-auto-update). Re-running `admt env init` is only necessary when the user explicitly wants to re-register the project.

### `admt env use`

Switches the active project:

```bash
admt env use adamant_example
admt env use adamant-standalone
```

`env use` writes **two** things: the per-terminal session entry (so the *current* terminal switches immediately) and the global `active_project` (so *new* terminals default to the last project you used). See [Active Project Resolution](#active-project-resolution). The active project determines which container all commands target. This is critical when working in a repo (like `adamant/`) that is mounted into multiple project containers -- which is also why the active project cannot be inferred from the working directory: a shared mount like `adamant/` belongs to several projects at once.

### `ADMT_ENV` Override

The active project can be overridden for a single invocation by setting the `ADMT_ENV` environment variable:

```bash
# Build using adamant-standalone's container instead of the active project
ADMT_ENV=adamant-standalone admt build
```

The project name must be registered in `~/.admt/config.yml`. If the name is not found, admt exits with an error listing available projects.

### `admt env list`

Lists all registered projects and shows which is active:

```bash
$ admt env list
  adamant-standalone    ~/projects/adamant/docker/docker-compose.yml
* adamant_example           ~/projects/adamant_example/docker/docker-compose.yml
```

### Active Project Resolution

Different terminals must be able to target different projects at the same time -- e.g. one terminal per worktree, each on its own container -- while a single global default still applies to freshly opened terminals. admt achieves this with a **per-terminal session store** plus the global `active_project`, resolved in this order:

1. **`ADMT_ENV`** environment variable, if set. Explicit, per-invocation, always wins.
2. **Per-terminal session entry**, if one exists for this terminal (set by `admt env use`).
3. **Global `active_project`** from `~/.admt/config.yml` (the last project any terminal `use`d).

If none of these yields a registered project, admt prints:

```
No project configured. Run 'admt env init' to set up a project.
```

Whichever wins, admt then looks up that project's cached config (after the [Config Auto-Update](#config-auto-update) staleness check), and -- for container commands -- maps the host working directory to the container path via that project's volume mounts.

#### Per-Terminal Session Store

The session store gives admt per-shell memory **without** a shell shim, an `eval`, or any rc-file setup: a normal `admt env use` in one terminal cannot change another terminal's behavior, and a child process cannot export into its parent shell, so admt records the choice itself.

- **Key:** the controlling TTY of the admt process (e.g. `/dev/ttys003`), with the POSIX session id (`getsid`) stored alongside as a staleness guard. Two admt invocations in the same terminal share a TTY; a different terminal window has a different TTY.
- **Location:** `~/.admt/sessions.yml` (separate from `config.yml`; this is volatile per-terminal state, not project registry).
- **Write:** `admt env use <name>` records `<tty> -> {project: <name>, sid: <getsid>}` and also updates the global `active_project`.
- **Read:** resolution step 2 looks up the current TTY. The entry is honored only if its stored `sid` still matches the current session id; otherwise the TTY was recycled by a new terminal and the stale entry is ignored (it is dropped on the next session write, which prunes entries whose owning shell has exited). The store is a disposable cache: an unreadable or corrupt `sessions.yml` degrades to "no sessions" rather than failing commands, and a failed pin write (unwritable `~/.admt`, disk full) degrades to a warning -- the terminal then follows the global default. The terminal is identified by probing stdin, then stderr, then stdout for a tty -- so a piped stdin (`echo y | admt ...`) does not lose the terminal's pin.
- **Lazy auto-pin:** the first time a terminal resolves the active project via the global default (step 3), admt writes a session entry pinning that terminal to the resolved project. **Every resolution path pins** -- `env list`, `env status`, and passthrough commands alike -- because any command that has shown the user which project the terminal is on has made a commitment; the terminal must stay on that project until told otherwise. This snapshots the inherited global into the terminal -- the no-shim equivalent of exporting `ADMT_ENV` at shell startup -- so that a later `env use` in another terminal (which moves the global) cannot change what an already-resolved terminal targets. Without it, an unpinned terminal would keep following every global change, leaking one terminal's `env use` into others. `ADMT_ENV`-sourced resolutions are never persisted (explicit per-invocation overrides stay ephemeral), and a global naming an unregistered project is not pinned. New terminals still inherit the last-used global, then immediately pin themselves.
- **No controlling TTY** (CI, agents under `ADMT_NONINTERACTIVE`, piped or `xargs` invocations): the session layer is skipped entirely; resolution uses `ADMT_ENV` then global `active_project`. Those contexts should set `ADMT_ENV` explicitly.
- **Granularity is per-terminal, not per-process:** a subshell or script launched within a terminal inherits that terminal's active project.

This is intentionally observable state (R11), not hidden: `admt env status` reports the active project **and its source** -- `ADMT_ENV`, this terminal's session entry, or the global default -- so the user can always tell why a given project is active.

---

## Container Passthrough

This is the core mechanism for MVP. All build-related commands (`build`, `test`, `style`, `what`, `clean`, `prove`, `coverage`, `publish`, `templates`) work the same way:

### Compose vs. plain Docker

admt uses two distinct families of Docker invocations, picked deliberately:

- **`docker compose -f <compose_file> <subcommand>`** for **lifecycle ops** that need the compose file: `up`, `stop`, `down`, `build`, `push`, `pull`. These define volumes, env, and the service graph; the compose plugin is the right tool. One-shot, user-initiated calls -- the compose-plugin overhead doesn't matter here.
- **`docker exec -u user <container_name> ...`** and **`docker inspect <container_name>`** for the **hot path**: status probes, every passthrough exec, and snapshot-script I/O. These target the already-running container directly by name and bypass the compose plugin entirely.

The split is justified by performance. On Docker Desktop for Mac, every `docker compose` invocation eats ~3s parsing the compose file before reaching the daemon. For status probes (every command run does one) and exec (every passthrough does one), that ~3s dominates the round-trip. Plain `docker exec` on the resolved `container_name` is roughly **20x faster**. Lifecycle ops only happen on user request (`admt env start`, `admt env stop`), so the overhead is amortized over a user-perceived "I'm starting the container" moment and doesn't matter.

The `DockerAdapter` exposes both families (`compose_*` vs. `docker_*`) explicitly so the call site picks the right one. `ContainerService` routes lifecycle through `compose_*` and exec/status/snapshot I/O through `docker_*`.

### Path Mapping

admt maps the host working directory to the container path using the volume mounts from the project config.

Example: Developer is at `~/projects/adamant/src/components/ccsds_router/` on the host. The active project is `adamant_example`. The volume mounts say:

```
/Users/dev/projects/adamant -> /home/user/adamant
```

So the container working directory is `/home/user/adamant/src/components/ccsds_router/`.

If the current host directory is not under any volume mount for the active project, admt fails with a clear error:

```
Error: Current directory is not mapped into the 'adamant_example' container.
Mapped directories:
  ~/projects/adamant -> /home/user/adamant
  ~/projects/adamant_example -> /home/user/adamant_example
  ...
```

### Environment Activation

admt does **not** depend on the project's `container_run.sh`, `adamant_env.sh`, or `activate_from_snapshot`. It assumes only that the project has an `env/activate` script at the conventional location inside the container (e.g., `/home/user/adamant_example/env/activate`). admt owns its own environment caching.

All admt container scripts are stored in a per-project directory: `/tmp/admt/<project>/` (e.g., `/tmp/admt/adamant_example/`). This prevents collisions when multiple users or CI jobs share a container.

**First exec (no snapshot exists):**

1. admt captures the environment **before** activation (the container's baseline) via a short bounded exec:
   ```bash
   docker exec -u user <container_name> env
   ```
   Output is parsed in Python on the host -- no temp file required.
2. admt runs the project's `env/activate` with **stdio inherited** so the user sees the script's progress live (first-run activation can take many minutes -- pip installs, alr builds, gprbuild of the Pico runtime; running it through a captured pipe would look like a hang). The final `env` dump is redirected to a container-side file so it doesn't flood the user's terminal:
   ```bash
   docker exec -u user <container_name> bash -c \
       "mkdir -p /tmp/admt/<project> && \
        source /home/user/<project>/env/activate && \
        env > /tmp/admt/<project>/env_activated"
   ```
   admt then reads the dump back via a short bounded `cat`:
   ```bash
   docker exec -u user <container_name> cat /tmp/admt/<project>/env_activated
   ```
3. admt diffs the two captures in Python and writes **only the changed/added variables** as a flat export script. The diff algorithm: parse each capture as key-value pairs (split on first `=`); skip lines whose key is not a valid POSIX shell identifier (filters activate-script chatter like `Note:` from leaking in as invalid `export`s). For each key in the activated set, if the key is absent from the baseline or has a different value, include it in the snapshot. Variables removed by activation are ignored (rare; not worth the complexity). The entire new value is stored (e.g., the full `PATH`, not a delta). Embedded `"` characters are backslash-escaped so the resulting `export KEY="..."` line is valid bash. The resulting script is written to `/tmp/admt/<project>/env_snapshot.sh` in the container via `docker_exec_with_stdin` (pipes the script content into a `bash -c "mkdir -p /tmp/admt/<project> && cat > .../env_snapshot.sh"`). Example content:
   ```bash
   #!/bin/bash
   # admt environment snapshot -- generated, do not edit
   # Only variables set or modified by env/activate
   export ADAMANT_DIR="/home/user/adamant"
   export PROJECT_DIR="/home/user/adamant_example"
   export ADAMANT_CONFIGURATION_YAML="/home/user/adamant_example/config/adamant_example.configuration.yaml"
   export PATH="/usr/gnat/bin:/home/user/.local/bin:..."
   ```
4. admt writes a proxy exec script to `/tmp/admt/<project>/exec.sh` in the container (using the same `docker_exec_with_stdin` mechanism):
   ```bash
   #!/bin/bash
   # Written by admt -- do not edit
   source /tmp/admt/<project>/env_snapshot.sh
   "$@"
   ```
   Note: `"$@"` (not `eval "$@"`) -- the command is passed as properly quoted arguments, avoiding shell re-interpretation hazards.

**Subsequent execs:** The proxy script sources the fast snapshot (a flat list of exports) instead of re-running the full `activate` (which may do git pulls, runtime installs, and other slow operations).

**Snapshot staleness:** The snapshot lives in `/tmp/` inside the container. If the container is recreated (`docker compose down && up`), `/tmp/` is cleared and the snapshot no longer exists. On the next exec, `ensure_env_snapshot()` detects that the snapshot file is missing and regenerates it automatically. This matches the behavior of Adamant's existing `adamant_env.sh` which uses a `.initialized` flag that is similarly cleared on container recreation. If the user changes `env/activate`, they run `admt env refresh` to regenerate the snapshot explicitly.

**`admt env refresh`:** Re-runs the full `activate`, regenerates the diff-based snapshot, and updates the exec script. Use this after changing `env/activate`, `requirements.txt`, or other environment configuration.

**`admt env start`:** If the Docker image does not exist locally, auto-runs `docker compose pull`. If the pull fails (image not in registry, network error, authentication failure), admt errors with an actionable message naming the failure reason and directs the user to build locally: `Pull failed: <reason>. Build the image locally with 'admt env build'.` Then runs `docker compose up -d`.

**Failure detection for docker compose commands:** admt checks the return code of every `docker compose` invocation. On non-zero exit, the captured stderr is forwarded to the caller as part of the error message. This applies to `up`, `pull`, `build`, `push`, `stop`, `down`, and `exec`. Error messages always name the underlying command and include its stderr output so the user can diagnose without re-running with `--verbose`.

After the container starts, admt automatically runs the full activation and generates the snapshot so that the first `admt build` is fast.

All exec calls go through the proxy script. Per the §"Compose vs. plain Docker" split above, exec uses **plain `docker exec`** against the resolved container name, not `docker compose exec`:

```bash
docker exec -u user <container_name> \
    /tmp/admt/<project>/exec.sh bash -c "cd /home/user/adamant/src/components/ccsds_router && redo all"
```

Both scripts are project-specific (keyed by project name in the path). When the active project changes via `admt env use`, the next container exec will use the new project's scripts, generating them if they don't exist yet.

### Passthrough Flow (e.g., `admt build`)

```
1. CLI adapter parses args -> calls BuildCommand.execute(context)
2. Config service loads active project from ~/.admt/config.yml (or ADMT_ENV override)
3. Path mapper resolves host cwd -> container path
4. Container service execs the proxy directly (optimistic):
   docker exec -u user <container> /tmp/admt/<project>/exec.sh ...
   with stderr=subprocess.STDOUT to merge redo's stderr into stdout (see Output Routing)
5. On non-zero exit, container service diagnoses via _recover_infrastructure:
   a. Container down? -> prompt / --yes auto-start / ADMT_NONINTERACTIVE error
      (start() also regenerates the snapshot as part of its flow). Retry.
   b. Container up but proxy script missing (snapshot wiped by an external
      docker compose down/up)? -> regenerate the snapshot transparently. Retry.
   c. Neither -> the failure is the user's command; propagate the exit code
      as-is, no spurious retry.
6. Exit code (from the first attempt or the retry) is returned in Result.
```

**Why optimistic, not check-then-execute?** The previous design pre-flighted ``is_running()`` and ``ensure_env_snapshot()`` on every exec. Both are full ``docker`` round-trips (~3s each on Docker Desktop for Mac). On the steady-state happy path -- container up, snapshot present, command works -- those probes contribute nothing and add ~6s per command. The optimistic flow saves them and pays for the diagnostic only when something is actually wrong. The behavior the user sees is identical on every path: ``--yes`` still auto-starts, ``ADMT_NONINTERACTIVE`` still errors with the same message, the exit code reflects the user's actual command failure (not a synthetic infrastructure failure on top). Only the order of ops changed.

### Passthrough with Optional Path

All passthrough commands accept an optional positional argument. The CLI adapter determines its meaning based on whether the resolved host path is a directory or not:

- **Directory path** -> changes the working directory for the redo command (populates `Context.path`). The default redo target is used (e.g., `all` for build).
- **File path or non-existent target name** -> passed as the redo target (populates `Context.target`). The working directory remains cwd.

```bash
admt build                     # redo all in current directory
admt build ../module_2/        # redo all in ../module_2/ (resolved to container path)
admt build build/obj/foo.o     # redo build/obj/foo.o in current directory
```

All path arguments are processed as follows:

1. **Resolve relative to cwd.** `admt build ../bar/` from `src/components/foo/` resolves to the absolute host path.
2. **Resolve symlinks.** The resolved path is canonicalized via `Path.resolve(strict=False)`. Using `strict=False` is important because build targets (e.g., `build/obj/foo.o`) may not exist on the host yet.
3. **Validate volume membership.** The resolved path must fall under one of the active project's volume mounts. If it does not, admt exits with exit code 4: listing the mapped directories.
4. **Map to container path.** Strip the host mount prefix and prepend the container mount point, using longest-prefix matching.

---

## Output Routing

redo routes all human-readable output to stderr and reserves stdout for its internal data pipeline between build rules. This is deliberate within redo but unusual for CLI tools.

**admt translates at the boundary.** redo's stderr is merged into stdout so that it follows standard CLI conventions:

- **stdout**: All build output (including what redo sends to stderr), command results, information the user or agent wants
- **stderr**: Errors, warnings, and diagnostic messages from admt itself only

This means `admt what | grep sensor` works as expected. `admt build 2>errors.log` captures only admt's own errors, not redo's build output.

### How This Works With Color Preservation

The key mechanism is `subprocess.Popen(cmd, stderr=subprocess.STDOUT)`, which merges the child process's stderr into stdout at the file descriptor level. Since stdout is connected to the terminal (TTY), the merged stream still passes through a TTY, so redo and docker compose output their ANSI color codes normally. Colors are just escape codes in the byte stream -- they survive the merge.

**Default mode:** Run docker compose with `stderr=subprocess.STDOUT` and stdout inherited to the terminal. Redo's colored output (originally on stderr) appears on the user's stdout with colors preserved.

**`--verbose` mode:** Same as default, but admt also prints the underlying command being executed, prefixed with `$`:

```
$ docker compose -f .../docker-compose.yml exec -u user adamant_example /tmp/admt/<project>/exec.sh bash -c "cd /home/user/adamant/src/components/ccsds_router && redo all"
redo  all
redo    build/obj/Linux/...
...
```

**`--quiet` mode:** Capture all output with `subprocess.PIPE` (both stdout and stderr). On success, discard everything -- only the exit code matters. On failure, print the captured output so the user can see what went wrong.

**How `ContainerService.exec()` reads flags:** `ContainerService.exec(command, context)` receives the full `Context` object. It reads `context.quiet`, `context.verbose`, and `context.debug` to decide how to spawn the subprocess (PIPE vs inherited stdio, whether to echo the command, whether to prepend `DEBUG=1`). The passthrough command itself does not need to configure subprocess behavior -- it just passes Context through.

**`-v` and `-q` are NOT mutually exclusive.** Combining them is valid and useful: `admt -v -q build` prints the underlying `docker exec` command being executed (or `docker compose ...` for lifecycle ops) but suppresses its output. This is especially helpful for agents that want to see what admt is doing under the hood without being flooded by build output. The combined behavior: verbose echo of commands + quiet capture of their output.

**When admt needs to inspect output** (e.g., environment variable capture during snapshot generation): Use `subprocess.PIPE` to capture programmatically. This is the exception, not the default.

### Output Rewriting

Beyond merging stderr into stdout, admt also **rewrites redo's output into admt's vocabulary** so the stream the user sees is continuous with the command they typed. `redo  Compiling 13 objects...` becomes a gold status line; `redo    build/src/foo.adb` becomes `build build/src/foo.adb` (or `admt build build/src/foo.adb` for the `admt what` listing). The rewriting layer lives in `adapters/redo_output.py` (see [Directory Structure](#directory-structure)).

Two callers consume it:

1. **Streaming passthrough commands** (`admt build` et al.) install a per-line `LineTransform` on the `docker_exec` call. Each line redo emits is intercepted before reaching the user's terminal; the transform rewrites or drops it in real time.
2. **`admt what`** captures the `redo what` output via `exec_captured` and post-processes the buffer once before printing -- the listing is short and benefits from a final cleanup pass.

The rewriter recognizes three line categories on the redo stream:

- **Top-level `redo  <target>` header** (exactly two spaces -- redo's "now processing X" marker). Dropped via `None` -- it's redundant with the verb the user just typed and with admt's own `<verb>...` opening status line.
- **Multi-word `redo  <message>` status lines** (e.g., `redo  Compiling 13 objects...`, `redo  Moving 13 objects...`). Stripped of the `redo ` prefix and routed through `output.admt(bold=False)` -- admt-relayed, not admt-emphatic.
- **Single-token `redo    <target>` progress lines** (four-or-more spaces -- nested dependency rebuilds). Rewritten to `[admt ]<verb> <target>` using a small map of redo target names to admt commands (`all`→`admt build`, `test`→`admt test`, `test_all`→`admt test --all`, etc.; unknown targets fall back to `admt build <target>` since `BuildCommand` forwards arbitrary positional targets through to redo). The verb gets bolded gold via `output.admt`; the target tail stays in the terminal's default color so it's still skimmable. Redo's nesting depth (extra spaces beyond the first level) is preserved as separator spacing between verb and target so the dependency tree stays legible.

Anything else (compiler diagnostics, tool warnings, unstructured output) passes through verbatim with its original ANSI codes intact.

In `--quiet` mode the transform is omitted; the captured buffer stays raw and is only emitted on failure via `OutputService.emit_captured`.

---

## Command Structure

Every command is a class that inherits from `Command` and implements `execute()`. This is the fundamental unit of extensibility.

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Context:
    """Everything a command needs to execute. Built by the CLI adapter.

    Services that depend on project configuration are Optional because some
    commands (`env init`, `env use`, `env list`, `--help`) run before any
    project is registered or active. Commands that need these services
    must check for None explicitly and fail with a clear error.
    """

    # Always available -- do not depend on project configuration
    config_service: "ConfigService"
    output: "OutputService"

    # Available only when an active project exists (lazily initialized).
    # Commands that need these must check for None and raise ConfigError
    # with a message like: "This command requires an active project.
    # Run 'admt env init' to register one."
    container_service: "ContainerService | None" = None
    path_mapper: "PathMapperService | None" = None

    # Global flags
    verbose: bool = False
    quiet: bool = False
    debug: bool = False
    yes: bool = False
    force: bool = False

    # Resolved from ADMT_NONINTERACTIVE env var
    noninteractive: bool = False

    # Command-specific arguments (set by CLI adapter per command)
    # The CLI adapter sets `path` if the positional arg is a directory,
    # or `target` if it is a file/non-existent target name.
    target: str | None = None       # e.g., "build/obj/foo.o" for admt build
    path: Path | None = None        # e.g., "../module_2/" for admt build ../module_2/
    run_all: bool = False           # --all / -a flag for test, clean, coverage

    def resolve_container_path(self) -> Path:
        """Map the effective host working directory to the container path.

        If self.path is set, resolve it relative to cwd first.
        Raises ConfigError if path_mapper is None (no active project).
        """
        if self.path_mapper is None:
            raise ConfigError(
                "This command requires an active project. "
                "Run 'admt env init' to register one."
            )
        host_path = Path.cwd()
        if self.path:
            host_path = (host_path / self.path).resolve()
        return self.path_mapper.host_to_container(host_path)


@dataclass
class Result:
    """Structured record of what a command did."""
    exit_code: int = 0
    files_created: list[Path] = field(default_factory=list)
    files_modified: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Command(ABC):
    """Base class for all admt commands."""

    name: str                      # e.g., "build", "env start"
    help: str                      # Shown in --help
    requires_project: bool         # Must there be an active project configured?
    requires_container: bool = False  # Set True for commands that need ContainerService

    @abstractmethod
    def execute(self, context: Context) -> Result:
        """Run the command. Returns a structured result."""
        ...
```

Note: `Result` does not carry `stdout`/`stderr` strings. For passthrough commands, output is streamed directly to the terminal (via `stderr=subprocess.STDOUT` with inherited stdio). `Result` carries the exit code and metadata about file operations. For commands that need to capture output (e.g., snapshot generation), the capture happens internally in the service and is not returned via `Result`.

The `Context` object carries everything a command might need -- resolved paths, services, configuration, flags (verbose, quiet, yes, force). Commands never reach into global state; everything arrives through the context.

**The `requires_container` flag.** Commands that need a live `ContainerService` set `requires_container = True`. The CLI adapter uses this flag to lazily wire `ContainerService` and `PathMapperService` onto the Context before `execute` runs -- so commands that don't need the container (e.g., `env init`, `env use`, `env list`) skip the wiring entirely and never pay the project-lookup cost. The metadata test in `tests/unit/test_architecture.py` verifies every concrete `Command` subclass declares the flag. Once inside `execute`, the container service still owns the "is it running?" / auto-start logic at exec time -- the flag governs *when* the service is constructed, not *how* it behaves.

### Why Classes, Not Functions

Functions cannot declare metadata, cannot be discovered by a plugin loader, and cannot participate in a type hierarchy. As admt grows to 30+ commands with shared behaviors (container passthrough, schema validation, atomic writes), classes provide the structure to keep each command self-contained without duplicating infrastructure.

### Container Passthrough Commands

Most MVP commands share the same pattern: map path, exec redo in container, stream output. A base class captures this:

```python
class ContainerPassthroughCommand(Command):
    """Base for commands that forward to redo in the container."""

    requires_project: bool = True
    redo_target: str  # e.g., "all", "test", "style", "what", "clean"

    def execute(self, context: Context) -> Result:
        container = context.container_service
        path = context.resolve_container_path()
        target = self.resolve_target(context)
        redo_cmd = f"cd {path} && redo {target}"
        return container.exec(redo_cmd, context)

    def resolve_target(self, context: Context) -> str:
        """Override for commands with special target logic."""
        return self.redo_target
```

Individual commands become minimal:

```python
class BuildCommand(ContainerPassthroughCommand):
    name = "build"
    help = "Build via redo in the current or specified directory"
    redo_target = "all"

    def resolve_target(self, context: Context) -> str:
        if context.target:
            return context.target
        return "all"


class StyleCommand(ContainerPassthroughCommand):
    name = "style"
    help = "Run style checks via redo"
    redo_target = "style"


class ProveCommand(ContainerPassthroughCommand):
    name = "prove"
    help = "Run SPARK proofs via redo"
    redo_target = "prove"
```

### The Templates Command

`admt templates` is a passthrough with extra behavior:

```
1. Exec `redo templates` in the container (same as other passthroughs)
2. Identify generated stub files in build/template/ directory on the host
   (accessible via volume mount -- Adamant's redo templates rule writes
   stubs there; see redo/rules/build_templates.py in adamant). Look for
   Ada .ads and .adb files matching the component-<name>-implementation.*
   pattern.
3. Prompt user: "Copy implementation stubs to source directory? [Y/n]"
   (unless --yes: auto-copy, unless --force: auto-copy without prompt)
   Note: --force skips the confirmation prompt but still creates the backup.
4. If yes:
   a. Back up existing implementation files to /tmp/admt-backup-XXXX/
      (via tempfile.mkdtemp, which lands in /tmp on Linux/macOS)
   b. Print backup location so user can restore if needed
   c. Copy generated stubs from build/template/ to source directory
   d. Record backup path in ~/.admt/backup-latest (plain text file
      containing the absolute path to the backup directory)
5. Return Result listing files copied and backup location
```

The marker (`~/.admt/backup-latest`) is intentionally split from the backup directory itself (`/tmp/admt-backup-XXXX/`):
- The **marker** lives under `~/.admt/` so it survives reboots -- a user who runs `admt templates`, reboots their machine, and then runs `admt templates --undo` shouldn't be silently denied because their pointer file got swept by `/tmp` cleanup.
- The **backup directory** lives under `/tmp` (via `tempfile.mkdtemp`) and may be cleared on reboot. When the marker points to a missing directory, `--undo` errors clearly ("Backup directory ... is missing") rather than silently restoring nothing.

**`admt templates --undo`:** Restores files from the most recent backup created by `admt templates`. Reads the backup path from `~/.admt/backup-latest`. If no marker exists, or the marker points to a missing directory, admt exits with a clear error. Only the most recent backup is restorable (no history stack).

---

## Services

### Config Service

Manages `~/.admt/config.yml` -- the project registry and active project state.

```python
class ConfigService:
    """Manages the admt configuration store at ~/.admt/config.yml."""

    def load(self) -> AdmtConfig: ...
    def save(self, config: AdmtConfig) -> None: ...
    def get_active_project(self) -> ProjectConfig:
        """Return the active project config.

        Resolution order: ADMT_ENV env var, then this terminal's session
        entry, then the global active_project (see Active Project
        Resolution). Runs the Config Auto-Update staleness check before
        returning. Raises ConfigError if no project is configured.
        """
        ...
    def get_active_source(self) -> ActiveSource:
        """Report which mechanism selected the active project (for env status)."""
        ...
    def set_active_project(self, name: str) -> None:
        """Set the per-terminal session entry AND the global active_project."""
        ...
    def register_project(self, project_root: Path) -> ProjectConfig:
        """Register a project. Verifies markers, derives config via
        `docker compose config` (resolves .env interpolation)."""
        ...
    def list_projects(self) -> dict[str, ProjectConfig]: ...
```

The per-terminal session store (`~/.admt/sessions`, keyed by controlling TTY with a `getsid` staleness guard) is managed by `ConfigService`. Deriving resolved metadata requires shelling out, so `register_project` and the auto-refresh delegate the `docker compose config` call to the Docker adapter (only adapters import `subprocess`); the resolved struct is passed back to the service.

### Container Service

The most critical service. Encapsulates all interaction with Docker.

```python
class ContainerService:
    """Manages container lifecycle and command execution."""

    def is_running(self) -> bool: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def restart(self) -> None: ...
    def rm(self, volumes: bool = False, image: bool = False, remove_all: bool = False) -> None: ...
    def status(self) -> ContainerStatus: ...
    def login(self) -> None: ...
    def build_image(self) -> None: ...
    def push_image(self) -> None: ...
    def pull_image(self) -> None: ...
    def refresh(self) -> None: ...
    def ensure_env_snapshot(self) -> None: ...
    def exec(self, command: str, context: Context) -> Result: ...
```

The `ensure_env_snapshot()` method checks if `/tmp/admt/<project>/env_snapshot.sh` and `/tmp/admt/<project>/exec.sh` exist in the container. If not (first use, or after container restart), it runs the full `env/activate`, captures the environment, and writes both scripts. The `exec()` method calls `ensure_env_snapshot()` and then executes through the proxy script. It also checks if the container is running and handles the prompt/auto-start logic (see [Global Flags](#global-flags)).

### Path Mapper Service

Resolves host paths to container paths and vice versa using the volume mount configuration.

```python
class PathMapperService:
    """Maps between host filesystem paths and container paths."""

    def __init__(self, volume_mounts: dict[Path, Path]) -> None: ...

    def host_to_container(self, host_path: Path) -> Path:
        """Map a host path to its container equivalent.

        Uses longest-prefix matching among volume mounts.
        Raises PathNotMappedError if the path is not under any volume mount.
        """
        ...

    def container_to_host(self, container_path: Path) -> Path: ...

    def is_mapped(self, host_path: Path) -> bool: ...
```

### Output Service

Handles formatting and routing of command output.

```python
class OutputService:
    """Formats and routes command output based on verbosity settings."""

    def __init__(self, verbose: bool, quiet: bool, yes: bool, noninteractive: bool) -> None: ...

    def command_echo(self, command: str) -> None:
        """Print the command being executed (verbose mode only)."""
        ...

    def info(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...
    def success(self, message: str) -> None: ...

    def prompt(self, message: str, default: bool | None = True) -> bool:
        """Ask user a yes/no question.

        If default is None, --yes cannot auto-accept (still asks).
        With --yes and a default: returns default without prompting.
        With ADMT_NONINTERACTIVE: errors with exit code 3.
        """
        ...

    def choose(self, message: str, choices: list[str]) -> str:
        """Ask the user to pick one item from ``choices`` and return it.

        Boolean ``prompt`` cannot express "pick one of N" (used by
        ``admt env init`` when multiple compose files are present).

        - A single-item list returns directly without prompting.
        - With ADMT_NONINTERACTIVE set: raises ArgumentError -- there is
          no meaningful default for an arbitrary list.
        - --yes does NOT auto-resolve choose() -- there is no obvious
          "default" item; the user has to pick one explicitly.
        """
        ...

    def admt(self, message: str, *, bold: bool = True) -> str:
        """Wrap ``message`` in admt's signature gold (#CFB87C) when stdout is a color TTY.

        - bold=True (default): bold + gold. Used for admt verbs in
          rewritten output (``build`` in ``build foo.adb``) and for the
          opening status line of long-running commands (``building...``).
        - bold=False: gold only. Used for closing markers (``done.``)
          and for admt-relayed tool messages like multi-word redo
          status lines (``Compiling 13 objects...``) -- admt-presented
          but not admt-emphatic.

        Returns the message unchanged when stdout is not a TTY or
        NO_COLOR is set, so callers can wrap unconditionally.
        """
        ...

    def emit_captured(self, content: str, *, to_stderr: bool = False) -> None:
        """Print captured subprocess output verbatim, bypassing --quiet.

        Used by passthrough commands so that a failed redo invocation
        in --quiet mode still shows the captured output -- otherwise
        the user sees only an exit code with no explanation.
        """
        ...
```

### Schema Service (Post-MVP)

For future schema-driven creation and validation. Not implemented in MVP.

```python
class SchemaService:
    """Discovers and queries Adamant's pykwalify schemas."""

    def discover_schemas(self, adamant_root: Path) -> dict[str, Path]: ...
    def validate_file(self, file_path: Path) -> ValidationResult: ...
    def get_fields(self, model_type: str) -> list[FieldInfo]: ...
```

### Filesystem Service (Post-MVP)

For future atomic file creation operations.

```python
class FilesystemService:
    """Staged atomic file operations for creation commands."""

    def stage_file(self, path: Path, content: str) -> None: ...
    def stage_directory(self, path: Path) -> None: ...
    def commit(self, force: bool = False) -> list[Path]: ...
    def rollback(self) -> None: ...
```

### Service Wiring

Services are instantiated in the CLI adapter's top-level group callback and stored in Click's context object. Each Click command function retrieves them and builds the `Context` dataclass before calling the command class:

```python
# In cli.py -- top-level group callback
@click.group(cls=AliasedGroup)
@click.pass_context
def cli(ctx: click.Context, verbose: bool, quiet: bool, ...) -> None:
    config_service = ConfigService()
    output = OutputService(verbose=verbose, quiet=quiet, yes=yes, noninteractive=noninteractive)
    # ContainerService and PathMapperService are created lazily
    # when a command calls get_active_project() -- because they
    # depend on project config that may not exist yet (e.g., env init).
    ctx.obj = {
        "config_service": config_service,
        "output": output,
        "flags": {"verbose": verbose, "quiet": quiet, "debug": debug, ...},
    }
```

Commands that need the container (most passthrough commands) resolve the active project, build the `PathMapperService` from its volume mounts, and construct the `ContainerService` with the Docker adapter. This lazy initialization avoids errors when running commands that do not need a project (e.g., `admt --help`, `admt env init`).

---

## Global Flags

These flags are available on every command and are handled by the CLI adapter before dispatching to commands. They are passed to commands via the `Context` object.

| Flag | Short | Behavior |
|------|-------|----------|
| `--verbose` | `-v` | Print underlying docker/redo commands before executing. Show detailed output. |
| `--quiet` | `-q` | Suppress all output on success. Only errors are printed. Exit code is the signal. |
| `--debug` | `-d` | Implies `--verbose`. Additionally prepends `DEBUG=1` to redo commands in the container, enabling Adamant's redo-level debug output. Use for diagnosing build system issues. |
| `--yes` | `-y` | Auto-accept all interactive prompts with their default values. For commands that need the container, auto-starts it if not running. |
| `--force` | `-f` | Overwrite existing files without confirmation. For destructive operations, skip the safety prompt. |

### Flag Placement

Global flags are parsed **at the top-level group only**, before the subcommand name. `admt -v build` is valid; `admt build -v` is not (Click reports the flag as unknown on the subcommand). This matches the behavior of `docker` and `git`, and keeps the CLI adapter minimal -- each global flag is declared exactly once.

Subcommand-specific flags (for example `--all`/`-a` on `test`, `--volumes` on `env rm`, or `--undo` on `templates`) are placed after the subcommand name as usual:

```bash
admt -v build                 # global flag before subcommand
admt test --all               # subcommand flag after subcommand
admt -v -q test --all         # combined: globals first, then subcommand flags
```

**Note on `-v` + `-q`:** These flags are **not mutually exclusive**. Combining them (`admt -v -q build`) prints the underlying commands being executed but suppresses their output -- useful for agents and scripts that want to see what admt is doing under the hood without capturing the full output stream.

### Interactive Mode: `--yes` vs `ADMT_NONINTERACTIVE`

admt is **human-first and interactive by default**. When a command needs input, it prompts and waits. There are two distinct mechanisms for suppressing prompts, designed for different audiences:

**`--yes` (`-y`) -- for humans who want to skip prompts:**
- Selects the default answer when a prompt has a default (e.g., "Start container? [Y/n]" -> Y)
- **Still asks** when there is no sensible default (e.g., "Which compose file?" when multiple are found)
- Designed for experienced humans who know what they want and don't need confirmation

**`ADMT_NONINTERACTIVE` -- for agents and scripts:**
- Set this environment variable to a truthy value (anything other than `0` or empty) to switch to agent mode
- **Never prompts.** Uses defaults when available; **errors with a non-zero exit code** when a required argument is missing and has no default
- The error message tells the caller exactly what flag to provide
- Designed for CI pipelines, scripts, and AI agents that must never block on stdin
- **Value semantics:** `unset` and `ADMT_NONINTERACTIVE=0` are off; any other value (`1`, `true`, `yes`, `on`, etc.) is on. Matches POSIX-shell-style boolean conventions so users with `ADMT_NONINTERACTIVE=0` in their shell init don't trip into agent mode unintentionally.

These are **not alternatives** -- they have different semantics:

```bash
# Human mode (default):
$ admt build
Container not running. Start it? [Y/n] y
Starting container... done.
redo all ...

# Human shortcut (--yes selects defaults):
$ admt build --yes
Starting container... done.
redo all ...

# Agent/script mode (errors instead of prompting):
$ ADMT_NONINTERACTIVE=1 admt build
Error: Container 'adamant_example' is not running.
  Run 'admt env start' first, or pass '--yes' to auto-start.
  Exit code: 2
```

---

## Exit Codes

Distinct exit codes allow scripts and agents to branch on failure type without parsing output:

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Command failed (e.g., redo build failed, test failure) |
| 2 | Environment error (container not running, no project configured) |
| 3 | Argument error (missing required argument, invalid value) |
| 4 | Path error (cwd not mapped into container, target not found) |
| 130 | Interrupted (SIGINT / Ctrl+C) |

---

## Signal Handling

When the user sends SIGINT (Ctrl+C) during a container-forwarded operation:

1. admt catches SIGINT.
2. admt sends SIGINT to the in-flight `docker exec` (or `docker compose ...`) subprocess (best-effort propagation).
3. admt prints the PIDs of any in-flight subprocesses so the user can manually `kill -9` them if needed. Obtaining the PID of the process *inside* the container is non-trivial; for MVP, the host-side PID is sufficient.
4. admt exits with code 130.

This is best-effort for MVP. The host-side `docker` subprocess receives the signal, but the process inside the container (e.g., redo) may survive if `docker exec` simply disconnects. Printing the PID gives the user a fallback.

---

## ANSI Color Handling

redo output contains ANSI escape codes. admt detects terminal capability via `sys.stdout.isatty()` and selects output behavior accordingly:

- **TTY detected (interactive terminal):** ANSI codes are passed through. admt's own output (status messages, error formatting) uses color.
- **No TTY (piped or redirected):** ANSI codes are stripped from redo output and admt's own output is plain text.
- **`NO_COLOR` env var set:** All color output is suppressed regardless of terminal detection.

The implementation uses two output modes sharing the same interface, selected by TTY detection with an explicit override.

---

## TTY and Stdin Handling

| Operation | TTY | Stdin | Flags |
|-----------|-----|-------|-------|
| `login` | Yes (interactive terminal) | Forwarded | `-it -u user` |
| `exec` (build, test, etc.) | No (non-interactive) | Not forwarded | `-u user` |
| `env exec <cmd>` | No by default, yes if stdin is a terminal | Detect | `-u user`, add `-it` if `isatty(stdin)` |
| `refresh` | No | Not forwarded | `-u user` |

For `admt env exec`, admt checks whether stdin is a terminal (`os.isatty(0)`). If yes, it adds `-it` to the docker compose exec flags for interactive use. If no (piped input), it omits TTY flags.

---

## Behavioral Details

### `admt env restart`

Equivalent to `admt env stop` followed by `admt env start`. If `stop` fails, `restart` aborts -- it does not attempt `start`. The start step includes full environment activation and snapshot regeneration.

### `admt env rm`

Removes the container. Flags control scope:

- (no flags): Remove just the container (`docker compose down`)
- `--volumes`: Also remove volumes (`docker compose down -v`)
- `--image`: Also remove the Docker image
- `--remove-all`: Remove container, volumes, and image

Prompts for confirmation (default **No**, since removal is destructive). `--force` skips the prompt entirely; `--yes` accepts the default and therefore *declines* the removal -- consistent with `--yes` meaning "accept the default", not "do the dangerous thing". The project remains registered in `~/.admt/config.yml` -- only the container resources are removed.

### Passthrough with `--debug`

When `--debug` is set, admt prepends `DEBUG=1` to redo commands inside the container:

```bash
# Instead of: cd /path && redo all
# Runs:       cd /path && DEBUG=1 redo all
```

This enables Adamant's redo-level debug output for diagnosing build system issues. `--debug` also implies `--verbose`.

### `admt env exec`

`admt env exec` goes through the proxy script (`/tmp/admt/<project>/exec.sh`) so that the environment is activated. The argument is passed as a shell command string via `bash -c`:

```bash
admt env exec "cd src/components/foo && redo test"
# Runs: docker exec -u user <container_name> /tmp/admt/<project>/exec.sh bash -c "cd src/components/foo && redo test"
```

### `admt env login`

`admt env login` does **not** go through the proxy script. It runs a bare interactive bash shell:

```bash
# Runs: docker exec -it -u user <container_name> /bin/bash
```

The container's `.bashrc` already sources `env/activate` (or the cached snapshot), so the environment is activated automatically when bash starts. This matches the current `adamant_env.sh login` behavior.

### Working Directory for `admt test`

`admt test src/components/foo/` runs `redo test` in `src/components/foo/`. If the user intends to test from the `test/` subdirectory, they specify `admt test src/components/foo/test/`. admt does not append `test/` automatically -- it operates in the directory the user provides.

### Shell Completion Scope

MVP shell completion covers command names, subcommands, and flags. Tab-completing paths and component names from the project is deferred to post-MVP (requires project-aware completion functions).

### Graceful Degradation Without Container

For post-MVP commands that could partially operate on the host (e.g., `admt create component` generating YAML without needing the container, but needing it for `redo templates`), admt uses a staged approach: perform all host-side work in a temp directory, then forward container-required steps. If the container is unavailable, admt can complete the host-side portion and report what remains:

```
Created YAML models in /tmp/admt-xxx/src/components/foo/
Container is not running. Run `admt env start` then `admt templates` to generate implementation stubs.
```

### Version

The canonical version string lives in `src/admt/__init__.py` as `__version__`. `pyproject.toml` reads it dynamically. `admt --version` prints it.

---

## Non-Goals

admt is not:

- **A replacement for redo.** admt invokes redo. It is a wrapper.
- **An IDE or editor.** admt produces files. You edit them.
- **A generator reimplementation.** admt triggers Adamant's own code generators. It does not duplicate them.
- **A package manager.** `admt create project` scaffolds structure. It does not manage dependencies.
- **Network-dependent.** Local operations work offline.
- **A GUI.** Command line only.

---

## Plugin System (Roadmap)

Not part of MVP, but the architecture is designed to support it. Plugins are Python packages that register commands via entry points:

```toml
# In a plugin's pyproject.toml
[project.entry-points."admt.plugins"]
my_mission = "my_mission_admt:register"
```

```python
def register(registry):
    registry.add(DeployCommand())
    registry.add(FlashCommand())
```

Plugin commands appear in `admt --help` alongside built-in commands. They have access to all services. This allows mission teams to add `admt deploy`, `admt flash`, or other project-specific workflows without forking admt.

---

## How This Scales

The architecture is designed so that growth happens in the **commands** directory, not across the system:

| Change | What gets added/modified |
|--------|--------------------------|
| New redo passthrough (e.g., `admt prove`) | One file in `commands/`. One Click entry in `cli.py`. |
| New service (e.g., schema validation) | One file in `services/`. Commands that need it accept it via context. |
| New adapter (e.g., Podman support) | One file in `adapters/`. The container service switches implementations. |
| New plugin | External package registers commands via entry point. Zero changes to admt core. |
| New output format (e.g., `--json`) | One change in `output.py`. Zero changes to commands. |

No command needs to know about any other command. No service needs to know about any specific command. The system grows additively, not by modifying existing code.
