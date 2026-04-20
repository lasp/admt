"""Tests for the YAML adapter: round-trip load/dump and Docker Compose parsing."""

from pathlib import Path

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


def _write_compose(tmp_path, body):
    docker = tmp_path / "proj" / "docker"
    docker.mkdir(parents=True)
    path = docker / "docker-compose.yml"
    path.write_text(body)
    return path


def test_parse_compose_short_form_volume(adapter, tmp_path):
    path = _write_compose(
        tmp_path,
        """name: demo
services:
  demo:
    container_name: demo_container
    volumes:
      - ../../adamant:/home/user/adamant
""",
    )
    compose = adapter.parse_compose(path)
    assert compose.project_name == "demo"
    svc = compose.services["demo"]
    assert svc.container_name == "demo_container"
    # ../../adamant relative to /tmp/.../proj/docker resolves to /tmp/.../adamant
    expected_host = (tmp_path / "adamant").resolve()
    assert svc.volumes == {expected_host: Path("/home/user/adamant")}


def test_parse_compose_long_form_bind_volume(adapter, tmp_path):
    path = _write_compose(
        tmp_path,
        """name: demo
services:
  demo:
    volumes:
      - type: bind
        source: ../../adamant
        target: /home/user/adamant
""",
    )
    compose = adapter.parse_compose(path)
    svc = compose.services["demo"]
    expected_host = (tmp_path / "adamant").resolve()
    assert svc.volumes == {expected_host: Path("/home/user/adamant")}


def test_parse_compose_mixed_forms(adapter, tmp_path):
    path = _write_compose(
        tmp_path,
        """name: demo
services:
  demo:
    volumes:
      - ../../adamant:/home/user/adamant
      - type: bind
        source: ../../sibling
        target: /home/user/sibling
""",
    )
    compose = adapter.parse_compose(path)
    svc = compose.services["demo"]
    assert (tmp_path / "adamant").resolve() in svc.volumes
    assert (tmp_path / "sibling").resolve() in svc.volumes


def test_parse_compose_skips_named_volumes(adapter, tmp_path):
    path = _write_compose(
        tmp_path,
        """name: demo
services:
  demo:
    volumes:
      - named_volume:/var/data
      - ../../adamant:/home/user/adamant
""",
    )
    compose = adapter.parse_compose(path)
    svc = compose.services["demo"]
    # named_volume skipped; only the bind mount remains.
    assert len(svc.volumes) == 1
    assert Path("/home/user/adamant") in svc.volumes.values()


def test_parse_compose_skips_non_bind_long_form(adapter, tmp_path):
    path = _write_compose(
        tmp_path,
        """name: demo
services:
  demo:
    volumes:
      - type: volume
        source: named
        target: /var/data
      - ../../adamant:/home/user/adamant
""",
    )
    compose = adapter.parse_compose(path)
    svc = compose.services["demo"]
    assert len(svc.volumes) == 1


def test_parse_compose_absolute_source(adapter, tmp_path):
    path = _write_compose(
        tmp_path,
        """name: demo
services:
  demo:
    volumes:
      - /abs/src:/home/user/abs
""",
    )
    compose = adapter.parse_compose(path)
    svc = compose.services["demo"]
    assert svc.volumes == {Path("/abs/src"): Path("/home/user/abs")}


def test_parse_compose_missing_services_raises(adapter, tmp_path):
    path = _write_compose(tmp_path, "name: demo\n")
    with pytest.raises(ConfigError, match="services"):
        adapter.parse_compose(path)


def test_parse_compose_non_mapping_raises(adapter, tmp_path):
    path = _write_compose(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="mapping"):
        adapter.parse_compose(path)


def test_parse_compose_multiple_services(adapter, tmp_path):
    path = _write_compose(
        tmp_path,
        """name: demo
services:
  primary:
    container_name: primary_c
    volumes:
      - ../../adamant:/home/user/adamant
  secondary:
    volumes:
      - ../../other:/home/user/other
""",
    )
    compose = adapter.parse_compose(path)
    assert set(compose.services) == {"primary", "secondary"}
    assert compose.services["primary"].container_name == "primary_c"
    assert compose.services["secondary"].container_name is None


def test_parse_compose_no_project_name(adapter, tmp_path):
    path = _write_compose(
        tmp_path,
        """services:
  demo:
    volumes: []
""",
    )
    compose = adapter.parse_compose(path)
    assert compose.project_name is None


def test_parse_compose_service_not_mapping_raises(adapter, tmp_path):
    path = _write_compose(
        tmp_path,
        """services:
  demo: "just a string"
""",
    )
    with pytest.raises(ConfigError, match="not a mapping"):
        adapter.parse_compose(path)


def test_parse_compose_volumes_not_list_raises(adapter, tmp_path):
    path = _write_compose(
        tmp_path,
        """services:
  demo:
    volumes: "just-a-string"
""",
    )
    with pytest.raises(ConfigError, match="non-list"):
        adapter.parse_compose(path)


def test_parse_compose_malformed_short_volume_raises(adapter, tmp_path):
    path = _write_compose(
        tmp_path,
        """services:
  demo:
    volumes:
      - "no-colon-here"
""",
    )
    with pytest.raises(ConfigError, match="malformed"):
        adapter.parse_compose(path)


def test_parse_compose_unrecognized_volume_entry_raises(adapter, tmp_path):
    path = _write_compose(
        tmp_path,
        """services:
  demo:
    volumes:
      - 42
""",
    )
    with pytest.raises(ConfigError, match="unrecognized"):
        adapter.parse_compose(path)


def test_parse_compose_long_form_missing_target_skipped(adapter, tmp_path):
    path = _write_compose(
        tmp_path,
        """services:
  demo:
    volumes:
      - type: bind
        source: ../../adamant
""",
    )
    compose = adapter.parse_compose(path)
    assert compose.services["demo"].volumes == {}
