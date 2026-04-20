"""Admt-specific exception hierarchy.

Every admt exception carries an ``exit_code`` class attribute that the CLI
adapter uses to produce meaningful, distinct process exit codes per
ARCHITECTURE.md Exit Codes.
"""


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
