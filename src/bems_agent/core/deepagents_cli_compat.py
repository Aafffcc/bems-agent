from __future__ import annotations

import sys
from pathlib import Path

_DEEPAGENTS_CLI_SOURCE = Path("/Library/WorkSpace Python/deepagents/libs/cli")


def ensure_deepagents_cli_available() -> None:
    """Make the local deepagents_cli source importable when not installed."""
    try:
        __import__("deepagents_cli")
        return
    except ModuleNotFoundError:
        if _DEEPAGENTS_CLI_SOURCE.exists():
            source = str(_DEEPAGENTS_CLI_SOURCE)
            if source not in sys.path:
                sys.path.insert(0, source)

    __import__("deepagents_cli")
