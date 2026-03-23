import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from bems_agent.agent.exceptions import (
    AgentConfigurationError,
    MCPConfigurationError,
    SessionNotFoundError,
)
from bems_agent.agent.service import (
    ConversationResult,
    ConversationTraceEvent,
    conversation_service,
)

router = APIRouter()

THREAD_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
MAX_USER_INPUT_LENGTH = 20_000


class ErrorDetail(BaseModel):
    code: str
    message: str


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    user_input: str = Field(
        ...,
        min_length=1,
        max_length=MAX_USER_INPUT_LENGTH,
        description="Natural language input for the agent.",
    )
    thread_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=THREAD_ID_PATTERN,
        validation_alias=AliasChoices("thread_id", "session_id"),
        description="Existing local thread ID.",
    )

    @field_validator("user_input", mode="before")
    @classmethod
    def validate_user_input(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            msg = "user_input must not be blank."
            raise ValueError(msg)
        return normalized

    @field_validator("thread_id", mode="before")
    @classmethod
    def validate_thread_id(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip()
        return value


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    session_id: str
    created_session: bool
    response: str


@router.post(
    "/invoke",
    response_model=AgentResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorDetail},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorDetail},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorDetail},
    },
)
async def invoke_agent(payload: AgentRequest) -> AgentResponse:
    try:
        result = await conversation_service.send_message(
            payload.user_input,
            session_id=payload.thread_id,
        )
    except (AgentConfigurationError, MCPConfigurationError, SessionNotFoundError) as exc:
        raise _translate_agent_http_error(exc) from exc

    return _build_agent_response(result)


@router.post(
    "/stream",
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {
            "content": {
                "text/event-stream": {
                    "example": (
                        "event: status\n"
                        'data: {"title":"Thinking","detail":"model=openai:gpt-5.4,mcp=on"}\n\n'
                        "event: final_response\n"
                        'data: {"thread_id":"thread-123","session_id":"thread-123",'
                        '"response":"分析完成"}\n\n'
                    )
                }
            },
        }
    },
)
async def stream_agent(payload: AgentRequest) -> StreamingResponse:
    return StreamingResponse(
        _stream_agent_events(payload),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _build_agent_response(result: ConversationResult) -> AgentResponse:
    return AgentResponse(
        thread_id=result.thread_id,
        session_id=result.session_id,
        created_session=result.created_session,
        response=result.response,
    )


def _translate_agent_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AgentConfigurationError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_build_error_detail("agent_configuration_error", str(exc)),
        )
    if isinstance(exc, MCPConfigurationError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_build_error_detail("mcp_configuration_error", str(exc)),
        )
    if isinstance(exc, SessionNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_build_error_detail("session_not_found", str(exc)),
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=_build_error_detail("internal_server_error", "Unexpected agent error."),
    )


def _build_error_detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


async def _stream_agent_events(payload: AgentRequest) -> AsyncIterator[bytes]:
    try:
        async for event in conversation_service.stream_message(
            payload.user_input,
            session_id=payload.thread_id,
        ):
            yield _encode_sse(event.kind, _serialize_trace_event(event))
    except (AgentConfigurationError, MCPConfigurationError, SessionNotFoundError) as exc:
        error = _translate_agent_http_error(exc)
        detail = error.detail
        if isinstance(detail, dict):
            yield _encode_sse("error", detail)
            return
        yield _encode_sse(
            "error",
            _build_error_detail("internal_server_error", "Unexpected agent error."),
        )
    except Exception:
        yield _encode_sse(
            "error",
            _build_error_detail("internal_server_error", "Unexpected agent error."),
        )


def _serialize_trace_event(event: ConversationTraceEvent) -> dict[str, Any]:
    if event.kind == "final_response":
        thread_id = event.detail
        return {
            "thread_id": thread_id,
            "session_id": thread_id,
            "response": event.response,
        }

    payload: dict[str, Any] = {
        "title": event.title,
        "detail": event.detail,
    }
    if event.kind == "tool_call":
        payload["tool_name"] = event.tool_name
        payload["tool_args"] = event.tool_args or {}
    elif event.kind == "tool_result":
        payload["tool_name"] = event.tool_name
    return payload


def _encode_sse(event_name: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {payload}\n\n".encode("utf-8")
