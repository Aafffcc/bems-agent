from __future__ import annotations

import importlib
import json
import os
import subprocess
import tempfile
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from deepagents.middleware import MemoryMiddleware, SkillsMiddleware
from deepagents.middleware.summarization import create_summarization_tool_middleware
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph

from bems_agent.agent.exceptions import AgentConfigurationError
from bems_agent.agent.mcp import (
    MCPServerSummary,
    MCPSessionManager,
    load_mcp_config,
    load_mcp_tools,
    summarize_mcp_servers,
)
from bems_agent.agent.prompts import SYSTEM_PROMPT
from bems_agent.agent.sessions import SessionStore, patch_aiosqlite
from bems_agent.core.config import get_settings
from bems_agent.core.deepagents_cli_compat import ensure_deepagents_cli_available

ensure_deepagents_cli_available()

AskUserMiddleware = importlib.import_module("deepagents_cli.ask_user").AskUserMiddleware
_configurable_model = importlib.import_module("deepagents_cli.configurable_model")
CLIContext = _configurable_model.CLIContext
ConfigurableModelMiddleware = _configurable_model.ConfigurableModelMiddleware
LocalContextMiddleware = importlib.import_module(
    "deepagents_cli.local_context"
).LocalContextMiddleware
_textual_adapter = importlib.import_module("deepagents_cli.textual_adapter")
_mcp_tools = importlib.import_module("deepagents_cli.mcp_tools")
MCPServerInfo = _mcp_tools.MCPServerInfo
MCPToolInfo = _mcp_tools.MCPToolInfo
is_summarization_chunk = _textual_adapter._is_summarization_chunk

ASSISTANT_ID = "bems-agent"


@dataclass(frozen=True, slots=True)
class RuntimeKey:
    mcp_enabled: bool
    model: str


@dataclass(slots=True)
class MCPToolMetadata:
    name: str
    server_name: str = ""
    original_name: str = ""


@dataclass(slots=True)
class RuntimeHandle:
    graph: CompiledStateGraph
    model: str
    mcp_enabled: bool
    tool_count: int
    tool_names: list[str]
    skills_paths: list[str]
    memory_paths: list[str]
    mcp_tools: list[MCPToolMetadata] = field(default_factory=list)
    mcp_manager: MCPSessionManager | None = None
    exit_stack: AsyncExitStack | None = None


@dataclass(slots=True)
class ConversationSessionContext:
    thread_id: str
    model: str
    mcp_enabled: bool
    tool_count: int
    tool_names: list[str]
    skills_paths: list[str]
    memory_paths: list[str]
    mcp_tools: list[MCPToolMetadata] = field(default_factory=list)

    @property
    def session_id(self) -> str:
        return self.thread_id


@dataclass(slots=True)
class ConversationResult:
    thread_id: str
    response: str
    created_session: bool
    model: str
    mcp_enabled: bool
    tool_count: int
    tool_names: list[str]
    skills_paths: list[str]
    memory_paths: list[str]
    mcp_tools: list[MCPToolMetadata] = field(default_factory=list)

    @property
    def session_id(self) -> str:
        return self.thread_id


@dataclass(slots=True)
class ConversationTraceEvent:
    kind: str
    title: str
    detail: str = ""
    response: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] | None = None
    tool_output: str = ""


