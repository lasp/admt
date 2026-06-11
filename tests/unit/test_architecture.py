"""Structural tests that enforce ARCHITECTURE.md's dependency and size rules.

These tests check the shape of the codebase, not runtime behavior. They
prevent architectural drift as the project grows.
"""

import ast
import importlib
import pkgutil
from pathlib import Path

import click
import pytest

import admt
from admt.cli import cli as cli_group
from admt.commands.base import Command, ContainerPassthroughCommand

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "admt"
COMMANDS_DIR = SRC_ROOT / "commands"
SERVICES_DIR = SRC_ROOT / "services"
ADAPTERS_DIR = SRC_ROOT / "adapters"
CLI_FILE = SRC_ROOT / "cli.py"

# ARCHITECTURE.md and TEST_PLAN.md cap cli.py function bodies at 15 lines --
# any logic past that belongs in a Command class, not the CLI adapter.
MAX_CLI_FUNCTION_LINES = 15


def _imports_of(filepath: Path) -> set[str]:
    tree = ast.parse(filepath.read_text())
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _is_forbidden(imp: str, forbidden: str) -> bool:
    return imp == forbidden or imp.startswith(forbidden + ".")


@pytest.mark.parametrize("py_file", list(COMMANDS_DIR.glob("*.py")))
def test_commands_do_not_import_click(py_file):
    for imp in _imports_of(py_file):
        assert not _is_forbidden(imp, "click"), (
            f"{py_file.name} imports click -- commands must not depend on the CLI layer"
        )


@pytest.mark.parametrize("py_file", list(SERVICES_DIR.glob("*.py")))
def test_services_do_not_import_commands_or_cli(py_file):
    for imp in _imports_of(py_file):
        assert not _is_forbidden(imp, "admt.commands"), (
            f"{py_file.name} imports from admt.commands -- services must not depend on commands"
        )
        assert not _is_forbidden(imp, "admt.cli"), (
            f"{py_file.name} imports admt.cli -- services must not depend on the CLI layer"
        )


@pytest.mark.parametrize("py_file", list(ADAPTERS_DIR.glob("*.py")))
def test_adapters_do_not_import_services_commands_or_cli(py_file):
    for imp in _imports_of(py_file):
        for forbidden in ("admt.services", "admt.commands", "admt.cli"):
            assert not _is_forbidden(imp, forbidden), (
                f"{py_file.name} imports {forbidden} -- adapters depend only on externals"
            )


@pytest.mark.parametrize(
    "py_file",
    list(COMMANDS_DIR.glob("*.py")) + list(SERVICES_DIR.glob("*.py")),
)
def test_only_adapters_import_subprocess(py_file):
    assert "subprocess" not in _imports_of(py_file), (
        f"{py_file} imports subprocess -- only adapters may use subprocess"
    )


