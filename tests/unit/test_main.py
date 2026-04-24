"""Tests for the ``admt.main`` entry point and its SIGINT handler."""

import signal
from unittest.mock import patch

import pytest

from admt.main import _SIGINT_EXIT_CODE, _sigint_handler, main


def test_main_installs_sigint_handler_and_runs_cli():
    with (
        patch("admt.main.cli") as mock_cli,
        patch("admt.main.signal.signal") as mock_signal,
    ):
        main()
    mock_cli.assert_called_once_with()
    # First call installs the SIGINT handler.
    sig_call = mock_signal.call_args_list[0]
    assert sig_call.args[0] is signal.SIGINT
    assert sig_call.args[1] is _sigint_handler


def test_sigint_handler_exits_130_with_no_active_processes(capsys):
    with (
        patch("admt.main.iter_active_pids", return_value=[]),
        pytest.raises(SystemExit) as exc_info,
    ):
        _sigint_handler(signal.SIGINT, None)
    assert exc_info.value.code == _SIGINT_EXIT_CODE
    captured = capsys.readouterr()
    assert "Interrupted" in captured.err


def test_sigint_handler_reports_active_pids(capsys):
    with (
        patch("admt.main.iter_active_pids", return_value=[111, 222]),
        pytest.raises(SystemExit),
    ):
        _sigint_handler(signal.SIGINT, None)
    captured = capsys.readouterr()
    assert "111" in captured.err
    assert "222" in captured.err
