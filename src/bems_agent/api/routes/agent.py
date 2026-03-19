from fastapi import APIRouter, HTTPException, status
from pydantic import AliasChoices, BaseModel, Field

from bems_agent.agent.exceptions import (
    AgentConfigurationError,
    MCPConfigurationError,
    SessionNotFoundError,
)
from bems_agent.agent.graph import run_agent

router = APIRouter()


class AgentRequest(BaseModel):
    user_input: str = Field(..., min_length=1, description="Natural language input for the agent.")
    thread_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("thread_id", "session_id"),
        description="Existing local thread ID.",
    )


class AgentResponse(BaseModel):
    thread_id: str
    session_id: str
    response: str


@router.post("/invoke", response_model=AgentResponse)
async def invoke_agent(payload: AgentRequest) -> AgentResponse:
    try:
        result = await run_agent(payload.user_input, session_id=payload.thread_id)
    except AgentConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except MCPConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return AgentResponse(
        thread_id=result.thread_id,
        session_id=result.session_id,
        response=result.response,
    )
