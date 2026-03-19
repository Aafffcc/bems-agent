import os
from types import SimpleNamespace

from bems_agent.agent.service import (
    AgentRuntime,
    ConversationResult,
    build_graph_config,
    extract_message_trace_events,
    extract_text_response,
)


def test_extract_text_response_from_message_blocks() -> None:
    message = {
        "content": [
            {"type": "text", "text": "first line"},
            {"type": "text", "text": "second line"},
        ]
    }

    assert extract_text_response(message) == "first line\nsecond line"


def test_build_graph_config_contains_thread_metadata() -> None:
    config = build_graph_config("thread-123")

    assert config["configurable"]["thread_id"] == "thread-123"
    assert config["metadata"]["assistant_id"] == "bems-agent"
    assert config["metadata"]["agent_name"] == "bems-agent"
    assert "updated_at" in config["metadata"]


def test_conversation_result_exposes_session_alias() -> None:
    result = ConversationResult(
        thread_id="thread-123",
        response="ok",
        created_session=False,
        model="openai:gpt-5.4",
        mcp_enabled=True,
        tool_count=0,
        tool_names=[],
        skills_paths=[],
        memory_paths=[],
    )

    assert result.session_id == "thread-123"

def test_agent_runtime_applies_anthropic_provider_environment(monkeypatch) -> None:
    runtime = AgentRuntime()
    runtime._settings.anthropic_api_key = None
    runtime._settings.anthropic_auth_token = "bridge-token"
    runtime._settings.anthropic_base_url = "https://api.jiekou.ai/anthropic"

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    runtime._apply_provider_environment()

    assert os.environ["ANTHROPIC_API_KEY"] == "bridge-token"
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://api.jiekou.ai/anthropic"


def test_extract_message_trace_events_builds_tool_call_and_detects_text() -> None:
    seen_tool_messages: set[str] = set()
    displayed_tool_calls: set[str] = set()
    tool_call_buffers: dict[str | int, dict] = {}
    message = SimpleNamespace(
        content_blocks=[
            {
                "type": "tool_call",
                "name": "list_buildings",
                "args": {"date": "2026-03-19"},
                "id": "call-1",
                "index": 0,
            },
            {
                "type": "text",
                "text": "Let me check that for you.",
            },
        ]
    )

    events, has_text = extract_message_trace_events(
        namespace=(),
        message=message,
        displayed_tool_calls=displayed_tool_calls,
        seen_tool_messages=seen_tool_messages,
        tool_call_buffers=tool_call_buffers,
    )

    assert has_text is True
    assert len(events) == 1
    assert events[0].kind == "tool_call"
    assert events[0].tool_name == "list_buildings"
