"""Tests for the ``admt.main`` entry point."""

from unittest.mock import patch

from admt.main import main


def test_main_invokes_cli():
    with patch("admt.main.cli") as mock_cli:
        main()
    mock_cli.assert_called_once_with()
