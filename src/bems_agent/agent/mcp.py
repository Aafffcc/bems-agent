from __future__ import annotations

import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

from bems_agent.agent.exceptions import MCPConfigurationError


@dataclass
class MCPSessionManager:
    """Hold open MCP sessions for the lifetime of the FastAPI process."""

    exit_stack: AsyncExitStack
    _cleaned: bool = False

    async def cleanup(self) -> None:
        """Close all open MCP sessions."""
        if self._cleaned:
            return
        try:
            await self.exit_stack.aclose()
        except RuntimeError as exc:
            if not _is_ignorable_mcp_cleanup_error(exc):
                raise
        finally:
            self._cleaned = True


@dataclass(frozen=True, slots=True)
class MCPServerSummary:
    name: str
    deployment: str
    transport: str
    endpoint: str | None = None
    command: str | None = None
    args: list[str] | None = None
    cwd: str | None = None
    headers_count: int = 0
    tool_names: list[str] = field(default_factory=list)


MCP_TOOL_RENAME_MAP: dict[str, dict[str, str]] = {
    "Energy-precise-data-query": {
        "query_device_logs_tool": "query_device_logs",
        "calculate_metrics_tool": "calculate_cop",
        "get_device_status_tool": "get_device_status",
        "list_buildings_tool": "list_buildings",
        "list_devices_tool": "list_device",
        "import_dataset_tool": "import_dataset",
    }
}


def _is_ignorable_mcp_cleanup_error(exc: RuntimeError) -> bool:
    message = str(exc)
    return (
        "aclose(): asynchronous generator is already running" in message
        or "Event loop is closed" in message
    )


def load_mcp_config(config_path: str) -> dict[str, Any]:
    """Load MCP configuration from disk.

    Args:
        config_path: Absolute or relative path to the MCP JSON file.

    Returns:
        Raw MCP configuration.

    Raises:
        MCPConfigurationError: If the file cannot be read or is invalid.
    """
    path = Path(config_path)
    if not path.exists():
        msg = f"MCP config file not found: {config_path}"
        raise MCPConfigurationError(msg)

    try:
        with path.open(encoding="utf-8") as file:
            config = json.load(file)
    except json.JSONDecodeError as exc:
        msg = f"Invalid JSON in MCP config file: {exc.msg}"
        raise MCPConfigurationError(msg) from exc

    if not isinstance(config, dict) or "mcpServers" not in config:
        msg = "MCP config must contain an 'mcpServers' object."
        raise MCPConfigurationError(msg)

    if not isinstance(config["mcpServers"], dict) or not config["mcpServers"]:
        msg = "'mcpServers' must be a non-empty object."
        raise MCPConfigurationError(msg)

    return config