def test_cli_functions_are_short():
    tree = ast.parse(CLI_FILE.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            body_lines = (node.end_lineno or node.lineno) - node.lineno
            assert body_lines <= MAX_CLI_FUNCTION_LINES, (
                f"Function '{node.name}' in cli.py is {body_lines} lines -- "
                f"cli.py must stay thin. Move logic to a command class."
            )


def test_no_circular_imports():
    for info in pkgutil.walk_packages(admt.__path__, prefix="admt."):
        importlib.import_module(info.name)


# Set of Context attribute names that the CLI adapter populates as part of
# building the Context for each command. Listing them explicitly (rather than
# introspecting the dataclass) means a future Context attribute is opt-in --
# adding a field doesn't silently widen what may be assigned outside cli.py.
_CONTEXT_FIELDS: frozenset[str] = frozenset(
    {
        "config_service",
        "output",
        "container_service",
        "path_mapper",
        "verbose",
        "quiet",
        "debug",
        "yes",
        "force",
        "noninteractive",
        "target",
        "path",
        "run_all",
    }
)
# Files that are allowed to assign to Context attributes. ``cli.py``
# populates Context fields from parsed CLI args (path/target/run_all);
# ``bootstrap.py`` was previously a violator and is now compliant -- listed
# here only for safety in case future wiring legitimately needs to set
# fields during construction. Any other file mutating Context attributes is
# the bootstrap-side-effect anti-pattern caught by Q5.
_CONTEXT_MUTATION_ALLOWLIST: frozenset[str] = frozenset({"cli.py", "context.py"})


def _context_attribute_assignments(filepath: Path) -> list[tuple[int, str]]:
    """Return ``(lineno, attr)`` for every ``<x>.<attr> = ...`` where attr is on Context.

    Detects assignments to names that match Context dataclass fields. Conservative:
    flags any ``X.attr = ...`` where ``attr`` is in ``_CONTEXT_FIELDS``, regardless
    of whether ``X`` is statically a Context. False positives are acceptable -- if
    a non-Context object happens to have the same attribute name, the file should
    rename it or be added to the allowlist with a justification.
    """
    tree = ast.parse(filepath.read_text())
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        hits.extend(
            (node.lineno, target.attr)
            for target in node.targets
            if isinstance(target, ast.Attribute) and target.attr in _CONTEXT_FIELDS
        )
    return hits


def test_only_cli_and_context_assign_to_context_attributes():
    """Only ``cli.py`` and ``context.py`` may assign to Context attributes.

    Catches the anti-pattern that motivated Q5 in MVP_RETRO.md: a "build a
    service" function (``bootstrap.build_container_service``) silently
    mutated ``context.path_mapper`` as a side effect of returning a
    ContainerService. The function's return-type signature lied about
    what it did. Any future regression of that pattern (in services/,
    commands/, adapters/, or bootstrap.py) trips this test.

    ``context.py`` is allowed because ``Context.resolve_container_path``
    is a method on the dataclass itself; ``cli.py`` is allowed because
    populating Context from parsed CLI args is the canonical builder
    pattern (see ``_parse_positional`` and the per-command callbacks).
    """
    violations: list[str] = []
    for py_file in SRC_ROOT.rglob("*.py"):
        if py_file.name in _CONTEXT_MUTATION_ALLOWLIST:
            continue
        for lineno, attr in _context_attribute_assignments(py_file):
            relative = py_file.relative_to(SRC_ROOT)
            violations.append(f"{relative}:{lineno}  assigns to context.{attr}")
    assert not violations, (
        "Context attributes may only be assigned in cli.py and context.py. "
        "Other files must not mutate Context as a side effect "
        "(see Q5 in MVP_RETRO.md):\n  " + "\n  ".join(violations)
    )


def _all_concrete_command_subclasses():
    """Discover concrete Command subclasses, skipping abstract bases like CPC.

    ``ContainerPassthroughCommand`` is abstract (Q4): its ``__new__`` raises
    ``TypeError`` on direct instantiation, and ``__init_subclass__`` requires
    concrete subclasses to declare ``redo_target``. Python's
    ``__abstractmethods__`` heuristic doesn't flag CPC (we use ``__new__``,
    not ``@abstractmethod``), so this loop filters it explicitly.
    """
    result: list[type[Command]] = []
    stack: list[type[Command]] = list(Command.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls is ContainerPassthroughCommand:
            stack.extend(cls.__subclasses__())
            continue
        if not getattr(cls, "__abstractmethods__", set()):
            result.append(cls)
        stack.extend(cls.__subclasses__())
    return result


def _click_command_names(group, prefix=""):
    names: set[str] = set()
    for name, cmd in group.commands.items():
        full_name = f"{prefix} {name}".strip()
        if isinstance(cmd, click.Group):
            names.update(_click_command_names(cmd, full_name))
        else:
            names.add(full_name)
    return names


def test_all_concrete_commands_declare_required_metadata():
    for cls in _all_concrete_command_subclasses():
        assert getattr(cls, "name", None), f"{cls.__name__} missing 'name'"
        assert getattr(cls, "help", None), f"{cls.__name__} missing 'help'"
        assert hasattr(cls, "requires_project"), f"{cls.__name__} missing 'requires_project'"


def test_every_concrete_command_has_click_entry():
    click_names = _click_command_names(cli_group)
    for cls in _all_concrete_command_subclasses():
        assert cls.name in click_names, (
            f"Command '{cls.name}' ({cls.__name__}) has no Click entry in cli.py"
        )


def test_every_click_entry_has_command():
    click_names = _click_command_names(cli_group)
    command_names = {cls.name for cls in _all_concrete_command_subclasses()}
    for name in click_names:
        assert name in command_names, (
            f"Click command '{name}' has no corresponding Command subclass"
        )
