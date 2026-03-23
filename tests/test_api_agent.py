from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from bems_agent.agent.exceptions import SessionNotFoundError
from bems_agent.agent.service import ConversationResult, ConversationTraceEvent
from bems_agent.api.routes import agent as agent_route
from bems_agent.main import app


def _parse_sse_events(lines: list[str]) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name: str | None = None
    event_data: dict[str, Any] | None = None

    for line in lines:
        if not line:
            if event_name is not None and event_data is not None:
                events.append((event_name, event_data))
            event_name = None
            event_data = None
            continue
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
            continue
        if line.startswith("data: "):
            event_data = json.loads(line.removeprefix("data: "))

    if event_name is not None and event_data is not None:
        events.append((event_name, event_data))

    return events


@pytest.mark.asyncio
async def test_invoke_agent_returns_response_and_thread_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_send_message(
        user_input: str,
        *,
        session_id: str | None = None,
        create_new: bool = False,
        mcp_enabled: bool | None = None,
        model_override: str | None = None,
    ) -> ConversationResult:
        assert user_input == "hello"
        assert session_id == "thread-123"
        assert create_new is False
        assert mcp_enabled is None
        assert model_override is None
        return ConversationResult(
            thread_id="thread-123",
            response="world",
            created_session=False,
            model="openai:gpt-5.4",
            mcp_enabled=True,
            tool_count=1,
            tool_names=["query_device_logs"],
            skills_paths=["skills/project"],
            memory_paths=["AGENTS.md"],
        )

    monkeypatch.setattr(agent_route.conversation_service, "send_message", fake_send_message)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/invoke",
            json={"user_input": "hello", "session_id": "thread-123"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-123",
        "session_id": "thread-123",
        "created_session": False,
        "response": "world",
    }


@pytest.mark.asyncio
async def test_invoke_agent_rejects_blank_user_input() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/invoke",
            json={"user_input": "   "},
        )

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any(
        error["loc"][-1] == "user_input" and "must not be blank" in error["msg"]
        for error in errors
    )


@pytest.mark.asyncio
async def test_invoke_agent_rejects_unknown_fields() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/invoke",
            json={"user_input": "hello", "model": "openai:gpt-5.4"},
        )

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any(error["type"] == "extra_forbidden" for error in errors)


@pytest.mark.asyncio
async def test_invoke_agent_rejects_unsafe_thread_id() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/invoke",
            json={"user_input": "hello", "thread_id": "../../etc/passwd"},
        )

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any(error["loc"][-1] == "thread_id" for error in errors)


@pytest.mark.asyncio
async def test_invoke_agent_translates_session_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_send_message(
        user_input: str,
        *,
        session_id: str | None = None,
        create_new: bool = False,
        mcp_enabled: bool | None = None,
        model_override: str | None = None,
    ) -> ConversationResult:
        raise SessionNotFoundError("Session 'missing-thread' was not found.")

    monkeypatch.setattr(agent_route.conversation_service, "send_message", fake_send_message)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/invoke",
            json={"user_input": "hello", "thread_id": "missing-thread"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "detail": {
            "code": "session_not_found",
            "message": "Session 'missing-thread' was not found.",
        }
    }


@pytest.mark.asyncio
async def test_stream_agent_returns_safe_sse_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_stream_message(
        user_input: str,
        *,
        session_id: str | None = None,
        create_new: bool = False,
        mcp_enabled: bool | None = None,
        model_override: str | None = None,
    ) -> AsyncIterator[ConversationTraceEvent]:
        assert user_input == "hello"
        assert session_id == "thread-123"
        assert create_new is False
        assert mcp_enabled is None
        assert model_override is None
        yield ConversationTraceEvent(
            kind="status",
            title="Thinking",
            detail="model=openai:gpt-5.4,mcp=on",
        )
        yield ConversationTraceEvent(
            kind="tool_call",
            title="Tool call: list_buildings",
            detail="building_id=1",
            tool_name="list_buildings",
            tool_args={"building_id": "1"},
        )
        yield ConversationTraceEvent(
            kind="tool_result",
            title="Tool result: list_buildings",
            detail="1 building found",
            tool_name="list_buildings",
            tool_output="SECRET FULL TOOL OUTPUT",
        )
        yield ConversationTraceEvent(
            kind="final_response",
            title="Response ready",
            detail="thread-123",
            response="分析完成",
        )

    monkeypatch.setattr(agent_route.conversation_service, "stream_message", fake_stream_message)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/api/v1/agent/stream",
            json={"user_input": "hello", "session_id": "thread-123"},
        ) as response:
            lines = [line async for line in response.aiter_lines()]

    body = "\n".join(lines)
    events = _parse_sse_events(lines)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert "SECRET FULL TOOL OUTPUT" not in body
    assert events == [
        (
            "status",
            {
                "title": "Thinking",
                "detail": "model=openai:gpt-5.4,mcp=on",
            },
        ),
        (
            "tool_call",
            {
                "title": "Tool call: list_buildings",
                "detail": "building_id=1",
                "tool_name": "list_buildings",
                "tool_args": {"building_id": "1"},
            },
        ),
        (
            "tool_result",
            {
                "title": "Tool result: list_buildings",
                "detail": "1 building found",
                "tool_name": "list_buildings",
            },
        ),
        (
            "final_response",
            {
                "thread_id": "thread-123",
                "session_id": "thread-123",
                "response": "分析完成",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_stream_agent_returns_error_event_for_missing_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_stream_message(
        user_input: str,
        *,
        session_id: str | None = None,
        create_new: bool = False,
        mcp_enabled: bool | None = None,
        model_override: str | None = None,
    ) -> AsyncIterator[ConversationTraceEvent]:
        raise SessionNotFoundError("Session 'missing-thread' was not found.")
        yield

    monkeypatch.setattr(agent_route.conversation_service, "stream_message", fake_stream_message)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/api/v1/agent/stream",
            json={"user_input": "hello", "thread_id": "missing-thread"},
        ) as response:
            lines = [line async for line in response.aiter_lines()]

    assert response.status_code == 200
    assert _parse_sse_events(lines) == [
        (
            "error",
            {
                "code": "session_not_found",
                "message": "Session 'missing-thread' was not found.",
            },
        )
    ]
