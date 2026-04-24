"""Tests for the admt exception hierarchy."""

import pytest

from admt.exceptions import (
    AdmtError,
    ArgumentError,
    ConfigError,
    ContainerError,
    PathNotMappedError,
)


@pytest.mark.parametrize(
    ("exc_cls", "expected_exit_code"),
    [
        (AdmtError, 1),
        (ContainerError, 2),
        (ConfigError, 2),
        (ArgumentError, 3),
        (PathNotMappedError, 4),
    ],
)
def test_exception_exit_codes(exc_cls, expected_exit_code):
    assert exc_cls.exit_code == expected_exit_code


def test_subclasses_inherit_from_admt_error():
    for cls in (ContainerError, ConfigError, ArgumentError, PathNotMappedError):
        assert issubclass(cls, AdmtError)


def test_exception_message_is_propagated():
    exc = ConfigError("no project configured")
    assert str(exc) == "no project configured"


def test_exception_can_be_raised_and_caught_as_admt_error():
    msg = "nope"
    with pytest.raises(AdmtError) as exc_info:
        raise PathNotMappedError(msg)
    assert exc_info.value.exit_code == PathNotMappedError.exit_code
