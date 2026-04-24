"""Tests for OutputService -- routing, prompts, choices, echo, color."""

import os
from unittest.mock import patch

import pytest

from admt.exceptions import ArgumentError
from admt.services.output import OutputService


def _svc(**overrides):
    defaults = {"verbose": False, "quiet": False, "yes": False, "noninteractive": False}
    defaults.update(overrides)
    return OutputService(**defaults)


def test_info_writes_to_stdout(capsys):
    _svc().info("hello")
    captured = capsys.readouterr()
    assert captured.out == "hello\n"
    assert captured.err == ""


def test_info_is_suppressed_when_quiet(capsys):
    _svc(quiet=True).info("hidden")
    captured = capsys.readouterr()
    assert captured.out == ""


def test_success_follows_info_rules(capsys):
    _svc(quiet=True).success("also hidden")
    assert capsys.readouterr().out == ""


def test_warning_writes_to_stderr(capsys):
    _svc().warning("watch out")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "watch out\n"


def test_error_writes_to_stderr(capsys):
    _svc().error("bad")
    captured = capsys.readouterr()
    assert captured.err == "bad\n"


def test_flag_properties_reflect_constructor_arguments():
    svc = _svc(verbose=True, quiet=True, yes=True, noninteractive=True)
    assert svc.verbose is True
    assert svc.quiet is True
    assert svc.yes is True
    assert svc.noninteractive is True


def test_prompt_with_yes_and_default_true_returns_true():
    assert _svc(yes=True).prompt("Go?", default=True) is True


def test_prompt_with_yes_and_default_false_returns_false():
    assert _svc(yes=True).prompt("Delete?", default=False) is False


def test_prompt_with_yes_but_no_default_still_prompts():
    with patch("builtins.input", return_value="y"):
        assert _svc(yes=True).prompt("Pick?", default=None) is True


def test_prompt_noninteractive_raises():
    with pytest.raises(ArgumentError, match="NONINTERACTIVE"):
        _svc(noninteractive=True).prompt("Go?", default=True)


def test_prompt_user_enter_uses_default_true():
    with patch("builtins.input", return_value=""):
        assert _svc().prompt("Go?", default=True) is True


def test_prompt_user_enter_uses_default_false():
    with patch("builtins.input", return_value=""):
        assert _svc().prompt("Delete?", default=False) is False


def test_prompt_user_types_y():
    with patch("builtins.input", return_value="y"):
        assert _svc().prompt("Go?", default=False) is True


def test_prompt_user_types_yes_caps():
    with patch("builtins.input", return_value="YES"):
        assert _svc().prompt("Go?", default=False) is True


def test_prompt_user_types_n():
    with patch("builtins.input", return_value="n"):
        assert _svc().prompt("Go?", default=True) is False


def test_prompt_user_enter_with_no_default_returns_false():
    with patch("builtins.input", return_value=""):
        assert _svc().prompt("Pick?", default=None) is False


def test_prompt_suffix_default_true(capsys):
    with patch("builtins.input", return_value="y") as mocked:
        _svc().prompt("Go?", default=True)
    assert mocked.call_args.args[0] == "Go? [Y/n] "


def test_prompt_suffix_default_false():
    with patch("builtins.input", return_value="n") as mocked:
        _svc().prompt("Delete?", default=False)
    assert mocked.call_args.args[0] == "Delete? [y/N] "


def test_prompt_suffix_no_default():
    with patch("builtins.input", return_value="y") as mocked:
        _svc().prompt("Pick?", default=None)
    assert mocked.call_args.args[0] == "Pick? [y/n] "


def test_choose_single_item_returns_directly():
    assert _svc().choose("Pick one", ["only"]) == "only"


def test_choose_empty_list_raises():
    with pytest.raises(ArgumentError, match="at least one"):
        _svc().choose("Pick one", [])


def test_choose_noninteractive_raises():
    with pytest.raises(ArgumentError, match="NONINTERACTIVE"):
        _svc(noninteractive=True).choose("Pick", ["a", "b"])


