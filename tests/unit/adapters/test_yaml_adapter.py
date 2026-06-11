"""Tests for the YAML adapter: round-trip load/dump of config files."""

import pytest

from admt.adapters.yaml_adapter import YamlAdapter
from admt.exceptions import ConfigError


@pytest.fixture
def adapter():
    return YamlAdapter()


def test_load_and_dump_round_trip(adapter, tmp_path):
    source = tmp_path / "example.yml"
    source.write_text("name: demo\nvalue: 42\n")
    doc = adapter.load(source)
    target = tmp_path / "out.yml"
    adapter.dump(doc, target)
    assert adapter.load(target) == doc


def test_dump_creates_parent_directories(adapter, tmp_path):
    target = tmp_path / "nested" / "dir" / "out.yml"
    adapter.dump({"k": "v"}, target)
    assert target.exists()


def test_load_raises_for_missing_file(adapter, tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        adapter.load(tmp_path / "missing.yml")


def test_load_raises_for_directory_as_path(adapter, tmp_path):
    # Opening a directory via ``open("r")`` raises IsADirectoryError, a
    # subclass of OSError, which the adapter wraps into ConfigError.
    (tmp_path / "dir").mkdir()
    with pytest.raises(ConfigError, match="Cannot read YAML file"):
        adapter.load(tmp_path / "dir")


def test_load_wraps_parse_errors_as_config_error(adapter, tmp_path):
    """Malformed YAML must become a clean ConfigError, not a ruamel traceback."""
    bad = tmp_path / "bad.yml"
    bad.write_text("{[definitely: not: yaml")
    with pytest.raises(ConfigError, match="Cannot parse YAML"):
        adapter.load(bad)


def test_dump_wraps_write_errors_as_config_error(adapter, tmp_path):
    """An unwritable destination must become a clean ConfigError, not OSError."""
    # A FILE where a parent directory is needed makes mkdir raise OSError.
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    with pytest.raises(ConfigError, match="Cannot write YAML"):
        adapter.dump({"k": "v"}, blocker / "out.yml")