def normalize_mcp_config(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize app MCP config into the adapter-friendly format.

    Supports both Claude Desktop style configs and the user's provided
    `serverUrl + transport=streamable-http` format.

    Args:
        config: Raw MCP config object.

    Returns:
        Normalized MCP config.

    Raises:
        MCPConfigurationError: If a server definition is invalid.
    """
    normalized_servers: dict[str, dict[str, Any]] = {}
    for server_name, raw_server in config["mcpServers"].items():
        if not isinstance(raw_server, dict):
            msg = f"Server '{server_name}' config must be an object."
            raise MCPConfigurationError(msg)
        normalized_servers[server_name] = _normalize_server(server_name, raw_server)
    return {"mcpServers": normalized_servers}


def summarize_mcp_servers(config: dict[str, Any]) -> list[MCPServerSummary]:
    """Build display-friendly MCP server summaries from config."""
    normalized = normalize_mcp_config(config)
    summaries: list[MCPServerSummary] = []
    for server_name, server_config in normalized["mcpServers"].items():
        if server_config.get("transport") == "stdio":
            summaries.append(
                MCPServerSummary(
                    name=server_name,
                    deployment="local",
                    transport="stdio",
                    command=server_config["command"],
                    args=[str(item) for item in server_config.get("args", [])],
                    cwd=server_config.get("cwd"),
                    headers_count=0,
                    tool_names=sorted(MCP_TOOL_RENAME_MAP.get(server_name, {}).values()),
                )
            )
            continue

        transport_type = str(server_config.get("type", "unknown"))
        summaries.append(
            MCPServerSummary(
                name=server_name,
                deployment="cloud",
                transport=transport_type,
                endpoint=server_config.get("url"),
                headers_count=len(server_config.get("headers", {})),
                tool_names=sorted(MCP_TOOL_RENAME_MAP.get(server_name, {}).values()),
            )
        )
    return summaries


def rename_mcp_tool(server_name: str, tool: BaseTool) -> BaseTool:
    original_name = tool.name
    canonical_name = original_name
    if canonical_name.startswith(f"{server_name}__"):
        canonical_name = canonical_name.removeprefix(f"{server_name}__")
    elif canonical_name.startswith(f"{server_name}_"):
        canonical_name = canonical_name.removeprefix(f"{server_name}_")

    renamed = MCP_TOOL_RENAME_MAP.get(server_name, {}).get(canonical_name, canonical_name)
    tool.name = renamed
    metadata = dict(getattr(tool, "metadata", {}) or {})
    metadata["mcp_server_name"] = server_name
    metadata["mcp_original_name"] = original_name
    metadata["mcp_canonical_name"] = canonical_name
    tool.metadata = metadata
    return tool


def _normalize_server(server_name: str, server_config: dict[str, Any]) -> dict[str, Any]:
    if "command" in server_config:
        return _normalize_stdio_server(server_name, server_config)

    transport = str(server_config.get("transport") or server_config.get("type") or "").lower()
    server_url = server_config.get("serverUrl") or server_config.get("url")
    headers = server_config.get("headers")

    if headers is not None and not isinstance(headers, dict):
        msg = f"Server '{server_name}' headers must be an object."
        raise MCPConfigurationError(msg)

    if transport in {"streamable-http", "http"}:
        if not isinstance(server_url, str) or not server_url:
            msg = f"Server '{server_name}' must define 'serverUrl' or 'url' for HTTP transport."
            raise MCPConfigurationError(msg)
        normalized = {"type": "http", "url": server_url}
        if headers:
            normalized["headers"] = headers
        return normalized

    if transport == "sse":
        if not isinstance(server_url, str) or not server_url:
            msg = f"Server '{server_name}' must define 'serverUrl' or 'url' for SSE transport."
            raise MCPConfigurationError(msg)
        normalized = {"type": "sse", "url": server_url}
        if headers:
            normalized["headers"] = headers
        return normalized

    msg = (
        f"Server '{server_name}' has unsupported transport "
        f"'{server_config.get('transport') or server_config.get('type')}'."
    )
    raise MCPConfigurationError(msg)


def _normalize_stdio_server(server_name: str, server_config: dict[str, Any]) -> dict[str, Any]:
    command = server_config.get("command")
    args = server_config.get("args", [])
    cwd = server_config.get("cwd")
    env = server_config.get("env")

    if not isinstance(command, str) or not command:
        msg = f"Server '{server_name}' must define a non-empty 'command'."
        raise MCPConfigurationError(msg)
    if not isinstance(args, list):
        msg = f"Server '{server_name}' args must be a list."
        raise MCPConfigurationError(msg)
    if cwd is not None and (not isinstance(cwd, str) or not cwd):
        msg = f"Server '{server_name}' cwd must be a non-empty string."
        raise MCPConfigurationError(msg)
    if env is not None and not isinstance(env, dict):
        msg = f"Server '{server_name}' env must be an object."
        raise MCPConfigurationError(msg)

    normalized = {"command": command, "args": args, "transport": "stdio"}
    if cwd:
        normalized["cwd"] = cwd
    if env:
        normalized["env"] = env
    return normalized


async def load_mcp_tools(
    config_path: str,
) -> tuple[list[BaseTool], MCPSessionManager]:
    """Load MCP tools and keep sessions open for later agent calls.

    Args:
        config_path: Path to the MCP config file.

    Returns:
        Loaded tool list and an active session manager.

    Raises:
        MCPConfigurationError: If config parsing or session setup fails.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langchain_mcp_adapters.sessions import (
        SSEConnection,
        StdioConnection,
        StreamableHttpConnection,
    )
    from langchain_mcp_adapters.tools import load_mcp_tools as load_tools_from_session

    config = normalize_mcp_config(load_mcp_config(config_path))
    connections: dict[str, Any] = {}

    for server_name, server_config in config["mcpServers"].items():
        if server_config.get("type") == "http":
            connection = StreamableHttpConnection(
                transport="streamable_http",
                url=server_config["url"],
            )
            if "headers" in server_config:
                connection["headers"] = server_config["headers"]
            connections[server_name] = connection
            continue

        if server_config.get("type") == "sse":
            connection = SSEConnection(
                transport="sse",
                url=server_config["url"],
            )
            if "headers" in server_config:
                connection["headers"] = server_config["headers"]
            connections[server_name] = connection
            continue

        connections[server_name] = StdioConnection(
            command=server_config["command"],
            args=server_config.get("args", []),
            cwd=server_config.get("cwd"),
            env=server_config.get("env"),
            transport="stdio",
        )

    exit_stack = AsyncExitStack()
    manager = MCPSessionManager(exit_stack=exit_stack)

    try:
        client = MultiServerMCPClient(connections=connections)
        tools: list[BaseTool] = []
        for server_name in config["mcpServers"]:
            session = await exit_stack.enter_async_context(client.session(server_name))
            server_tools = await load_tools_from_session(
                session,
                server_name=server_name,
                tool_name_prefix=True,
            )
            for tool in server_tools:
                rename_mcp_tool(server_name, tool)
            tools.extend(server_tools)
        return tools, manager
    except Exception as exc:
        await manager.cleanup()
        msg = f"Failed to initialize MCP tools from '{config_path}': {exc}"
        raise MCPConfigurationError(msg) from exc
