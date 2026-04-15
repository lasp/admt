# admt Coding Rules

These rules apply to **all** code contributed to admt, whether written by a human or an AI agent. They are not suggestions -- they are requirements. Code that violates these rules must not be merged.

---

## Table of Contents

- [Language and Runtime](#language-and-runtime)
- [Toolchain](#toolchain)
- [Architecture Rules](#architecture-rules)
- [Type Annotations](#type-annotations)
- [Code Style](#code-style)
- [File and Function Size](#file-and-function-size)
- [Comments and Documentation](#comments-and-documentation)
- [DRY and Abstraction](#dry-and-abstraction)
- [Error Handling](#error-handling)
- [Subprocess Handling](#subprocess-handling)
- [Dependencies](#dependencies)
- [Testing Requirements](#testing-requirements)
- [Git and Commit Discipline](#git-and-commit-discipline)
- [What Not to Do](#what-not-to-do)
- [Agent-Specific Rules](#agent-specific-rules)

---

## Language and Runtime

- **Python 3.13+** is the minimum supported version. Use modern Python features (type unions with `|`, `match` statements, `StrEnum`, etc.) where they improve clarity.
- **Target platforms:** Linux and macOS for MVP. Windows is not yet a target but will be supported in the future. Do not introduce platform-specific code (e.g., hardcoded `/` separators, Unix-only subprocess flags) that would preclude Windows support. Use `pathlib.Path` for all path operations.

---

## Toolchain

All tools are configured in `pyproject.toml`. No separate config files (no `.flake8`, no `setup.cfg`, no `mypy.ini`).

| Tool | Purpose | Invocation |
|------|---------|------------|
| [uv](https://docs.astral.sh/uv/) | Package and project management | `uv sync`, `uv run`, `uv tool install` |
| [ruff](https://docs.astral.sh/ruff/) | Linting **and** formatting (replaces flake8, black, isort) | `ruff check`, `ruff format` |
| [mypy](https://mypy-lang.org/) | Static type checking (strict mode) | `mypy src/` |
| [pytest](https://pytest.org/) | Testing | `pytest` |
| [Click](https://click.palletsprojects.com/) | CLI framework | (library, not a standalone tool) |
| [ruamel.yaml](https://yaml.readthedocs.io/) | YAML parsing/writing with comment preservation | (library, not a standalone tool) |

### Ruff Configuration

Ruff is configured in strict/pedantic mode with most rule sets enabled. This is deliberate -- we want the linter to catch as much as possible.

```toml
# In pyproject.toml
[tool.ruff]
target-version = "py313"
line-length = 100

[tool.ruff.lint]
select = [
    "E",     # pycodestyle errors
    "W",     # pycodestyle warnings
    "F",     # pyflakes
    "I",     # isort (import sorting)
    "N",     # pep8-naming
    "UP",    # pyupgrade (modernize syntax)
    "B",     # flake8-bugbear (common bugs)
    "A",     # flake8-builtins (shadowing builtins)
    "ANN",   # flake8-annotations (type annotation presence)
    "ARG",   # flake8-unused-arguments
    "C4",    # flake8-comprehensions (simplify comprehensions)
    "DTZ",   # flake8-datetimez (timezone-aware datetimes)
    "EM",    # flake8-errmsg (string literal in exceptions)
    "ERA",   # eradicate (commented-out code)
    "FBT",   # flake8-boolean-trap (boolean positional args)
    "ICN",   # flake8-import-conventions
    "ISC",   # flake8-implicit-str-concat
    "PIE",   # flake8-pie (misc lints)
    "PL",    # pylint rules (errors, warnings, refactoring)
    "PT",    # flake8-pytest-style
    "Q",     # flake8-quotes
    "RET",   # flake8-return (return statement lints)
    "RSE",   # flake8-raise (raise statement lints)
    "RUF",   # ruff-specific rules
    "S",     # flake8-bandit (security)
    "SIM",   # flake8-simplify
    "T20",   # flake8-print (no print statements -- use output service)
    "TCH",   # flake8-type-checking (move imports to TYPE_CHECKING)
    "TID",   # flake8-tidy-imports
    "TRY",   # tryceratops (exception handling)
    "D",     # pydocstyle (docstring conventions)
    "PTH",   # flake8-use-pathlib (enforce pathlib over os.path)
    "C90",   # mccabe (cyclomatic complexity)
    "PERF",  # perflint (performance lints)
]
ignore = [
    "ISC001",  # conflicts with ruff formatter
]

[tool.ruff.lint.per-file-ignores]
"tests/**/*.py" = [
    "S101",    # assert is fine in tests
    "ANN",     # test annotations encouraged but not enforced by linter
    "ARG",     # unused fixture args are normal in pytest
    "D",       # test docstrings are optional (test names are documentation)
]
"src/**/adapters/**/*.py" = [
    "S603",    # subprocess call -- required for docker/redo integration
    "S607",    # partial executable path -- required for docker/redo
]

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.ruff.lint.mccabe]
max-complexity = 10
```

#### Ruff Suppression Policy

**When ruff reports a violation, fix it. Do not suppress it.** Run `ruff rule <CODE>` (e.g., `ruff rule TRY003`) to understand a rule before deciding it does not apply.

The narrow legitimate exceptions:
- The `[tool.ruff.lint.per-file-ignores]` table above carries the only project-wide suppressions (test-file relaxations, `subprocess` calls in adapters). Adding a new entry here requires a justifying comment on the same line and reviewer agreement.
- Inline `# noqa: <CODE>` is **not allowed**. If you find yourself reaching for it, the right answer is almost always to fix the code, not silence the linter.

### Mypy Configuration

Mypy is configured in strict mode with explicit flags listed for documentation. Only `warn_unreachable` is additive beyond strict.

```toml
# In pyproject.toml
[tool.mypy]
python_version = "3.13"
strict = true
warn_return_any = true
warn_unused_configs = true
warn_unreachable = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
disallow_untyped_decorators = true
check_untyped_defs = true
no_implicit_optional = true
no_implicit_reexport = true
```

---

## Architecture Rules

These correspond to the dependency rules in [ARCHITECTURE.md](ARCHITECTURE.md). They are enforced by automated import linting tests.

1. **Files in `commands/`** must not import from `cli.py` or from `click`.
2. **Files in `services/`** must not import from `commands/` or `cli.py`.
3. **Files in `adapters/`** must not import from `services/`, `commands/`, or `cli.py`.
4. **No circular imports** anywhere in the codebase.
5. **The CLI adapter (`cli.py`)** contains only Click decorators, argument parsing, service wiring, and calls to command classes. No business logic. Aim for under 10 lines per function body. The automated test enforces a hard limit of 15 lines to allow Click decorator boilerplate.
6. **Every `Command` subclass** must declare `name`, `help`, and `requires_project`. This is verified by automated contract tests.
7. **Every `Command` subclass** must have a corresponding Click entry in `cli.py`, and vice versa. This is verified by parity tests.
8. **Exception classes** live in `src/admt/exceptions.py`. All admt-specific exceptions are defined there.
9. **Only adapters may import `subprocess`.** Commands and services interact with external tools through adapters.

---

## Type Annotations

All code is fully type-annotated. No exceptions.

```python
# Good
def map_host_to_container(self, host_path: Path) -> Path: ...

def load_config(self) -> AdmtConfig | None: ...

def exec(self, command: str, workdir: Path, timeout: int = 30) -> Result: ...

# Bad -- missing annotations
def map_host_to_container(self, host_path): ...

def load_config(self): ...
```

No `Any` except at adapter boundaries where external tool output is genuinely untyped. Adapters that use `Any` must document why in a comment at the usage site.

No `# type: ignore` without an adjacent comment explaining why. mypy runs in strict mode -- there are no global suppressions.

### Why This Matters

1. **Catches bugs at analysis time.** Agents frequently pass wrong types, return `None` when a value is expected, or confuse `str` and `Path`. mypy catches these before runtime.
2. **Self-documenting interfaces.** A function signature tells the reader exactly what to pass and what to expect back, without reading the implementation.
3. **Prevents interface drift.** When a service interface changes, mypy flags every call site that needs updating.

Use `TypeAlias`, `TypeVar`, `Protocol`, `dataclass`, and `NamedTuple` where they improve clarity. Prefer `dataclass` for structured data over plain dicts.

---

## Code Style

### General

- **Line length:** 100 characters (configured in ruff).
- **Imports:** Sorted by ruff (`I` rules). Stdlib first, then third-party, then local.
- **Naming:** Follow PEP 8. `snake_case` for functions and variables, `PascalCase` for classes, `UPPER_SNAKE` for constants. Private methods use `_leading_underscore`. Module names are `snake_case.py` and match the primary class (`container.py` contains `ContainerService`).
- **Flat is better than nested.** If a function has more than two levels of indentation, restructure it. Use early returns, guard clauses, and extracted helpers.
- **One function, one job.** If a function needs a comment explaining what "the second half" does, it is two functions. If the name requires "and," split it.

### Strings

- Use double quotes for strings (ruff default).
- Use f-strings for interpolation, not `%` or `.format()`.
- Use `Path` objects for filesystem paths, not raw strings.

### Data Structures

- Use `dataclass` or `NamedTuple` for structured data.
- Use `StrEnum` for finite sets of string values (exit codes, command names, etc.).
- Avoid raw dicts for structured data that crosses function boundaries.

---

## File and Function Size

- **Source files:** ~300 lines as a guideline. Consistently exceeding this means the module is doing too much and should be split. This is a signal, not a hard gate.
- **CLI adapter functions:** Aim for under 10 lines. They consist of Click decorators, service wiring, and a single command call. Any logic in the CLI adapter is a design error. The automated test enforces a hard limit of 15 lines.
- **Functions:** ~50 lines signals that extraction is needed. If a function needs a comment explaining what "the second half" does, it is two functions.
- **Names are documentation.** If the function name does not explain what it does, rename it. Do not add a docstring to compensate for a bad name.
- **Explicit return types.** Always annotate return types, including `-> None`.

---

## Comments and Documentation

### Code Comments

Code must be well-commented. Comments explain **why**, not **what**. If the code needs a comment explaining what it does, rename the variables or extract a function.

```python
# Good -- explains why
# redo routes human output to stderr; we translate to stdout at the admt boundary
stdout_content = process.stderr

# Bad -- restates the code
# Set x to 5
x = 5
```

Comments are required for:

- Non-obvious business logic or Adamant-specific conventions
- Workarounds or known limitations
- Constants and magic values (explain their origin)
- Regular expressions (explain what they match)

### Docstrings

Every public class and public method gets a docstring. Use Google-style docstrings:

```python
def host_to_container(self, host_path: Path) -> Path:
    """Map a host filesystem path to its container equivalent.

    Searches the volume mount table for the longest prefix match.
    For example, if /Users/dev/projects/adamant is mounted at
    /home/user/adamant, then /Users/dev/projects/adamant/src/foo
    maps to /home/user/adamant/src/foo.

    Args:
        host_path: Absolute path on the host filesystem.

    Returns:
        The corresponding absolute path inside the container.

    Raises:
        PathNotMappedError: If host_path is not under any volume mount.
    """
```

Private methods (`_helper`) get docstrings if their purpose is not obvious from the name. Skip docstrings for trivial methods where the name says it all.

### Module-Level Docstrings

Every module (`.py` file) gets a one-line docstring at the top explaining its role:

```python
"""Container lifecycle management and command execution."""
```

No commented-out code. Delete it. Git remembers.

### License Headers

Do **not** add per-file SPDX tags or Apache-2.0 license headers. The top-level `LICENSE` file governs the entire repository. Per-file headers add noise to every module and drift out of sync over time.

---

## DRY and Abstraction

DRY (Don't Repeat Yourself) is important for maintainability. **Prefer making helpers to reduce code duplication.**

- If the same logic appears in three or more places, extract it.
- If two functions differ only in one parameter, combine them.
- If a pattern is shared across multiple commands (e.g., container passthrough), encode it in a base class or service.

However:

- **No abstraction without duplication.** Do not create a helper for something that happens once. Three concrete instances of a pattern is the threshold for extraction.
- **Helpers must be simpler than the code they replace.** If the helper is more complex than the three call sites inlined, it is not helping.
- **Name helpers for what they do, not where they are used.** `ensure_directory_exists()` not `setup_for_create_command()`.
- **No `utils.py` catch-all.** When extraction is warranted, the helper should live in the module where it is used. Shared helpers across modules belong in a service.

---

## Error Handling

### Principles

- **Fail early, fail loudly, clean up.** If something is wrong, stop immediately. Do not attempt partial operations.
- **Error messages name the problem and the fix.** Not "operation failed" -- instead:
  ```
  Error: Container 'adamant_example' is not running.
    Run 'admt env start' first, or pass '--yes' to auto-start.
  ```
- **Print failed command info even without `--verbose`.** When a redo command fails, show what command failed. The user should not need to re-run with `-v` to see what went wrong.
- **Use distinct exit codes.** See [ARCHITECTURE.md](ARCHITECTURE.md) for exit code table.

### Exception Strategy

- Define a small hierarchy of admt-specific exceptions in `src/admt/exceptions.py`:

```python
class AdmtError(Exception):
    """Base exception for all admt errors."""
    exit_code: int = 1

class ContainerError(AdmtError):
    """Container is not running or not reachable."""
    exit_code: int = 2

class ConfigError(AdmtError):
    """No project configured or config file is invalid."""
    exit_code: int = 2

class ArgumentError(AdmtError):
    """Missing required argument or invalid value."""
    exit_code: int = 3

class PathNotMappedError(AdmtError):
    """Current directory is not mapped into the active container."""
    exit_code: int = 4
```

- Commands and services raise these exceptions. The CLI adapter catches them and formats error messages.
- Never catch bare `Exception` unless re-raising. Never silently swallow exceptions.
- External failures (docker, redo) should be wrapped in admt exceptions with context.

---

## Subprocess Handling

- **Always use `subprocess.run()` or `subprocess.Popen()`.** Never use `os.system()` or `os.popen()`.
- **Never use `shell=True` on the host side.** Shell invocation introduces injection risks and platform-dependent behavior.
- **Prefer passing commands as lists, not strings, on the host side.** Lists avoid shell parsing and quoting issues.
- **Subprocess calls belong only in the adapters layer.** Commands and services must not call subprocess directly; they delegate to adapters.
- **Validate and escape paths before interpolating into shell command strings that run inside the container.** Even when `shell=True` is unavoidable inside a container exec, paths must be sanitized.
- **Handle subprocess timeouts on bounded calls.** Calls with a predictable upper bound -- `docker compose ps`, env snapshot capture, status checks, `docker compose exec true` reachability probes, short informational commands -- must pass `timeout=` to `subprocess.run()` and handle `subprocess.TimeoutExpired` explicitly. Long-running passthrough calls (`redo all`, `redo test`, `redo coverage`, interactive `docker compose exec bash`, `docker compose up`) **must not** set a timeout: a real Adamant build can legitimately run for many minutes, and a timeout would kill legitimate work. Each streaming passthrough in the adapters layer should carry a short comment explaining why no timeout is set (e.g., `# no timeout: user-bounded build, can run indefinitely`).

---

## Dependencies

- **Minimize dependencies.** Every dependency is a maintenance burden and a potential supply chain risk.
- **Justify every addition.** "It would save a few lines" is not justification. A dependency must solve a problem that is hard to solve correctly in-house.
- **Approved dependencies:** Click, ruamel.yaml, pykwalify (post-MVP). These are justified by the problem domain.
- **No framework imports.** admt interacts with the Adamant framework through its public interfaces: CLI commands, schemas, and file conventions. Importing framework internals (e.g., `from gen.models.component import component`) creates coupling that makes admt fragile to framework changes. This is a post-MVP concern but the principle holds now.
- **Pin versions in `pyproject.toml`.** Use `>=X.Y,<X+1` bounds. Do not use unpinned `*` dependencies.

---

## Testing Requirements

See [TEST_PLAN.md](TEST_PLAN.md) for the full testing strategy. The rules here are the non-negotiable minimums:

1. **Every new public method gets at least one test.** No exceptions.
2. **Every bug fix includes a test that reproduces the failure** before the fix and passes after.
3. **Tests are code.** They follow the same quality rules as production code. No copy-paste test matrices, no 500-line test files, no untested test helpers.
4. **Run the full test suite before considering any task complete.** If tests fail, fix them before moving on.
5. **100% test coverage is required.** This is non-negotiable. Coverage is measured by line and by branch. Lines that genuinely cannot be tested (e.g., `if __name__ == "__main__"`) may be excluded with `# pragma: no cover`, but this must be rare and justified.
6. **Test edge cases explicitly:** missing files, invalid config, container not running, permission errors, paths not under volume mounts. These are the cases agents tend to skip.

---

## Git and Commit Discipline

- **One logical change per commit.** Do not mix refactoring with new features. Do not mix formatting fixes with bug fixes.
- **Commit messages explain why**, not what. The diff shows what changed; the message explains the motivation. Imperative mood, under 72 characters for the subject line.
- **Do not commit generated files.** `.pyc`, `__pycache__/`, `build/`, `.mypy_cache/`, `.ruff_cache/` are all in `.gitignore`.
- **Do not commit secrets.** No API keys, passwords, or credentials. No `.env` files with real values.
- **No force-push** to shared branches without coordination.

### Commit Granularity (Especially for Agents)

Agents naturally produce iterative WIP. That iteration belongs in the working tree, not in git history.

- **Do not commit until the [quality gate](TEST_PLAN.md#quality-gate) passes.** Partial work, broken tests, type errors, and lint failures stay uncommitted. Use `git stash` if you need to switch context with dirty state.
- **Prefer one polished commit per phase** (or per logical sub-step) over a noisy WIP trail. A clean commit history is part of the deliverable -- reviewers should be able to read it linearly without "fix typo", "wip", "address review" entries.
- **Amending the last commit is acceptable** before push to fold late fixes into the same logical change. Once pushed and reviewed, prefer a follow-up commit.

### TODO and Placeholder Policy

- **No `TODO`, `FIXME`, `XXX`, or `HACK` markers in committed code.** If something is not done, it does not ship. If it is done but imperfect, refine it now.
- **The single allowed exception:** `TODO(post-mvp): <one-line description>` referencing a roadmap item in [MVP_PLAN.md](MVP_PLAN.md#roadmap-post-mvp). This is for capabilities the MVP scope deliberately defers, not for unfinished work in the current change.
- **No commented-out code.** Delete it. (Already covered in [What Not to Do](#what-not-to-do); restated here because it is the most common form of "TODO via comment".)

### What to Review Per PR

Each PR is one phase from [MVP_PLAN.md](MVP_PLAN.md) (or one logical sub-step within a phase). Reviewers verify, in this order:

1. **Spec traceability.** Every change traces to a section of ARCHITECTURE / CODING_RULES / TEST_PLAN / MVP_PLAN. No speculative features, no unrequested refactors.
2. **Quality gate green.** `ruff format --check`, `ruff check`, `mypy src/`, and `pytest --cov --cov-branch --cov-fail-under=100` all pass on the PR head. CI confirms this; reviewers check that CI ran on the latest commit.
3. **Failure paths covered.** Tests cover the error paths from the [TEST_PLAN.md "What to Test" table](TEST_PLAN.md#what-to-test), not just the happy path.
4. **No suppression escapes.** No `# noqa`, no `# type: ignore` without an adjacent reason, no new `per-file-ignores` entries without justification, no `TODO`/`FIXME` markers (except the documented `TODO(post-mvp)` form).
5. **History hygiene.** Single logical commit (or a short, clean series), commit message explains *why*, no merge commits from `main` into the feature branch (rebase instead).

---

## What Not to Do

These anti-patterns are specifically called out because they are common in agent-generated code:

1. **Do not hardcode YAML field names or structures.** If it is in an Adamant schema, read the schema. If it is not in a schema, it is not admt's concern. (Post-MVP, when schema-driven creation is implemented.)

2. **Do not add features beyond what was asked.** A bug fix does not need surrounding code cleaned up. A new command does not need extra configurability that was not requested. Do not add docstrings or type annotations to code you did not change.

3. **Do not create helpers for one-time operations.** Three similar lines of code is better than a premature abstraction.

4. **Do not add error handling for impossible scenarios.** Trust internal code. Only validate at system boundaries (user input, external commands, Docker responses).

5. **Do not leave commented-out code.** Delete it. Git remembers.

6. **Do not add backwards-compatibility shims.** If code is unused, delete it. No `_old_function()` aliases, no `# removed` comments.

7. **Do not generate code that the framework's own generators should produce.** admt triggers code generation; it does not duplicate the generators.

8. **Do not import Click in commands or services.** Click lives in `cli.py` only.

9. **Do not use raw dicts for structured data.** Use dataclasses, named tuples, or typed objects.

10. **Do not skip writing tests** because "it's simple" or "it obviously works." Write the test.

---

## Agent-Specific Rules

These rules apply to AI agents contributing to admt. They supplement -- not replace -- all rules above.

1. **Read these rules before writing any code.** The design documents (ARCHITECTURE.md, CODING_RULES.md, MVP_PLAN.md, TEST_PLAN.md) are the specification. Code that does not trace to the spec is rejected.

2. **No speculative features.** Every line of code must trace to the design documents. No "improvements" beyond what was asked. No refactoring of code you were not asked to touch.

3. **Write tests for every new public method.** No exceptions. Tests are written alongside the implementation, not after.

4. **Run the full test suite before completing a task.** A green test suite is the minimum bar for submitting work.

5. **No new dependencies without human approval.** If a task seems to require a new dependency, stop and ask.

6. **Stay within the established directory structure.** Do not create new top-level directories or reorganize existing structure without approval.

7. **When in doubt: smaller, simpler, fewer dependencies.** The right answer is almost always less code, not more.
