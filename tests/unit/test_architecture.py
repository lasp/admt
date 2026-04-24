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


def _all_concrete_command_subclasses():
    """Discover concrete Command subclasses, skipping base classes like CPC.

    ``ContainerPassthroughCommand`` implements ``execute`` to share the
    redo-passthrough flow with its real subclasses (BuildCommand, ...), so
    it is technically "concrete" but is never registered as a CLI command
    itself. Filter it out explicitly.
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