def test_choose_prompts_until_valid(capsys):
    inputs = iter(["0", "99", "abc", "2"])
    with patch("builtins.input", lambda _prompt: next(inputs)):
        result = _svc().choose("Pick one", ["alpha", "beta"])
    assert result == "beta"
    captured = capsys.readouterr()
    assert "Pick one" in captured.out
    assert "[1] alpha" in captured.out
    assert "[2] beta" in captured.out
    assert "Enter a number between 1 and 2" in captured.err


# ----- command_echo -----


def test_command_echo_emits_when_verbose(capsys):
    _svc(verbose=True).command_echo("docker compose up -d")
    captured = capsys.readouterr()
    assert "$ docker compose up -d" in captured.out


def test_command_echo_silent_when_not_verbose(capsys):
    _svc().command_echo("docker compose up -d")
    assert capsys.readouterr().out == ""


def test_command_echo_survives_quiet_when_verbose(capsys):
    """``-v -q`` together: echo the command, suppress the subprocess output.

    Per ARCHITECTURE §TTY, this combination is explicitly useful for
    agents and scripts that want to see what admt is about to run without
    being flooded by the subprocess's own output.
    """
    _svc(verbose=True, quiet=True).command_echo("docker compose up -d")
    captured = capsys.readouterr()
    assert "$ docker compose up -d" in captured.out


# ----- ANSI color handling -----


def test_success_emits_green_when_stdout_is_tty(capsys):
    with patch("sys.stdout.isatty", return_value=True), patch.dict(os.environ, {}, clear=False):
        os.environ.pop("NO_COLOR", None)
        _svc().success("all good")
    captured = capsys.readouterr()
    assert "\033[32m" in captured.out
    assert "\033[0m" in captured.out


def test_warning_emits_yellow_when_stderr_is_tty(capsys):
    with patch("sys.stderr.isatty", return_value=True), patch.dict(os.environ, {}, clear=False):
        os.environ.pop("NO_COLOR", None)
        _svc().warning("heads up")
    captured = capsys.readouterr()
    assert "\033[33m" in captured.err


def test_error_emits_red_when_stderr_is_tty(capsys):
    with patch("sys.stderr.isatty", return_value=True), patch.dict(os.environ, {}, clear=False):
        os.environ.pop("NO_COLOR", None)
        _svc().error("nope")
    captured = capsys.readouterr()
    assert "\033[31m" in captured.err


def test_color_suppressed_when_no_tty(capsys):
    # pytest's capsys replaces stdout with a non-TTY capture; no color expected.
    _svc().success("plain")
    captured = capsys.readouterr()
    assert "\033[" not in captured.out


def test_color_suppressed_by_no_color_env(capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    with patch("sys.stdout.isatty", return_value=True):
        _svc().success("plain")
    captured = capsys.readouterr()
    assert "\033[" not in captured.out


def test_info_is_never_colored(capsys):
    with patch("sys.stdout.isatty", return_value=True):
        _svc().info("plain info")
    captured = capsys.readouterr()
    assert "\033[" not in captured.out


# ----- emit_captured -----


def test_emit_captured_writes_to_stdout_by_default(capsys):
    _svc(quiet=True).emit_captured("some captured text\n")
    captured = capsys.readouterr()
    # Bypasses --quiet.
    assert captured.out == "some captured text\n"


def test_emit_captured_routes_to_stderr_when_requested(capsys):
    _svc(quiet=True).emit_captured("oops\n", to_stderr=True)
    captured = capsys.readouterr()
    assert captured.err == "oops\n"
    assert captured.out == ""


def test_emit_captured_adds_trailing_newline_when_missing(capsys):
    _svc().emit_captured("no newline")
    captured = capsys.readouterr()
    assert captured.out == "no newline\n"


def test_emit_captured_empty_is_noop(capsys):
    _svc().emit_captured("")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
