from bems_agent.agent.service import ConversationResult, conversation_service


async def run_agent(
    user_input: str,
    *,
    session_id: str | None = None,
    create_new: bool = False,
    mcp_enabled: bool | None = None,
) -> ConversationResult:
    """Invoke the configured deep agent conversation service."""
    return await conversation_service.send_message(
        user_input,
        session_id=session_id,
        create_new=create_new,
        mcp_enabled=mcp_enabled,
    )
