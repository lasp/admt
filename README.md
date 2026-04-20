# admt -- The Adamant Multitool

A command-line interface for the [Adamant](https://github.com/lasp/adamant) spacecraft flight software framework.

admt provides composable commands for common Adamant workflows -- building, testing, managing the development environment, and (eventually) creating and modifying components, types, and assemblies. It encodes the idiomatic patterns that experienced Adamant developers already follow, making them available to everyone: humans at a terminal, agents in a pipeline, and engineers on day one.

---

## Why

- **Repetition.** Creating YAML models, running code generation, copying Ada templates, setting up paths, and wiring assemblies is well-defined and idiomatic -- but manual. Engineers repeat these steps across every component and every project.

- **Fragmented tooling.** Adamant's capabilities are spread across `redo`, `adamant_env.sh`, Python autocoders, `pykwalify`, and more -- roughly 15 tools with no single entry point. A developer must know which tool to reach for, how to invoke it, and how to navigate between host and container. This tribal knowledge is a barrier for new engineers, a tax on experienced ones, and a minefield for AI agents.

- **Entry friction.** A developer new to Adamant can follow the documentation, but the path from concept to tested component involves several tools and project conventions. admt brings these under one entry point.

- **Agent overhead.** AI agents execute deterministic sequences step-by-step. Operations that a shell command could handle in milliseconds instead consume inference cycles and tokens. admt avoids effects from agent degradation or other changes in provider capabilities.

- **Script brittleness.** CI pipelines and local scripts rely on fragile shell invocations of docker exec, redo, and path manipulation that break when conventions change.

admt replaces this fragmented toolchain with one command. `admt build` from anywhere on the host does the right thing -- path mapping, container forwarding, environment activation -- so developers and agents can focus on the work, not the plumbing.

---

## Project Status

admt is in the design phase. Implementation has not started.

See the companion documents for details:

- [ARCHITECTURE.md](ARCHITECTURE.md) -- Layered architecture, services, discovery, and container passthrough design
- [CODING_RULES.md](CODING_RULES.md) -- Rules for all contributors (human and agent)
- [MVP_PLAN.md](MVP_PLAN.md) -- Step-by-step plan for the first working version
- [TEST_PLAN.md](TEST_PLAN.md) -- Testing strategy and quality gates

---

## Quick Start (MVP Target)

```bash
# Install admt
uv tool install admt

# Initialize admt for your project (run from project root)
cd ~/projects/adamant_example/
admt env init

# Start the container
admt env start

# Build, test, and check style from anywhere on the host
cd ~/projects/adamant_example/src/components/my_component/
admt build
admt test
admt style

# List buildable targets
admt what

# Run redo templates and optionally copy stubs
admt templates
```

---

## Command Overview (MVP)

### Environment Management

| Command | Short | Purpose |
|---------|-------|---------|
| `admt env init [path]` | `admt e init` | Register a project with admt (run from project root, or pass path) |
| `admt env use <project>` | `admt e use` | Switch active project |
| `admt env start` | `admt e start` | Start the project container |
| `admt env stop` | `admt e stop` | Stop the container |
| `admt env restart` | `admt e restart` | Restart the container (stop + start) |
| `admt env login` | `admt e login` | Open a shell in the container |
| `admt env status` | `admt e status` | Show container status |
| `admt env build` | `admt e build` | Build the Docker image |
| `admt env push` | `admt e push` | Push image to registry |
| `admt env pull` | `admt e pull` | Pull image from registry |
| `admt env exec <cmd>` | `admt e exec` | Run an arbitrary command in the container |
| `admt env refresh` | `admt e refresh` | Rebuild the cached environment snapshot |
| `admt env list` | `admt e list` | List registered projects |
| `admt env rm` | `admt e rm` | Remove container (`--volumes`, `--image`, `--remove-all`) |

### Build Passthrough

| Command | Short | Purpose |
|---------|-------|---------|
| `admt build [path]` | `admt b` | Build via redo (default: `redo all`) |
| `admt test [path]` | `admt t` | Run tests via redo |
| `admt test --all` | `admt t -a` | Recursive test (`redo test_all`) |
| `admt style [path]` | `admt s` | Check code style via redo |
| `admt style --all` | `admt s -a` | Recursive style check (`redo style_all`) |
| `admt analyze [path]` | `admt an` | Run static analysis via redo |
| `admt analyze --all` | `admt an -a` | Recursive analysis (`redo analyze_all`) |
| `admt what [path]` | `admt w` | List buildable targets (`redo what`) |
| `admt clean [path]` | `admt cl` | Remove build artifacts (`redo clean`) |
| `admt clean --all` | `admt cl -a` | Recursive clean (`redo clean_all`) |
| `admt prove [path]` | `admt p` | Run SPARK proofs via redo |
| `admt coverage [path]` | `admt cov` | Generate coverage report |
| `admt coverage --all` | `admt cov -a` | Recursive coverage (`redo coverage_all`) |
| `admt publish [path]` | `admt pub` | Publish via redo (`redo publish`) |
| `admt publish --all` | `admt pub -a` | Recursive publish (`redo publish_all`) |
| `admt templates [path]` | `admt tmpl` | Run `redo templates` + optional stub copy |
| `admt templates --undo` | `admt tmpl --undo` | Restore files overwritten by last `admt templates` |

### Global Flags

| Flag | Short | Purpose |
|------|-------|---------|
| `--verbose` | `-v` | Show underlying commands being executed |
| `--quiet` | `-q` | Minimal output (exit code only on success; errors still printed) |
| `--debug` | `-d` | Implies `--verbose` and runs redo with `DEBUG=1` for Adamant-level debug output |
| `--yes` | `-y` | Auto-accept prompts with defaults |
| `--force` | `-f` | Overwrite existing files without confirmation |

Global flags go **before** the subcommand (`admt -v build`), matching `docker` and `git` conventions. Subcommand-specific flags (like `--all` on `test`) go after the subcommand name. See [ARCHITECTURE.md](ARCHITECTURE.md#flag-placement) for details.

### `--yes` vs `ADMT_NONINTERACTIVE`

admt is human-first and interactive by default -- when information is missing, it prompts.

**`--yes` is for humans** who want to skip confirmations. It selects the default when one exists (e.g., "Start container? [Y/n]" -> Y) but still asks when there is no sensible default.

**`ADMT_NONINTERACTIVE` is for agents and scripts.** Set this environment variable to ensure admt never blocks on stdin. It uses defaults when available and **errors** (with a non-zero exit code) when a required argument is missing. The error message names the flag to provide.

```bash
# Human shortcut: accept defaults, still ask when ambiguous
admt --yes build

# Agent mode: never prompt, error if info is missing
export ADMT_NONINTERACTIVE=1
admt build   # errors if container not running, instead of asking
```

### Project Override

The active project (set via `admt env use`) can be overridden for a single invocation:

```bash
ADMT_ENV=adamant-standalone admt build
```

---

## Audience

- **Embedded developers** who want a clean, composable CLI
- **CI/CD pipelines** that need reliable, scriptable commands
- **AI agents** that need deterministic, low-overhead operations
- **New developers** who need a guided path into the Adamant ecosystem

---

## Core Requirements

1. Correct by construction (YAML validates before writing)
2. Schema-driven, never invents Adamant semantics
3. Thin wrapper (composes redo, pykwalify, docker -- doesn't reimplement)
4. Host-only, container-forwarding
5. Non-interactive parity (every wizard choice is a CLI flag)
6. Atomic operations (all-or-nothing file writes)
7. Observable and predictable failure (meaningful exit codes, actionable errors)
8. Dual audience (humans, scripts, agents)

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full treatment (R1-R14).

---

## Design-First Development

admt is built design-first. The design documents are the specification. Code traces to them. Deviations require a design amendment -- not just a code change.

This is intentional. The goal is narrow convergence: multiple developers and agents working in parallel should produce code that integrates cleanly because the interfaces, algorithms, and behaviors are defined before implementation begins. The design phase is not overhead -- it is the mechanism that keeps implementation on rails.

### Specification Documents

These four documents are authoritative. They define what admt does, how it is built, and how it is tested:

| Document | Purpose |
|----------|---------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Layers, services, adapters, requirements, exact command templates |
| [CODING_RULES.md](CODING_RULES.md) | Mandatory development standards for humans and agents |
| [MVP_PLAN.md](MVP_PLAN.md) | Step-by-step implementation plan with deliverables and verification |
| [TEST_PLAN.md](TEST_PLAN.md) | Testing tiers, coverage thresholds, quality gates |

---

## Technology Stack

- **Language:** Python 3.14+
- **CLI Framework:** [Click](https://click.palletsprojects.com/)
- **Package Management:** [uv](https://docs.astral.sh/uv/)
- **Linting and Formatting:** [ruff](https://docs.astral.sh/ruff/)
- **Type Checking:** [mypy](https://mypy-lang.org/) (strict mode)
- **Testing:** [pytest](https://pytest.org/)
- **YAML:** [ruamel.yaml](https://yaml.readthedocs.io/)

---

## Non-Goals

See the [Non-Goals section in ARCHITECTURE.md](ARCHITECTURE.md#non-goals).

---

## Platform Support

Linux and macOS. Both must be supported from the start.

---

## License

Apache-2.0 (compatible with [Adamant](https://github.com/lasp/adamant))

---

## Contributing

### For humans

Read the four specification documents before writing code. Every change must trace to the design. If the design doesn't cover your change, propose a design amendment first.

Each implementation step is a PR. PRs must pass all quality gates (ruff, mypy, tests, coverage). PRs are reviewed by at least one other developer before merge.

### For agents

Read [CODING_RULES.md](CODING_RULES.md) in full before writing any code. The agent-specific rules at the end are mandatory. Key constraints:

- Every line traces to the design documents
- Write tests alongside implementation, not after
- Run the full test suite before submitting work
- No new dependencies without human approval
- No speculative features or unrequested improvements

Set `ADMT_NONINTERACTIVE=1` when running admt during development to ensure non-interactive behavior is tested.
