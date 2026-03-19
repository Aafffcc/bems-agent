from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from bems_agent import cli
from bems_agent.agent.mcp import MCPServerSummary
from bems_agent.agent.service import ConversationTraceEvent, MCPToolMetadata
from bems_agent.cli import ChatRuntimeState, SkillSummary


def test_main_defaults_to_chat_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_chat(args: argparse.Namespace) -> int:
        captured["command"] = args.command
        captured["message"] = args.message
        captured["no_mcp"] = args.no_mcp
        return 0

    async def fake_shutdown() -> None:
        captured["shutdown_called"] = True

    monkeypatch.setattr(cli, "run_chat", fake_run_chat)
    monkeypatch.setattr(cli.agent_runtime, "shutdown", fake_shutdown)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--message", "hello", "--no-mcp"])

    assert exc_info.value.code == 0
    assert captured == {
        "command": "chat",
        "message": "hello",
        "no_mcp": True,
        "shutdown_called": True,
    }


def test_main_routes_serve_command(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_serve(args: argparse.Namespace) -> int:
        captured["command"] = args.command
        captured["host"] = args.host
        captured["port"] = args.port
        captured["reload"] = args.reload
        return 0

    monkeypatch.setattr(cli, "run_serve", fake_run_serve)

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["serve", "--host", "127.0.0.1", "--port", "9000", "--reload"])

    assert exc_info.value.code == 0
    assert captured == {
        "command": "serve",
        "host": "127.0.0.1",
        "port": 9000,
        "reload": True,
    }


@pytest.mark.asyncio
async def test_handle_slash_command_toggles_trace() -> None:
    state = ChatRuntimeState(
        thread_id="thread-1",
        model="openai:gpt-5.4",
        mcp_enabled=True,
        tool_count=1,
        mcp_tool_names=["Energy-precise-data-query__calculate_metrics_tool"],
        skills_paths=["skills/project"],
        memory_paths=["AGENTS.md"],
        mcp_tools=[],
        trace_enabled=True,
    )

    updated, should_continue = await cli.handle_slash_command("/trace off", state)

    assert should_continue is True
    assert updated.trace_enabled is False


@pytest.mark.asyncio
async def test_handle_slash_command_switches_model(monkeypatch: pytest.MonkeyPatch) -> None:
    state = ChatRuntimeState(
        thread_id="thread-1",
        model="openai:gpt-5.4",
        mcp_enabled=True,
        tool_count=1,
        mcp_tool_names=["Energy-precise-data-query__calculate_metrics_tool"],
        skills_paths=["skills/project"],
        memory_paths=["AGENTS.md"],
        mcp_tools=[],
        trace_enabled=True,
    )

    async def fake_refresh_runtime_state(
        current_state: ChatRuntimeState,
        *,
        mcp_enabled: bool | None = None,
        model_override: str | None = None,
    ) -> ChatRuntimeState:
        assert current_state.thread_id == "thread-1"
        assert mcp_enabled is None
        assert model_override == "openai:gpt-5.5"
        return ChatRuntimeState(
            thread_id=current_state.thread_id,
            model="openai:gpt-5.5",
            mcp_enabled=current_state.mcp_enabled,
            tool_count=current_state.tool_count,
            mcp_tool_names=current_state.mcp_tool_names,
            skills_paths=current_state.skills_paths,
            memory_paths=current_state.memory_paths,
            mcp_tools=current_state.mcp_tools,
            trace_enabled=current_state.trace_enabled,
        )

    monkeypatch.setattr(cli, "refresh_runtime_state", fake_refresh_runtime_state)

    updated, should_continue = await cli.handle_slash_command("/model openai:gpt-5.5", state)

    assert should_continue is True
    assert updated.model == "openai:gpt-5.5"


@pytest.mark.asyncio
async def test_add_file_completion_root_requests_permission_for_external_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = ChatRuntimeState(
        thread_id="thread-1",
        model="openai:gpt-5.4",
        mcp_enabled=True,
        tool_count=1,
        mcp_tool_names=[],
        skills_paths=["skills/project"],
        memory_paths=["AGENTS.md"],
        mcp_tools=[],
    )

    external_root = tmp_path / "Downloads"
    external_root.mkdir()

    async def fake_request_directory_access(_root: Path) -> bool:
        return True

    monkeypatch.setattr(cli, "request_directory_access", fake_request_directory_access)

    added = await cli.add_file_completion_root(state, external_root, interactive=True)

    assert added is True
    assert external_root.as_posix() in state.file_completion_roots