class AgentRuntime:
    """Manage deepagents runtime assembly, MCP lifecycle, and checkpointers."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._handles: dict[RuntimeKey, RuntimeHandle] = {}

    def _apply_provider_environment(self) -> None:
        anthropic_api_key = self._settings.resolved_anthropic_api_key
        if anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = anthropic_api_key

        if self._settings.anthropic_base_url and not os.environ.get("ANTHROPIC_BASE_URL"):
            os.environ["ANTHROPIC_BASE_URL"] = self._settings.anthropic_base_url

    async def startup(
        self,
        *,
        mcp_enabled: bool | None = None,
        model_override: str | None = None,
    ) -> RuntimeHandle:
        self._apply_provider_environment()
        resolved_mcp_enabled = self._settings.mcp_enabled if mcp_enabled is None else mcp_enabled
        resolved_model = self._resolve_model(model_override)
        key = RuntimeKey(mcp_enabled=resolved_mcp_enabled, model=resolved_model)
        handle = self._handles.get(key)
        if handle is not None:
            return handle

        patch_aiosqlite()
        exit_stack = AsyncExitStack()

        tools = []
        mcp_manager: MCPSessionManager | None = None
        mcp_server_info: list[MCPServerInfo] = []
        if resolved_mcp_enabled:
            tools, mcp_manager = await load_mcp_tools(str(self._settings.resolved_mcp_config_path))
            mcp_server_info = _build_mcp_server_info(
                summarize_mcp_servers(load_mcp_config(str(self._settings.resolved_mcp_config_path))),
                tools,
            )

        checkpointer = await exit_stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(self._settings.bems_session_db_path))
        )

        shell_backend = LocalShellBackend(
            root_dir=self._settings.project_root,
            inherit_env=True,
            env=os.environ.copy(),
        )
        composite_backend = CompositeBackend(
            default=shell_backend,
            routes={
                "/large_tool_results/": FilesystemBackend(
                    root_dir=tempfile.mkdtemp(prefix="bems-agent-large-results-"),
                    virtual_mode=True,
                ),
                "/conversation_history/": FilesystemBackend(
                    root_dir=tempfile.mkdtemp(prefix="bems-agent-conversation-history-"),
                    virtual_mode=True,
                ),
            },
        )

        middleware = [
            ConfigurableModelMiddleware(),
            AskUserMiddleware(),
            MemoryMiddleware(
                backend=FilesystemBackend(),
                sources=self._settings.resolved_memory_paths,
            ),
            SkillsMiddleware(
                backend=FilesystemBackend(),
                sources=self._settings.resolved_skills_paths,
            ),
            LocalContextMiddleware(
                backend=shell_backend,
                mcp_server_info=mcp_server_info,
            ),
            create_summarization_tool_middleware(resolved_model, composite_backend),
        ]

        handle = RuntimeHandle(
            graph=create_deep_agent(
                model=resolved_model,
                tools=tools,
                system_prompt=SYSTEM_PROMPT,
                backend=composite_backend,
                middleware=middleware,
                checkpointer=checkpointer,
            ),
            model=resolved_model,
            mcp_enabled=resolved_mcp_enabled,
            tool_count=len(tools),
            tool_names=[tool.name for tool in tools],
            skills_paths=self._settings.resolved_skills_paths,
            memory_paths=self._settings.resolved_memory_paths,
            mcp_tools=[
                MCPToolMetadata(
                    name=tool.name,
                    server_name=str((tool.metadata or {}).get("mcp_server_name", "")),
                    original_name=str((tool.metadata or {}).get("mcp_original_name", "")),
                )
                for tool in tools
            ],
            mcp_manager=mcp_manager,
            exit_stack=exit_stack,
        )
        self._handles[key] = handle
        return handle

    async def shutdown(self) -> None:
        for handle in self._handles.values():
            if handle.mcp_manager is not None:
                await handle.mcp_manager.cleanup()
            if handle.exit_stack is not None:
                await handle.exit_stack.aclose()
        self._handles.clear()

    async def invoke(
        self,
        user_input: str,
        *,
        thread_id: str,
        mcp_enabled: bool | None = None,
        model_override: str | None = None,
    ) -> ConversationResult:
        handle = await self.startup(mcp_enabled=mcp_enabled, model_override=model_override)
        result = await handle.graph.ainvoke(
            {"messages": [HumanMessage(content=user_input)]},
            config=build_graph_config(thread_id),
            context=CLIContext(model=handle.model, model_params={}),
        )
        messages = result.get("messages", [])
        if not messages:
            msg = "Agent returned no messages."
            raise AgentConfigurationError(msg)
        return ConversationResult(
            thread_id=thread_id,
            response=extract_text_response(messages[-1]),
            created_session=False,
            model=handle.model,
            mcp_enabled=handle.mcp_enabled,
            tool_count=handle.tool_count,
            tool_names=handle.tool_names,
            mcp_tools=handle.mcp_tools,
            skills_paths=handle.skills_paths,
            memory_paths=handle.memory_paths,
        )

    async def stream(
        self,
        user_input: str,
        *,
        thread_id: str,
        mcp_enabled: bool | None = None,
        model_override: str | None = None,
    ) -> AsyncIterator[ConversationTraceEvent]:
        handle = await self.startup(mcp_enabled=mcp_enabled, model_override=model_override)
        seen_tool_messages: set[str] = set()
        displayed_tool_calls: set[str] = set()
        tool_call_buffers: dict[str | int, dict[str, Any]] = {}
        final_messages: list[BaseMessage] | None = None
        phase = "thinking"

        yield ConversationTraceEvent(
            kind="status",
            title="Thinking",
            detail=f"model={handle.model}, mcp={'on' if handle.mcp_enabled else 'off'}",
        )

        async for item in handle.graph.astream(
            {"messages": [HumanMessage(content=user_input)]},
            stream_mode=["messages", "values"],
            subgraphs=True,
            config=build_graph_config(thread_id),
            context=CLIContext(model=handle.model, model_params={}),
            durability="exit",
        ):
            namespace, mode, payload = item
            if mode == "messages" and isinstance(payload, tuple) and len(payload) == 2:
                message, metadata = payload
                if is_summarization_chunk(metadata):
                    if phase != "offloading":
                        phase = "offloading"
                        yield ConversationTraceEvent(
                            kind="status",
                            title="Offloading context",
                        )
                    continue

                if phase == "offloading":
                    phase = "thinking"
                    yield ConversationTraceEvent(
                        kind="status",
                        title="Thinking",
                    )

                events, has_text = extract_message_trace_events(
                    namespace=namespace,
                    message=message,
                    displayed_tool_calls=displayed_tool_calls,
                    seen_tool_messages=seen_tool_messages,
                    tool_call_buffers=tool_call_buffers,
                )
                for event in events:
                    yield event
                    if event.kind == "tool_result" and phase != "thinking":
                        phase = "thinking"
                        yield ConversationTraceEvent(
                            kind="status",
                            title="Thinking",
                        )

                if has_text and phase != "responding":
                    phase = "responding"
                    yield ConversationTraceEvent(
                        kind="status",
                        title="Composing response",
                    )
            elif mode == "values" and isinstance(payload, dict):
                final_messages = payload.get("messages")

        if not final_messages:
            msg = "Agent returned no messages."
            raise AgentConfigurationError(msg)

        yield ConversationTraceEvent(
            kind="final_response",
            title="Agent execution finished",
            response=extract_text_response(final_messages[-1]),
        )

    def _resolve_model(self, model_override: str | None = None) -> str:
        if model_override:
            return model_override
        if self._settings.resolved_agent_model:
            return self._settings.resolved_agent_model
        msg = "AGENT_MODEL or ANTHROPIC_MODEL is required before invoking the deep agent."
        raise AgentConfigurationError(msg)


class ConversationService:
    """Shared conversation entrypoint for CLI and HTTP API."""

    def __init__(
        self,
        runtime: AgentRuntime,
        session_store: SessionStore,
    ) -> None:
        self._runtime = runtime
        self._session_store = session_store

    async def open_session(
        self,
        *,
        session_id: str | None = None,
        create_new: bool = False,
        mcp_enabled: bool | None = None,
        model_override: str | None = None,
    ) -> ConversationSessionContext:
        handle = await self._runtime.startup(
            mcp_enabled=mcp_enabled,
            model_override=model_override,
        )

        created_thread = create_new or session_id is None
        if created_thread:
            thread_id = self._session_store.create_thread(session_id)
        else:
            assert session_id is not None
            self._session_store.ensure_thread_exists(session_id)
            thread_id = session_id

        return ConversationSessionContext(
            thread_id=thread_id,
            model=handle.model,
            mcp_enabled=handle.mcp_enabled,
            tool_count=handle.tool_count,
            tool_names=handle.tool_names,
            mcp_tools=handle.mcp_tools,
            skills_paths=handle.skills_paths,
            memory_paths=handle.memory_paths,
        )

    async def send_message(
        self,
        user_input: str,
        *,
        session_id: str | None = None,
        create_new: bool = False,
        mcp_enabled: bool | None = None,
        model_override: str | None = None,
    ) -> ConversationResult:
        context = await self.open_session(
            session_id=session_id,
            create_new=create_new,
            mcp_enabled=mcp_enabled,
            model_override=model_override,
        )
        invocation = await self._runtime.invoke(
            user_input,
            thread_id=context.thread_id,
            mcp_enabled=context.mcp_enabled,
            model_override=context.model,
        )
        self._session_store.mark_persisted(context.thread_id)
        return ConversationResult(
            thread_id=context.thread_id,
            response=invocation.response,
            created_session=create_new or session_id is None,
            model=context.model,
            mcp_enabled=context.mcp_enabled,
            tool_count=context.tool_count,
            tool_names=context.tool_names,
            mcp_tools=context.mcp_tools,
            skills_paths=context.skills_paths,
            memory_paths=context.memory_paths,
        )

    async def stream_message(
        self,
        user_input: str,
        *,
        session_id: str | None = None,
        create_new: bool = False,
        mcp_enabled: bool | None = None,
        model_override: str | None = None,
    ) -> AsyncIterator[ConversationTraceEvent]:
        context = await self.open_session(
            session_id=session_id,
            create_new=create_new,
            mcp_enabled=mcp_enabled,
            model_override=model_override,
        )
        final_response: str | None = None

        async for event in self._runtime.stream(
            user_input,
            thread_id=context.thread_id,
            mcp_enabled=context.mcp_enabled,
            model_override=context.model,
        ):
            if event.kind == "final_response":
                final_response = event.response
                continue
            yield event

        if final_response is None:
            msg = "Agent returned no messages."
            raise AgentConfigurationError(msg)

        self._session_store.mark_persisted(context.thread_id)
        yield ConversationTraceEvent(
            kind="final_response",
            title="Response ready",
            detail=context.thread_id,
            response=final_response,
        )

    def list_sessions(self) -> list[dict[str, str | int]]:
        return [
            {
                "thread_id": session.thread_id,
                "session_id": session.session_id,
                "updated_at": session.updated_at,
                "turn_count": session.turn_count,
            }
            for session in self._session_store.list_sessions()
        ]


def build_graph_config(thread_id: str) -> dict[str, Any]:
    metadata: dict[str, str] = {
        "assistant_id": ASSISTANT_ID,
        "agent_name": ASSISTANT_ID,
        "updated_at": datetime.now(UTC).isoformat(),
        "cwd": str(get_settings().project_root),
    }
    branch = _get_git_branch()
    if branch:
        metadata["git_branch"] = branch
    return {
        "configurable": {"thread_id": thread_id},
        "metadata": metadata,
    }


def extract_text_response(message: BaseMessage | dict[str, Any] | Any) -> str:
    """Extract a string response from a LangChain message payload."""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [_extract_content_part(part) for part in content]
        text = "\n".join(part for part in parts if part)
        if text:
            return text
    if isinstance(message, dict):
        dict_content = message.get("content")
        if isinstance(dict_content, str):
            return dict_content
        if isinstance(dict_content, list):
            parts = [_extract_content_part(part) for part in dict_content]
            text = "\n".join(part for part in parts if part)
            if text:
                return text
    return str(message)


def _extract_content_part(part: Any) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict) and part.get("type") == "text":
        text = part.get("text")
        if isinstance(text, str):
            return text
    return ""


def extract_message_trace_events(
    *,
    namespace: tuple[Any, ...],
    message: BaseMessage | Any,
    displayed_tool_calls: set[str],
    seen_tool_messages: set[str],
    tool_call_buffers: dict[str | int, dict[str, Any]],
) -> tuple[list[ConversationTraceEvent], bool]:
    events: list[ConversationTraceEvent] = []
    namespace_prefix = format_namespace(namespace)
    has_text = False

    if isinstance(message, ToolMessage):
        if message.id and message.id in seen_tool_messages:
            return events, has_text
        if message.id:
            seen_tool_messages.add(message.id)
        events.append(
            ConversationTraceEvent(
                kind="tool_result",
                title=f"{namespace_prefix}Tool result: {message.name or 'unknown'}",
                detail=truncate_text(extract_text_response(message), limit=240),
                tool_name=message.name or "unknown",
                tool_output=extract_text_response(message),
            )
        )
        return events, has_text

    content_blocks = getattr(message, "content_blocks", [])
    for block in content_blocks:
        block_type = block.get("type")
        if block_type == "text" and block.get("text"):
            has_text = True
            continue

        if block_type not in {"tool_call_chunk", "tool_call"}:
            continue

        chunk_name = block.get("name")
        chunk_args = block.get("args")
        chunk_id = block.get("id")
        chunk_index = block.get("index")

        buffer_key: str | int
        if chunk_index is not None:
            buffer_key = chunk_index
        elif chunk_id is not None:
            buffer_key = chunk_id
        else:
            buffer_key = f"unknown-{len(tool_call_buffers)}"

        buffer = tool_call_buffers.setdefault(
            buffer_key,
            {"name": None, "id": None, "args": None, "args_parts": []},
        )

        if chunk_name:
            buffer["name"] = chunk_name
        if chunk_id:
            buffer["id"] = chunk_id

        if isinstance(chunk_args, dict):
            buffer["args"] = chunk_args
            buffer["args_parts"] = []
        elif isinstance(chunk_args, str):
            if chunk_args:
                parts: list[str] = buffer.setdefault("args_parts", [])
                if not parts or chunk_args != parts[-1]:
                    parts.append(chunk_args)
                buffer["args"] = "".join(parts)
        elif chunk_args is not None:
            buffer["args"] = chunk_args

        buffer_name = buffer.get("name")
        buffer_id = buffer.get("id")
        if buffer_name is None:
            continue

        parsed_args = buffer.get("args")
        if isinstance(parsed_args, str):
            if not parsed_args:
                continue
            try:
                parsed_args = json.loads(parsed_args)
            except json.JSONDecodeError:
                continue
        elif parsed_args is None:
            continue

        if not isinstance(parsed_args, dict):
            parsed_args = {"value": parsed_args}

        if buffer_id is not None and buffer_id not in displayed_tool_calls:
            displayed_tool_calls.add(buffer_id)
            events.append(
                ConversationTraceEvent(
                    kind="tool_call",
                    title=f"{namespace_prefix}Tool call: {buffer_name}",
                    detail=format_tool_args(parsed_args),
                    tool_name=str(buffer_name),
                    tool_args=parsed_args,
                )
            )
            tool_call_buffers.pop(buffer_key, None)

    return events, has_text


def format_namespace(namespace: tuple[Any, ...]) -> str:
    parts = [str(part) for part in namespace if str(part)]
    if not parts:
        return ""
    return f"[{' / '.join(parts)}] "


def format_tool_args(args: Any) -> str:
    if not isinstance(args, dict) or not args:
        return "no arguments"
    pairs = [f"{key}={value}" for key, value in args.items()]
    return truncate_text(", ".join(pairs), limit=240)


def truncate_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _build_mcp_server_info(
    summaries: list[MCPServerSummary],
    tools: list[Any],
) -> list[MCPServerInfo]:
    tools_by_server: dict[str, list[MCPToolInfo]] = {}
    for tool in tools:
        metadata = getattr(tool, "metadata", {}) or {}
        server_name = str(metadata.get("mcp_server_name", ""))
        if not server_name:
            continue
        tools_by_server.setdefault(server_name, []).append(
            MCPToolInfo(name=str(tool.name), description=str(getattr(tool, "description", "")))
        )

    return [
        MCPServerInfo(
            name=summary.name,
            transport=summary.transport,
            tools=tools_by_server.get(summary.name, []),
        )
        for summary in summaries
    ]


def _get_git_branch() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            cwd=get_settings().project_root,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    branch = completed.stdout.strip()
    return branch or None


agent_runtime = AgentRuntime()
conversation_service = ConversationService(
    runtime=agent_runtime,
    session_store=SessionStore(get_settings().bems_session_db_path),
)
