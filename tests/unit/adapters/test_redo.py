"""Tests for RedoAdapter -- pure string construction."""

from pathlib import Path

from admt.adapters.redo import RedoAdapter


def test_build_command_basic_target():
    cmd = RedoAdapter.build_command("all", cwd=Path("/home/user/proj"))
    assert cmd == "cd /home/user/proj && redo all"


def test_build_command_with_debug():
    cmd = RedoAdapter.build_command("test", cwd=Path("/home/user/proj"), debug=True)
    assert cmd == "cd /home/user/proj && DEBUG=1 redo test"


def test_build_command_debug_false_is_default():
    cmd = RedoAdapter.build_command("style", cwd=Path("/home/user/proj"))
    assert "DEBUG=1" not in cmd


def test_build_command_with_nested_path():
    cmd = RedoAdapter.build_command("all", cwd=Path("/home/user/proj/src/components/foo"))
    assert cmd == "cd /home/user/proj/src/components/foo && redo all"


def test_build_command_with_file_target():
    cmd = RedoAdapter.build_command("build/obj/Linux/foo.o", cwd=Path("/home/user/proj"))
    assert cmd.endswith("&& redo build/obj/Linux/foo.o")


def test_build_command_with_all_suffix_target():
    cmd = RedoAdapter.build_command("test_all", cwd=Path("/home/user/proj"))
    assert cmd.endswith("&& redo test_all")