def test_discover_skills_includes_scope_and_metadata(tmp_path: Path) -> None:
    project_source = tmp_path / "project"
    global_source = tmp_path / "global"
    custom_source = tmp_path / "custom"
    (project_source / "project-skill").mkdir(parents=True)
    (global_source / "global-skill").mkdir(parents=True)
    (custom_source / "custom-skill").mkdir(parents=True)
    (project_source / "project-skill" / "SKILL.md").write_text(
        "---\nname: project-skill\ndescription: project description\n---\nbody\n",
        encoding="utf-8",
    )
    (global_source / "global-skill" / "SKILL.md").write_text(
        "---\nname: global-skill\ndescription: global description\n---\nbody\n",
        encoding="utf-8",
    )
    (custom_source / "custom-skill" / "SKILL.md").write_text(
        "---\nname: custom-skill\ndescription: custom description\n---\nbody\n",
        encoding="utf-8",
    )

    original_loader = cli.load_cli_skills
    original_resolver = cli.resolve_skill_source_path
    original_classifier = cli.classify_skill_scope
    cli.load_cli_skills = lambda **_: [
        {
            "name": "project-skill",
            "description": "project description",
            "source": "project",
            "path": "/project-skill/SKILL.md",
            "metadata": {},
            "license": None,
            "compatibility": None,
            "allowed_tools": [],
        },
        {
            "name": "global-skill",
            "description": "global description",
            "source": "user",
            "path": "/global-skill/SKILL.md",
            "metadata": {},
            "license": None,
            "compatibility": None,
            "allowed_tools": [],
        },
    ]
    mapping = {
        project_source.as_posix(): project_source,
        global_source.as_posix(): global_source,
        custom_source.as_posix(): custom_source,
    }
    cli.resolve_skill_source_path = lambda raw: mapping.get(raw, Path(raw))
    cli.classify_skill_scope = (
        lambda source_path: "project"
        if source_path == project_source
        else "global"
        if source_path == global_source
        else "custom"
    )
    try:
        skills = cli.discover_skills(
            [project_source.as_posix(), global_source.as_posix(), custom_source.as_posix()]
        )
    finally:
        cli.load_cli_skills = original_loader
        cli.resolve_skill_source_path = original_resolver
        cli.classify_skill_scope = original_classifier

    assert [(skill.scope, skill.name) for skill in skills] == [
        ("custom", "custom-skill"),
        ("global", "global-skill"),
        ("project", "project-skill"),
    ]


def test_match_slash_commands_returns_ranked_matches() -> None:
    matches = cli.match_slash_commands("/ski")

    assert matches
    assert matches[0].command == "/skills"


def test_match_file_mentions_returns_project_file_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "list_completion_files",
        lambda _root: ["README.md", "src/streamable_http.py", "tests/test_cli.py"],
    )
    monkeypatch.setattr(
        cli,
        "fuzzy_search_files",
        lambda search, files, limit, include_dotfiles: [
            file for file in files if search in file
        ][:limit],
    )
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: type("SettingsStub", (), {"project_root": Path.cwd()})(),
    )

    matches = cli.match_file_mentions("@streamable", roots=[Path.cwd().as_posix()])

    assert len(matches) == 1
    assert matches[0].replacement == "@src/streamable_http.py"
    assert matches[0].meta == "py"


