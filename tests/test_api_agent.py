from httpx import ASGITransport, AsyncClient

from bems_agent.agent.service import ConversationResult
from bems_agent.main import app


async def test_invoke_agent_returns_response_and_thread_ids(monkeypatch) -> None:
    async def fake_run_agent(
        user_input: str,
        *,
        session_id: str | None = None,
        create_new: bool = False,
        mcp_enabled: bool | None = None,
    ) -> ConversationResult:
        assert user_input == "hello"
        assert session_id == "thread-123"
        assert create_new is False
        assert mcp_enabled is None
        return ConversationResult(
            thread_id="thread-123",
            response="world",
            created_session=False,
            model="openai:gpt-5.4",
            mcp_enabled=True,
            tool_count=1,
            tool_names=["Energy-precise-data-query__calculate_metrics_tool"],
            skills_paths=["skills/project"],
            memory_paths=["AGENTS.md"],
        )

    monkeypatch.setattr("bems_agent.api.routes.agent.run_agent", fake_run_agent)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/invoke",
            json={"user_input": "hello", "session_id": "thread-123"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-123",
        "session_id": "thread-123",
        "response": "world",
    }
