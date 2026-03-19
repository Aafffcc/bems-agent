from contextlib import AsyncExitStack

import pytest

from bems_agent.agent.mcp import (
    MCPSessionManager,
    normalize_mcp_config,
    rename_mcp_tool,
    summarize_mcp_servers,
)


def test_normalize_streamable_http_mcp_config() -> None:
    config = {
        "mcpServers": {
            "Energy-precise-data-query": {
                "serverUrl": "http://47.111.9.219:9977/mcp",
                "transport": "streamable-http",
                "timeout": 60000,
                "headers": {
                    "Accept": "application/json, text/event-stream",
                },
            }
        }
    }

    normalized = normalize_mcp_config(config)

    server = normalized["mcpServers"]["Energy-precise-data-query"]
    assert server["type"] == "http"
    assert server["url"] == "http://47.111.9.219:9977/mcp"
    assert server["headers"]["Accept"] == "application/json, text/event-stream"


def test_normalize_stdio_mcp_config() -> None:
    config = {
        "mcpServers": {
            "Energy-precise-data-query": {
                "command": "uv",
                "args": ["run", "energy-mcp"],
                "cwd": "/Library/WorkSpace Python/mcp-dev",
                "transport": "stdio",
            }
        }
    }

    normalized = normalize_mcp_config(config)

    server = normalized["mcpServers"]["Energy-precise-data-query"]
    assert server["transport"] == "stdio"
    assert server["command"] == "uv"
    assert server["args"] == ["run", "energy-mcp"]
    assert server["cwd"] == "/Library/WorkSpace Python/mcp-dev"


def test_normalize_http_mcp_requires_url() -> None:
    config = {
        "mcpServers": {
            "Energy-precise-data-query": {
                "transport": "streamable-http",
            }
        }
    }

    from pytest import raises

    from bems_agent.agent.exceptions import MCPConfigurationError

    with raises(MCPConfigurationError):
        normalize_mcp_config(config)


def test_summarize_mcp_servers_distinguishes_cloud_and_local() -> None:
    config = {
        "mcpServers": {
            "remote-server": {
                "serverUrl": "http://47.111.9.219:9977/mcp",
                "transport": "streamable-http",
                "headers": {"Authorization": "Bearer token"},
            },
            "local-server": {
                "command": "uv",
                "args": ["run", "energy-mcp"],
                "cwd": "/tmp/mcp",
                "transport": "stdio",
            },
        }
    }

    summaries = summarize_mcp_servers(config)

    assert summaries[0].deployment == "cloud"
    assert summaries[0].endpoint == "http://47.111.9.219:9977/mcp"
    assert summaries[0].headers_count == 1
    assert summaries[1].deployment == "local"
    assert summaries[1].command == "uv"
    assert summaries[1].args == ["run", "energy-mcp"]


def test_rename_mcp_tool_strips_server_prefix_before_mapping() -> None:
    class DummyTool:
        def __init__(self) -> None:
            self.name = "Energy-precise-data-query_query_device_logs_tool"
            self.metadata: dict[str, str] = {}

    tool = DummyTool()

    rename_mcp_tool("Energy-precise-data-query", tool)

    assert tool.name == "query_device_logs"
    assert tool.metadata["mcp_server_name"] == "Energy-precise-data-query"
    assert tool.metadata["mcp_original_name"] == "Energy-precise-data-query_query_device_logs_tool"
    assert tool.metadata["mcp_canonical_name"] == "query_device_logs_tool"


@pytest.mark.asyncio
async def test_mcp_session_manager_cleanup_ignores_busy_async_generator_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = MCPSessionManager(exit_stack=AsyncExitStack())

    async def fake_aclose() -> None:
        raise RuntimeError("aclose(): asynchronous generator is already running")

    monkeypatch.setattr(manager.exit_stack, "aclose", fake_aclose)

    await manager.cleanup()
    await manager.cleanup()