def test_build_message_input_embeds_referenced_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("energy baseline", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    message = cli.build_message_input("analyze @notes.txt")

    assert "analyze @notes.txt" in message
    assert "## Referenced Files" in message
    assert "notes.txt" in message
    assert "energy baseline" in message


def test_print_skills_list_only_shows_name_and_description(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = ChatRuntimeState(
        thread_id="thread-1",
        model="openai:gpt-5.4",
        mcp_enabled=True,
        tool_count=1,
        mcp_tool_names=["list_device"],
        skills_paths=["skills/project"],
        memory_paths=["AGENTS.md"],
        mcp_tools=[],
        trace_enabled=True,
    )

    monkeypatch.setattr(
        cli,
        "discover_skills",
        lambda _paths: [
            SkillSummary(
                name="energy-analysis-baseline",
                description="Use for building energy analysis.",
                scope="project",
                source_path=Path("/tmp/project"),
                skill_path=Path("/tmp/project/energy-analysis-baseline/SKILL.md"),
            )
        ],
    )
    monkeypatch.setattr(cli, "terminal_width", lambda default=100: 80)

    cli.print_skills_list(state)

    output = capsys.readouterr().out
    assert "energy-analysis-baseline" in output
    assert "Use for building energy analysis." in output
    assert "/tmp/project" not in output
    assert "source=" not in output
    assert "file=" not in output


def test_print_mcp_list_shows_deployment_and_params(capsys: pytest.CaptureFixture[str]) -> None:
    state = ChatRuntimeState(
        thread_id="thread-1",
        model="openai:gpt-5.4",
        mcp_enabled=True,
        tool_count=2,
        mcp_tool_names=[
            "calculate_cop",
            "query_device_logs",
        ],
        skills_paths=["skills/project"],
        memory_paths=["AGENTS.md"],
        mcp_tools=[
            MCPToolMetadata(
                name="calculate_cop",
                server_name="Energy-precise-data-query",
                original_name="calculate_metrics_tool",
            ),
            MCPToolMetadata(
                name="query_device_logs",
                server_name="Energy-precise-data-query",
                original_name="query_device_logs_tool",
            ),
        ],
        trace_enabled=True,
    )

    original_loader = cli.load_mcp_summaries
    cli.load_mcp_summaries = lambda: [
        MCPServerSummary(
            name="Energy-precise-data-query",
            deployment="cloud",
            transport="http",
            endpoint="http://47.111.9.219:9977/mcp",
            headers_count=1,
        ),
        MCPServerSummary(
            name="local-debug",
            deployment="local",
            transport="stdio",
            command="uv",
            args=["run", "energy-mcp"],
            cwd="/tmp/mcp",
            headers_count=0,
        ),
    ]
    try:
        cli.print_mcp_list(state)
    finally:
        cli.load_mcp_summaries = original_loader

    output = capsys.readouterr().out
    assert "[cloud] Energy-precise-data-query" in output
    assert "endpoint=http://47.111.9.219:9977/mcp" in output
    assert "[local] local-debug" in output
    assert "command=uv" in output
    assert "args=run energy-mcp" in output


def test_print_trace_event_card_formats_mcp_tool_output(capsys: pytest.CaptureFixture[str]) -> None:
    state = ChatRuntimeState(
        thread_id="thread-1",
        model="openai:gpt-5.4",
        mcp_enabled=True,
        tool_count=1,
        mcp_tool_names=["list_device"],
        skills_paths=["skills/project"],
        memory_paths=["AGENTS.md"],
        mcp_tools=[
            MCPToolMetadata(
                name="list_device",
                server_name="Energy-precise-data-query",
                original_name="list_devices_tool",
            )
        ],
        trace_enabled=True,
    )
    original_loader = cli.load_mcp_summaries
    cli.load_mcp_summaries = lambda: [
        MCPServerSummary(
            name="Energy-precise-data-query",
            deployment="cloud",
            transport="http",
            endpoint="http://47.111.9.219:9977/mcp",
            headers_count=1,
        )
    ]
    try:
        cli.print_trace_event_card(
            ConversationTraceEvent(
                kind="tool_call",
                title="Tool call: list_device",
                tool_name="list_device",
                tool_args={"building_id": "BUILD-003"},
            ),
            state,
        )
        cli.print_trace_event_card(
            ConversationTraceEvent(
                kind="tool_result",
                title="Tool result: list_device",
                tool_name="list_device",
                tool_output='{"device_id":"DEV-003-01","device_name":"大莲花主场馆总电表"}',
            ),
            state,
        )
    finally:
        cli.load_mcp_summaries = original_loader

    output = capsys.readouterr().out
    assert "MCP Tool" in output
    assert "server: Energy-precise-data-query" in output
    assert "list_device(building_id=BUILD-003)" in output
    assert "MCP Result" in output
    assert '"device_id": "DEV-003-01"' in output
