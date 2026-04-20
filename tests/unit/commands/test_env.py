"""Unit tests for EnvInitCommand and EnvUseCommand (services mocked)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from admt.commands.env import EnvInitCommand, EnvUseCommand
from admt.exceptions import ArgumentError, ConfigError
from admt.services.config import ProjectConfig


def _project_stub(name: str = "myproj", mounts: int = 2) -> ProjectConfig:
    return ProjectConfig(
        name=name,
        compose_file=Path(f"/sim/{name}/docker/docker-compose.yml"),
        compose_file_mtime=0,
        service_name=name,
        container_name=f"{name}_container",
        project_root=Path(f"/sim/{name}"),
        container_home=Path("/home/user"),
        volume_mounts=dict.fromkeys(
            (Path(f"/sim/{name}/mount{i}") for i in range(mounts)),
            Path(f"/home/user/{name}"),
        ),
        activate_script=Path(f"/home/user/{name}/env/activate"),
    )


# ----- EnvInitCommand -----


def test_env_init_with_explicit_path_registers(make_context):
    context = make_context()
    context.config_service.is_project_registered_at.return_value = None
    context.config_service.register_project.return_value = _project_stub()
    cmd = EnvInitCommand(Path("/sim/myproj"))
    result = cmd.execute(context)
    context.config_service.register_project.assert_called_once()
    call_args = context.config_service.register_project.call_args
    assert call_args.kwargs["force"] is False
    assert result.exit_code == 0


def test_env_init_with_no_path_uses_cwd(monkeypatch, make_context, tmp_path):
    monkeypatch.chdir(tmp_path)
    context = make_context()
    context.config_service.is_project_registered_at.return_value = None
    context.config_service.register_project.return_value = _project_stub()
    EnvInitCommand().execute(context)
    passed_root = context.config_service.register_project.call_args.args[0]
    assert passed_root == tmp_path.resolve()


def test_env_init_already_registered_force_overwrites(make_context):
    context = make_context(force=True)
    context.config_service.is_project_registered_at.return_value = "existing"
    context.config_service.register_project.return_value = _project_stub("existing")
    EnvInitCommand(Path("/sim/existing")).execute(context)
    assert context.config_service.register_project.call_args.kwargs["force"] is True


def test_env_init_already_registered_yes_declines(make_context):
    context = make_context(yes=True)
    context.config_service.is_project_registered_at.return_value = "existing"
    # Real OutputService behavior for --yes with default=False is already covered;
    # here we just ensure the command takes the "decline" path and never calls register.
    context.output.prompt.return_value = False
    result = EnvInitCommand(Path("/sim/existing")).execute(context)
    context.config_service.register_project.assert_not_called()
    assert result.exit_code == 0


def test_env_init_already_registered_noninteractive_raises(make_context):
    context = make_context(noninteractive=True)
    context.config_service.is_project_registered_at.return_value = "existing"
    with pytest.raises(ArgumentError, match="--force"):
        EnvInitCommand(Path("/sim/existing")).execute(context)


def test_env_init_already_registered_noninteractive_force_overwrites(make_context):
    context = make_context(noninteractive=True, force=True)
    context.config_service.is_project_registered_at.return_value = "existing"
    context.config_service.register_project.return_value = _project_stub("existing")
    EnvInitCommand(Path("/sim/existing")).execute(context)
    assert context.config_service.register_project.call_args.kwargs["force"] is True


def test_env_init_interactive_prompt_yes_proceeds(make_context):
    context = make_context()
    context.config_service.is_project_registered_at.return_value = "existing"
    context.output.prompt.return_value = True
    context.config_service.register_project.return_value = _project_stub("existing")
    EnvInitCommand(Path("/sim/existing")).execute(context)
    assert context.config_service.register_project.call_args.kwargs["force"] is True


def test_env_init_interactive_prompt_no_declines(make_context):
    context = make_context()
    context.config_service.is_project_registered_at.return_value = "existing"
    context.output.prompt.return_value = False
    result = EnvInitCommand(Path("/sim/existing")).execute(context)
    context.config_service.register_project.assert_not_called()
    assert result.exit_code == 0


def test_env_init_prints_success_message_on_fresh_register(make_context):
    context = make_context()
    context.config_service.is_project_registered_at.return_value = None
    project = _project_stub(mounts=3)
    context.config_service.register_project.return_value = project
    EnvInitCommand(Path("/sim/myproj")).execute(context)
    # success() is called with the "Registered..." line.
    success_calls = [call.args[0] for call in context.output.success.call_args_list]
    assert any("Registered project 'myproj'" in msg for msg in success_calls)
    assert any("3 volume mount" in msg for msg in success_calls)


def test_env_init_falls_back_to_context_path(make_context):
    context_path = Path("/sim/frompath")
    context = make_context(path=context_path)
    context.config_service.is_project_registered_at.return_value = None
    context.config_service.register_project.return_value = _project_stub()
    EnvInitCommand().execute(context)
    passed_root = context.config_service.register_project.call_args.args[0]
    assert passed_root == context_path.resolve()


# ----- EnvUseCommand -----


def test_env_use_switches_active_project(make_context):
    context = make_context()
    result = EnvUseCommand("other").execute(context)
    context.config_service.set_active_project.assert_called_once_with("other")
    info_calls = [call.args[0] for call in context.output.info.call_args_list]
    assert any("Active project: other" in msg for msg in info_calls)
    assert result.exit_code == 0


def test_env_use_propagates_config_error(make_context):
    context = make_context()
    msg = "No registered project named 'ghost'"
    context.config_service.set_active_project.side_effect = ConfigError(msg)
    with pytest.raises(ConfigError, match="ghost"):
        EnvUseCommand("ghost").execute(context)


# ----- Command metadata contract -----


def test_env_init_declares_metadata():
    assert EnvInitCommand.name == "env init"
    assert EnvInitCommand.help
    assert EnvInitCommand.requires_project is False


def test_env_use_declares_metadata():
    assert EnvUseCommand.name == "env use"
    assert EnvUseCommand.help
    assert EnvUseCommand.requires_project is False


# ----- Shared mock sanity -----


def test_mock_is_used(make_context):
    # Sanity check that make_context's config_service responds to mock methods.
    ctx = make_context()
    assert isinstance(ctx.config_service, MagicMock)
