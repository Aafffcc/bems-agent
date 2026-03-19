class AgentConfigurationError(RuntimeError):
    """Raised when the agent runtime is not configured correctly."""


class MCPConfigurationError(RuntimeError):
    """Raised when the MCP configuration is invalid."""


class SessionNotFoundError(RuntimeError):
    """Raised when a requested session does not exist."""
