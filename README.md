# admt -- The Adamant Multitool

A unified command-line interface for the [Adamant](https://github.com/lasp/adamant) software framework. admt orchestrates Adamant's container, build system, and code generators behind one composable tool, so building, testing, and managing your project is `admt build` from anywhere on the host -- no juggling redo, docker exec, environment scripts, or path mapping.

admt is an experimental front end for users and agents. It sits in front of Adamant's own build system and documentation and does not replace them; commands, output, and defaults may still change between releases. Adamant remains the reference for how a project is built and tested.

## Installation

```bash
uv tool install admt
```

Requires Python 3.14+, Docker, and Linux or macOS. If `uv python install 3.14` fails with "No download found", update uv first (`uv self update`, or `brew upgrade uv`).

To install from a source checkout:

```bash
git clone https://github.com/lasp/admt.git
cd admt
uv python install 3.14
uv tool install --python 3.14 --editable .
```

## Quick start

One-time setup for an Adamant project (run from the project root, which must contain `default.do`, `docker/*.yml`, and `env/activate`):

```bash
cd ~/projects/my-adamant-project/
admt env init           # register the project
admt env start          # start the development container
```

Daily use, from anywhere on the host:

```bash
cd src/components/my_component/
admt build              # redo all
admt test               # redo test
admt style              # redo style
admt what               # list buildable targets
admt templates          # generate stubs and (optionally) copy them in
```

Path arguments work too: `admt build ../other_component/` builds another directory; `admt build build/obj/foo.o` builds a specific target.

## Commands

### Environment

| Command | Alias | Purpose |
|---|---|---|
| `admt env init [path]` | `e init` | Register a project with admt. |
| `admt env use <project>` | `e use` | Switch the active project. |
| `admt env list` | `e list` | List registered projects. |
| `admt env start` | `e start` | Start the project container (auto-pulls the image when missing). |
| `admt env stop` | `e stop` | Stop the container. |
| `admt env restart` | `e restart` | Stop then start. |
| `admt env status` | `e status` | Show container status. |
| `admt env login` | `e login` | Open an interactive shell in the container. |
| `admt env exec <cmd>` | `e exec` | Run a command inside the container. |
| `admt env build` | `e build` | Build the Docker image. |
| `admt env push` | `e push` | Push the image to its registry. |
| `admt env pull` | `e pull` | Pull the image from its registry. |
| `admt env refresh` | `e refresh` | Re-run `env/activate` and rebuild the cached environment snapshot. |
| `admt env rm` | `e rm` | Remove the container. `--volumes`, `--image`, or `--remove-all` widen the scope. |

### Build, test, generate

Each of these forwards to redo inside the container, mapping your host working directory to its container counterpart.

| Command | Alias | Equivalent | Notes |
|---|---|---|---|
| `admt build [path-or-target]` | `b` | `redo all` or `redo <target>` | Default builds the current directory. |
| `admt test [path]` | `t` | `redo test` | `--all` switches to `redo test_all` (recursive). |
| `admt style [path]` | `s` | `redo style` | `--all` switches to `redo style_all`. |
| `admt analyze [path]` | `an` | `redo analyze` | `--all` switches to `redo analyze_all`. |
| `admt clean [path]` | `cl` | `redo clean` | `--all` switches to `redo clean_all`. |
| `admt coverage [path]` | `cov` | `redo coverage` | `--all` switches to `redo coverage_all`. |
| `admt publish [path]` | `pub` | `redo publish` | `--all` switches to `redo publish_all`. |
| `admt prove [path]` | `p` | `redo prove` | SPARK formal verification. |
| `admt what [path]` | `w` | `redo what` | List buildable targets. |
| `admt templates [path]` | `tmpl` | `redo templates` | Optionally copies generated implementation stubs into your source directory. `--undo` restores from the most recent backup. |

## Global flags

| Flag | Short | Effect |
|---|---|---|
| `--verbose` | `-v` | Print the underlying `docker exec` / `redo` command before running. |
| `--quiet` | `-q` | Suppress output on success; errors still print. Combine with `-v` to see the command without its output. |
| `--debug` | `-d` | Implies `--verbose`, and runs redo with `DEBUG=1` for build-system debug output. |
| `--yes` | `-y` | Auto-accept prompts that have a default. |
| `--force` | `-f` | Overwrite without confirmation. |

Global flags go **before** the subcommand (`admt -v build`), like `docker` and `git`. Subcommand-specific flags (`--all`, `--undo`, etc.) go after.

## Selecting the active project

Every command targets one **active project** -- the container admt forwards into. It is chosen in this order:

1. **`ADMT_ENV=<project>`** -- a per-command override; wins over everything and pins nothing.
2. **Your session's pin** -- what `admt env use` recorded for this terminal or headless session.
3. **The global default** -- the last project a terminal `env use`d or `env init` registered.

Set it once per session and every later command follows:

- **In a terminal:** `admt env use <project>` pins this terminal (keyed on its tty).
- **Headless -- AI agents, CI (no tty):** `admt env use <project>` pins your *session*. admt keys the pin on your harness's session id, so one `env use` holds across every later command even though each runs in a fresh shell that would drop an `export`. Claude Code (`CLAUDE_CODE_SESSION_ID`) and Codex (`CODEX_THREAD_ID`) are detected automatically; Gemini CLI's `GEMINI_SESSION_ID` is recognized too but only appears in its hook environment, not the normal shell path. Any other harness can supply its own stable id via `ADMT_SESSION_KEY`.
- **One-off / orchestration:** `ADMT_ENV=<project> admt <cmd>` selects the project for that single call.

```bash
ADMT_ENV=adamant-standalone admt build     # one invocation, no pin
admt env use my-project                    # pin this session (terminal or agent)
```

A headless `env use` pins **only your session** -- it never moves the global default, so concurrent agents (one per worktree) do not collide. A headless caller with **no tty and no session key** is refused rather than silently building against whatever the shared global happens to be; the error names the fix. `admt env status` reports which of the above selected the active project.

## Scripts and agents

`ADMT_NONINTERACTIVE=1` switches admt into non-blocking mode: prompts become errors with non-zero exit codes and a message naming the flag to pass. Use it in CI and for AI agents -- pair it with a project selection (above) so a tty-less run both avoids prompts and knows which project to target.

## Shell completion

```bash
# bash
eval "$(_ADMT_COMPLETE=bash_source admt)"

# zsh
eval "$(_ADMT_COMPLETE=zsh_source admt)"

# fish
eval (env _ADMT_COMPLETE=fish_source admt)
```

Add the appropriate line to your shell rc to persist.

## Roadmap

Future capabilities under development:

- **Creation and introspection** -- `admt create component <name>`, `admt create record/array/enum`, `admt info <name>`, `admt validate [file]`, schema-driven wizards, per-project `.admt.yml`.
- **Mutation** -- `admt add event`, `admt add command`, `admt add connector`, `admt add data-product`, with directory-context detection and `--dry-run` previews.
- **Assemblies and projects** -- `admt create assembly`, `admt add connection`, `admt create project`, cross-model validation.
- **Plugins** -- Register project-specific commands under the `admt` namespace via Python entry points.

## License

[Apache-2.0](LICENSE), compatible with [Adamant](https://github.com/lasp/adamant).
