"""Unit tests for env subcommands -- services mocked."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from admt.commands.env import (
    EnvBuildCommand,
    EnvExecCommand,
    EnvInitCommand,
    EnvListCommand,
    EnvLoginCommand,
    EnvPullCommand,
    EnvPushCommand,
    EnvRefreshCommand,
    EnvRestartCommand,
    EnvRmCommand,
    EnvStartCommand,
    EnvStatusCommand,
    EnvStopCommand,
    EnvUseCommand,
)
from admt.exceptions import ArgumentError, ConfigError, ContainerError
from admt.services.config import ProjectConfig
from admt.services.container import ContainerService, ContainerStatus


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


# ----- Container-backed commands (EnvStart/Stop/Restart/Login/Status/...) -----


def _ctx_with_container(make_context, **overrides):
    container = MagicMock(spec=ContainerService)
    ctx = make_context(container_service=container, **overrides)
    return ctx, container


def test_env_start_delegates(make_context):
    ctx, container = _ctx_with_container(make_context)
    result = EnvStartCommand().execute(ctx)
    container.start.assert_called_once()
    assert result.exit_code == 0


def test_env_stop_delegates(make_context):
    ctx, container = _ctx_with_container(make_context)
    result = EnvStopCommand().execute(ctx)
    container.stop.assert_called_once()
    assert result.exit_code == 0


def test_env_restart_delegates(make_context):
    ctx, container = _ctx_with_container(make_context)
    EnvRestartCommand().execute(ctx)
    container.restart.assert_called_once()


def test_env_login_propagates_exit_code(make_context):
    sentinel = 42
    ctx, container = _ctx_with_container(make_context)
    container.login.return_value = sentinel
    result = EnvLoginCommand().execute(ctx)
    assert result.exit_code == sentinel


def test_env_status_prints_project_and_state(make_context):
    ctx, container = _ctx_with_container(make_context)
    ctx.config_service.get_active_project.return_value = _project_stub("demo")
    container.status.return_value = ContainerStatus.RUNNING
    EnvStatusCommand().execute(ctx)
    info_lines = [c.args[0] for c in ctx.output.info.call_args_list]
    assert any("Project: demo" in line for line in info_lines)
    assert any("Status: running" in line for line in info_lines)


def test_env_build_push_pull_delegate(make_context):
    for cmd_cls, method in [
        (EnvBuildCommand, "build_image"),
        (EnvPushCommand, "push_image"),
        (EnvPullCommand, "pull_image"),
    ]:
        ctx, container = _ctx_with_container(make_context)
        cmd_cls().execute(ctx)
        getattr(container, method).assert_called_once()


def test_env_exec_uses_noninteractive_when_flag_set(make_context):
    ctx, container = _ctx_with_container(make_context, noninteractive=True)
    container.exec.return_value = 0
    EnvExecCommand("echo hi").execute(ctx)
    assert container.exec.call_args.kwargs["interactive"] is False


def test_env_exec_respects_stdin_tty_detection(make_context):
    sentinel = 7
    ctx, container = _ctx_with_container(make_context, noninteractive=False)
    container.exec.return_value = sentinel
    with patch("admt.commands.env.os.isatty", return_value=True):
        result = EnvExecCommand("bash").execute(ctx)
    assert container.exec.call_args.kwargs["interactive"] is True
    assert result.exit_code == sentinel


def test_env_refresh_delegates(make_context):
    ctx, container = _ctx_with_container(make_context)
    EnvRefreshCommand().execute(ctx)
    container.refresh.assert_called_once()


def test_env_rm_aborts_when_prompt_declines(make_context):
    ctx, container = _ctx_with_container(make_context)
    ctx.output.prompt.return_value = False
    ctx.config_service.get_active_project.return_value = _project_stub()
    result = EnvRmCommand().execute(ctx)
    container.rm.assert_not_called()
    assert result.exit_code == 0


def test_env_rm_delegates_when_confirmed(make_context):
    ctx, container = _ctx_with_container(make_context)
    ctx.output.prompt.return_value = True
    ctx.config_service.get_active_project.return_value = _project_stub()
    EnvRmCommand(remove_volumes=True).execute(ctx)
    container.rm.assert_called_once_with(remove_volumes=True, remove_image=False, remove_all=False)


def test_env_rm_remove_all_is_passed_through(make_context):
    ctx, container = _ctx_with_container(make_context)
    ctx.output.prompt.return_value = True
    ctx.config_service.get_active_project.return_value = _project_stub()
    EnvRmCommand(remove_all=True).execute(ctx)
    container.rm.assert_called_once_with(remove_volumes=False, remove_image=False, remove_all=True)


def test_env_rm_describes_scope_in_prompt(make_context):
    ctx, _container = _ctx_with_container(make_context)
    ctx.output.prompt.return_value = False
    ctx.config_service.get_active_project.return_value = _project_stub()
    EnvRmCommand(remove_volumes=True, remove_image=True).execute(ctx)
    prompt_message = ctx.output.prompt.call_args.args[0]
    assert "volumes" in prompt_message
    assert "image" in prompt_message


def test_env_rm_remove_all_prompt_uses_combined_label(make_context):
    ctx, _ = _ctx_with_container(make_context)
    ctx.output.prompt.return_value = False
    ctx.config_service.get_active_project.return_value = _project_stub()
    EnvRmCommand(remove_all=True).execute(ctx)
    prompt_message = ctx.output.prompt.call_args.args[0]
    assert "container + volumes + image" in prompt_message


def test_env_rm_force_skips_prompt_and_calls_rm(make_context):
    """``--force`` bypasses the prompt entirely and proceeds with removal."""
    ctx, container = _ctx_with_container(make_context, force=True)
    EnvRmCommand(remove_volumes=True).execute(ctx)
    ctx.output.prompt.assert_not_called()
    container.rm.assert_called_once_with(remove_volumes=True, remove_image=False, remove_all=False)


def test_container_commands_missing_container_service_raises(make_context):
    ctx = make_context()  # no container_service
    with pytest.raises(ContainerError, match="CLI bug"):
        EnvStartCommand().execute(ctx)


# ----- EnvListCommand -----


def test_env_list_empty(make_context):
    ctx = make_context()
    ctx.config_service.list_projects.return_value = {}
    EnvListCommand().execute(ctx)
    messages = [c.args[0] for c in ctx.output.info.call_args_list]
    assert any("No projects registered" in m for m in messages)


def test_env_list_renders_projects_with_active_marker(make_context):
    ctx = make_context()
    projects = {"alpha": _project_stub("alpha"), "beta": _project_stub("beta")}
    ctx.config_service.list_projects.return_value = projects
    ctx.config_service.load.return_value.active_project = "beta"
    EnvListCommand().execute(ctx)
    lines = [c.args[0] for c in ctx.output.info.call_args_list]
    active_line = next(line for line in lines if "beta" in line)
    inactive_line = next(line for line in lines if "alpha" in line)
    assert active_line.startswith("*")
    assert inactive_line.startswith(" ")


# ----- Command-class metadata contract -----


@pytest.mark.parametrize(
    ("cls", "expected_name", "expected_requires_container"),
    [
        (EnvStartCommand, "env start", True),
        (EnvStopCommand, "env stop", True),
        (EnvRestartCommand, "env restart", True),
        (EnvLoginCommand, "env login", True),
        (EnvStatusCommand, "env status", True),
        (EnvBuildCommand, "env build", True),
        (EnvPushCommand, "env push", True),
        (EnvPullCommand, "env pull", True),
        (EnvRefreshCommand, "env refresh", True),
        (EnvRmCommand, "env rm", True),
        (EnvListCommand, "env list", False),
    ],
)
def test_phase2_env_commands_declare_metadata(cls, expected_name, expected_requires_container):
    assert cls.name == expected_name
    assert cls.help
    assert cls.requires_container is expected_requires_container
    if expected_requires_container:
        assert cls.requires_project is True
