"""Tests for PathMapperService -- longest-prefix host<->container mapping."""

from pathlib import Path

import pytest

from admt.exceptions import PathNotMappedError
from admt.services.path_mapper import PathMapperService


@pytest.fixture
def mapper():
    return PathMapperService(
        {
            Path("/sim/dev/projects/adamant"): Path("/home/user/adamant"),
            Path("/sim/dev/projects/adamant_example"): Path("/home/user/adamant_example"),
            Path("/sim/dev/projects/xmera-components"): Path("/home/user/xmera-components"),
        }
    )


def test_maps_nested_file_under_mount(mapper):
    host = Path("/sim/dev/projects/adamant/src/components/ccsds_router")
    assert mapper.host_to_container(host) == Path("/home/user/adamant/src/components/ccsds_router")


def test_maps_project_root_exactly(mapper):
    host = Path("/sim/dev/projects/adamant_example")
    assert mapper.host_to_container(host) == Path("/home/user/adamant_example")


def test_raises_for_unmapped_path(mapper):
    with pytest.raises(PathNotMappedError) as exc_info:
        mapper.host_to_container(Path("/sim/other/project/foo"))
    assert "is not under any volume mount" in str(exc_info.value)
    assert "/home/user/adamant" in str(exc_info.value)


def test_longest_prefix_match():
    mapper = PathMapperService(
        {
            Path("/sim/dev/projects"): Path("/home/user"),
            Path("/sim/dev/projects/adamant"): Path("/home/user/adamant"),
        }
    )
    host = Path("/sim/dev/projects/adamant/src/foo")
    assert mapper.host_to_container(host) == Path("/home/user/adamant/src/foo")


def test_is_mapped_true_for_mapped_path(mapper):
    assert mapper.is_mapped(Path("/sim/dev/projects/adamant/src")) is True


def test_is_mapped_false_for_unmapped_path(mapper):
    assert mapper.is_mapped(Path("/sim/other/repo")) is False


def test_container_to_host_maps_back(mapper):
    container = Path("/home/user/adamant/src/foo")
    assert mapper.container_to_host(container) == Path("/sim/dev/projects/adamant/src/foo")


def test_container_to_host_longest_prefix():
    mapper = PathMapperService(
        {
            Path("/sim/parent"): Path("/home/user"),
            Path("/sim/parent/adamant"): Path("/home/user/adamant"),
        }
    )
    assert mapper.container_to_host(Path("/home/user/adamant/src")) == Path(
        "/sim/parent/adamant/src"
    )


def test_container_to_host_raises_for_unmapped(mapper):
    with pytest.raises(PathNotMappedError):
        mapper.container_to_host(Path("/opt/other/path"))


def test_empty_mapper_reports_no_mounts_in_error():
    mapper = PathMapperService({})
    with pytest.raises(PathNotMappedError) as exc_info:
        mapper.host_to_container(Path("/any"))
    assert "no mounts configured" in str(exc_info.value)


def test_mounts_property_returns_copy(mapper):
    mounts = mapper.mounts
    mounts.clear()
    # Mutating the copy must not affect the internal table.
    assert mapper.mounts != {}


def test_host_to_container_resolves_non_strict():
    # ``non-strict`` means the path need not exist on disk. Use a clearly
    # non-existent path to confirm resolution does not raise.
    mapper = PathMapperService({Path("/sim/a"): Path("/home/user/a")})
    assert mapper.host_to_container(Path("/sim/a/nonexistent/child")) == Path(
        "/home/user/a/nonexistent/child"
    )
