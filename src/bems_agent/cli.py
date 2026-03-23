from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import shutil
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.shortcuts.dialogs import yes_no_dialog
from prompt_toolkit.styles import Style

from bems_agent.agent.exceptions import (
    AgentConfigurationError,
    MCPConfigurationError,
    SessionNotFoundError,
)
from bems_agent.agent.mcp import MCPServerSummary, load_mcp_config, summarize_mcp_servers
from bems_agent.agent.service import (
    ConversationSessionContext,
    ConversationTraceEvent,
    MCPToolMetadata,
    agent_runtime,
    conversation_service,
)
from bems_agent.core.config import get_settings
from bems_agent.core.deepagents_cli_compat import ensure_deepagents_cli_available

ensure_deepagents_cli_available()

_autocomplete = importlib.import_module("deepagents_cli.widgets.autocomplete")
_input = importlib.import_module("deepagents_cli.input")
load_cli_skills = importlib.import_module("deepagents_cli.skills.load").list_skills
_tool_display = importlib.import_module("deepagents_cli.tool_display")
format_tool_display = _tool_display.format_tool_display
format_tool_message_content = _tool_display.format_tool_message_content
find_project_root = importlib.import_module("deepagents_cli.project_utils").find_project_root
fuzzy_search_files = _autocomplete._fuzzy_search
get_project_files = _autocomplete._get_project_files
parse_file_mentions = _input.parse_file_mentions
parse_pasted_path_payload = _input.parse_pasted_path_payload
EMAIL_PREFIX_PATTERN = _input.EMAIL_PREFIX_PATTERN

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"


@dataclass(slots=True)
class ChatRuntimeState:
    thread_id: str
    model: str
    mcp_enabled: bool
    tool_count: int
    mcp_tool_names: list[str]
    skills_paths: list[str]
    memory_paths: list[str]
    mcp_tools: list[MCPToolMetadata]
    trace_enabled: bool = True
    file_completion_roots: list[str] = field(default_factory=list)
    model_override: str | None = None


@dataclass(frozen=True, slots=True)
class SkillSummary:
    name: str
    description: str
    scope: str
    source_path: Path
    skill_path: Path


@dataclass(frozen=True, slots=True)
class SlashCommandSummary:
    command: str
    description: str


@dataclass(frozen=True, slots=True)
class SlashCommandAction:
    label: str
    description: str
    command: str


@dataclass(frozen=True, slots=True)
class SlashCommandMenu:
    title: str
    subtitle: str
    actions: tuple[SlashCommandAction, ...]


SLASH_COMMANDS: tuple[SlashCommandSummary, ...] = (
    SlashCommandSummary("/help", "Show available slash commands"),
    SlashCommandSummary("/files", "Manage file autocomplete directories"),
    SlashCommandSummary("/skills", "Browse loaded skills and skill actions"),
    SlashCommandSummary("/mcp", "Inspect MCP status and loaded tools"),
    SlashCommandSummary("/model", "Show or switch the active model"),
    SlashCommandSummary("/session", "Show the current thread"),
    SlashCommandSummary("/sessions", "List recent persisted threads"),
    SlashCommandSummary("/trace", "Show or toggle execution trace output"),
    SlashCommandSummary("/exit", "Exit the CLI"),
)


SLASH_COMMAND_MENUS: dict[str, SlashCommandMenu] = {
    "/files": SlashCommandMenu(
        title="Files",
        subtitle="Choose an action",
        actions=(
            SlashCommandAction(
                label="List roots",
                description="Show current file autocomplete directories.",
                command="/files roots",
            ),
            SlashCommandAction(
                label="Enable Downloads",
                description="Request access to your Downloads directory for autocomplete.",
                command="/files downloads",
            ),
        ),
    ),
    "/skills": SlashCommandMenu(
        title="Skills",
        subtitle="Choose an action",
        actions=(
            SlashCommandAction(
                label="List skills",
                description="Show loaded skill names and descriptions.",
                command="/skills list",
            ),
            SlashCommandAction(
                label="Skill sources",
                description="Show configured skill directories by scope.",
                command="/skills sources",
            ),
        ),
    ),
    "/mcp": SlashCommandMenu(
        title="MCP",
        subtitle="Choose an action",
        actions=(
            SlashCommandAction(
                label="Status",
                description="Show current MCP status and loaded tool count.",
                command="/mcp",
            ),
            SlashCommandAction(
                label="List servers",
                description="Show configured MCP servers and loaded tools.",
                command="/mcp list",
            ),
            SlashCommandAction(
                label="Enable MCP",
                description="Enable MCP for subsequent turns in this session.",
                command="/mcp on",
            ),
            SlashCommandAction(
                label="Disable MCP",
                description="Disable MCP for subsequent turns in this session.",
                command="/mcp off",
            ),
        ),
    ),
    "/model": SlashCommandMenu(
        title="Model",
        subtitle="Choose an action",
        actions=(
            SlashCommandAction(
                label="Current model",
                description="Show the active model for this session.",
                command="/model",
            ),
        ),
    ),
    "/trace": SlashCommandMenu(
        title="Trace",
        subtitle="Choose an action",
        actions=(
            SlashCommandAction(
                label="Trace status",
                description="Show whether execution trace output is enabled.",
                command="/trace",
            ),
            SlashCommandAction(
                label="Trace on",
                description="Enable execution trace output for subsequent turns.",
                command="/trace on",
            ),
            SlashCommandAction(
                label="Trace off",
                description="Disable execution trace output for subsequent turns.",
                command="/trace off",
            ),
        ),
    ),
}

SLASH_PROMPT_STYLE = Style.from_dict(
    {
        "prompt": "bold #0891b2",
        "completion-menu": "bg:#f8fafc #0f172a",
        "completion-menu.completion": "bg:#f8fafc #0f172a",
        "completion-menu.completion.current": "bg:#f8fafc #0891b2 bold",
        "completion-menu.meta.completion": "bg:#f8fafc #64748b",
        "completion-menu.meta.completion.current": "bg:#f8fafc #0891b2 bold",
        "scrollbar.background": "bg:#f8fafc",
        "scrollbar.button": "bg:#f8fafc",
        "scrollbar.arrow": "bg:#f8fafc #f8fafc",
        "scrollbar.start": "bg:#f8fafc nounderline",
        "scrollbar.end": "bg:#f8fafc nounderline",
        "menu": "noinherit",
        "menu.title": "noinherit bold #0891b2",
        "menu.subtitle": "noinherit #94a3b8",
        "menu.item": "noinherit #64748b",
        "menu.item.selected": "noinherit bold #0891b2",
        "menu.tip": "noinherit #94a3b8",
        "menu.tip.selected": "noinherit #0891b2",
        "menu.hint": "noinherit #94a3b8 italic",
    }
)

MAX_SLASH_SUGGESTIONS = 8
MAX_FILE_EMBED_BYTES = 256 * 1024
MAX_COMPLETION_FILES = 4000
MAX_COMPLETION_DEPTH = 6
COMPLETION_SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
}

_file_completion_cache: dict[str, list[str]] = {}
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BEMS Agent CLI.")
    subparsers = parser.add_subparsers(dest="command")

    chat_parser = subparsers.add_parser("chat", help="Start an interactive BEMS Agent session.")
    add_chat_arguments(chat_parser)

    serve_parser = subparsers.add_parser("serve", help="Run the BEMS Agent HTTP API.")
    serve_parser.add_argument(
        "--host",
        default=None,
        help="Override APP_HOST from the environment.",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override APP_PORT from the environment.",
    )
    serve_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for local development.",
    )
    return parser


def add_chat_arguments(parser: argparse.ArgumentParser) -> None:
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument(
        "--session",
        default=None,
        help="Continue an existing local thread by ID.",
    )
    session_group.add_argument("--new", action="store_true", help="Create a new local thread.")
    parser.add_argument("--message", default=None, help="Send a single message and exit.")
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="Disable MCP tools for this chat run.",
    )
    parser.add_argument(
        "--hide-steps",
        action="store_true",
        help="Hide execution trace output and only print the final response.",
    )


def normalize_argv(argv: list[str] | None = None) -> list[str]:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return ["chat"]
    if args[0] in {"chat", "serve", "-h", "--help"}:
        return args
    if args[0].startswith("-"):
        return ["chat", *args]
    return ["chat", *args]


def build_runtime_state(
    context: ConversationSessionContext,
    *,
    trace_enabled: bool,
) -> ChatRuntimeState:
    return ChatRuntimeState(
        thread_id=context.thread_id,
        model=context.model,
        mcp_enabled=context.mcp_enabled,
        tool_count=context.tool_count,
        mcp_tool_names=context.tool_names,
        skills_paths=context.skills_paths,
        memory_paths=context.memory_paths,
        mcp_tools=context.mcp_tools,
        trace_enabled=trace_enabled,
        file_completion_roots=[get_settings().project_root.as_posix()],
        model_override=None,
    )


def print_session_banner(state: ChatRuntimeState) -> None:
    print(style_text("BEMS Agent", BOLD + CYAN))
    print(f"{style_text('Thread', DIM)}: {state.thread_id}")
    print(f"{style_text('Model', DIM)}: {state.model}")
    print(f"{style_text('MCP', DIM)}: {'enabled' if state.mcp_enabled else 'disabled'}")
    print(f"{style_text('Loaded MCP tools', DIM)}: {state.tool_count}")
    print(f"{style_text('Skills sources', DIM)}: {len(state.skills_paths)}")
    print(f"{style_text('Memory sources', DIM)}: {len(state.memory_paths)}")
    print(f"{style_text('File roots', DIM)}: {len(state.file_completion_roots)}")
    print(f"{style_text('Trace', DIM)}: {'enabled' if state.trace_enabled else 'disabled'}")


def print_help() -> None:
    print("Commands:")
    print("  @<file>              Auto-complete local files and inject their content")
    print("  /files               Open the file autocomplete action menu")
    print("  /files roots         Show file autocomplete directories")
    print("  /files downloads     Request Downloads directory for autocomplete")
    print("  /files add <path>    Add an absolute directory to autocomplete roots")
    print("  /help                Show available slash commands")
    print("  /skills              Open the skills action menu")
    print("  /skills list         List loaded skills with names and descriptions")
    print("  /skills sources      Show configured skill directories")
    print("  /mcp                 Show MCP status")
    print("  /mcp list            List configured MCP servers and loaded tools")
    print("  /mcp on|off          Toggle MCP for subsequent turns")
    print("  /model               Show current model")
    print("  /model <name>        Switch model for subsequent turns")
    print("  /session             Show current thread")
    print("  /sessions            List recent persisted threads")
    print("  /trace               Show trace status")
    print("  /trace on|off        Toggle execution trace output")
    print("  /exit                Exit the CLI")


def resolve_skill_source_path(raw_path: str) -> Path:
    settings = get_settings()
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (settings.project_root / path).resolve()


def classify_skill_scope(source_path: Path) -> str:
    settings = get_settings()
    project_skills_path = (settings.project_root / "skills" / "project").resolve()
    global_skills_path = (settings.bems_home_path / "skills").resolve()
    if source_path == project_skills_path:
        return "project"
    if source_path == global_skills_path:
        return "global"
    return "custom"


def parse_skill_frontmatter(skill_path: Path) -> tuple[str, str]:
    content = skill_path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return skill_path.parent.name, ""

    name = skill_path.parent.name
    description = ""
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key == "name" and value:
            name = value
        elif key == "description":
            description = value
    return name, description


def discover_skills(skills_paths: list[str]) -> list[SkillSummary]:
    settings = get_settings()
    resolved_paths = [resolve_skill_source_path(path) for path in skills_paths]
    project_path = (settings.project_root / "skills" / "project").resolve()
    global_path = (settings.bems_home_path / "skills").resolve()

    summaries: dict[tuple[str, str], SkillSummary] = {}
    if project_path in resolved_paths or global_path in resolved_paths:
        cli_skills = load_cli_skills(
            built_in_skills_dir=None,
            user_skills_dir=global_path if global_path in resolved_paths else None,
            project_skills_dir=project_path if project_path in resolved_paths else None,
            user_agent_skills_dir=None,
            project_agent_skills_dir=None,
        )
        for skill in cli_skills:
            source_path = project_path if skill["source"] == "project" else global_path
            summaries[(skill["source"], skill["name"])] = SkillSummary(
                name=skill["name"],
                description=skill["description"],
                scope="project" if skill["source"] == "project" else "global",
                source_path=source_path,
                skill_path=(source_path / skill["path"].lstrip("/")).resolve(),
            )

    for source_path in resolved_paths:
        if source_path in {project_path, global_path}:
            continue
        if not source_path.exists():
            continue
        for skill_dir in sorted(source_path.iterdir()):
            skill_path = skill_dir / "SKILL.md"
            if not skill_dir.is_dir() or not skill_path.exists():
                continue
            name, description = parse_skill_frontmatter(skill_path)
            summaries[(source_path.as_posix(), name)] = SkillSummary(
                name=name,
                description=description,
                scope=classify_skill_scope(source_path),
                source_path=source_path,
                skill_path=skill_path,
            )

    return sorted(summaries.values(), key=lambda skill: (skill.scope, skill.name))


def load_mcp_summaries() -> list[MCPServerSummary]:
    settings = get_settings()
    return summarize_mcp_servers(load_mcp_config(str(settings.resolved_mcp_config_path)))


def terminal_width(default: int = 100) -> int:
    return shutil.get_terminal_size((default, 24)).columns


def downloads_root() -> Path:
    return Path.home() / "Downloads"


def normalize_directory(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def invalidate_file_completion_cache(root: Path | None = None) -> None:
    if root is None:
        _file_completion_cache.clear()
        return
    _file_completion_cache.pop(root.as_posix(), None)


def list_completion_files(root: Path) -> list[str]:
    cache_key = root.as_posix()
    cached = _file_completion_cache.get(cache_key)
    if cached is not None:
        return cached

    files: list[str] = []
    root_depth = len(root.parts)
    try:
        for current_root, dirnames, filenames in os.walk(root):
            current_path = Path(current_root)
            relative_depth = len(current_path.parts) - root_depth
            dirnames[:] = [
                name
                for name in dirnames
                if name not in COMPLETION_SKIP_DIRS and not name.startswith(".")
            ]
            if relative_depth >= MAX_COMPLETION_DEPTH:
                dirnames[:] = []

            for filename in filenames:
                if filename.startswith("."):
                    continue
                path = current_path / filename
                try:
                    relative_path = path.relative_to(root).as_posix()
                except ValueError:
                    continue
                files.append(relative_path)
                if len(files) >= MAX_COMPLETION_FILES:
                    _file_completion_cache[cache_key] = files
                    return files
    except OSError:
        _file_completion_cache[cache_key] = []
        return []

    _file_completion_cache[cache_key] = files
    return files


def match_slash_commands(text: str) -> list[SlashCommandSummary]:
    if not text.startswith("/"):
        return []
    if parse_pasted_path_payload(text, allow_leading_path=True) is not None:
        return []

    search = text[1:].lower()
    if " " in search:
        return []
    if not search:
        return list(SLASH_COMMANDS[:MAX_SLASH_SUGGESTIONS])

    matches = [
        summary
        for summary in SLASH_COMMANDS
        if summary.command.lstrip("/").lower().startswith(search)
    ]
    return matches[:MAX_SLASH_SUGGESTIONS]


def extract_file_mention_fragment(text: str) -> tuple[int, str] | None:
    if not text:
        return None

    at_index = text.rfind("@")
    if at_index < 0:
        return None
    if at_index > 0 and EMAIL_PREFIX_PATTERN.search(text[:at_index]):
        return None

    fragment = text[at_index:]
    if not fragment or " " in fragment:
        return None
    return at_index, fragment


@dataclass(frozen=True, slots=True)
class FileCompletionMatch:
    replacement: str
    display: str
    meta: str


def match_file_mentions(
    text: str,
    *,
    roots: list[str] | None = None,
    cwd: Path | None = None,
) -> list[FileCompletionMatch]:
    fragment_info = extract_file_mention_fragment(text)
    if fragment_info is None:
        return []

    _, fragment = fragment_info
    search = fragment[1:]
    completion_roots = roots or [get_settings().project_root.as_posix()]

    suggestions: list[FileCompletionMatch] = []
    for root_text in completion_roots:
        root = Path(root_text)
        files = list_completion_files(root)
        matches = fuzzy_search_files(
            search,
            files,
            limit=MAX_SLASH_SUGGESTIONS,
            include_dotfiles=search.startswith("."),
        )
        for path in matches:
            relative_path = Path(path)
            absolute_path = (root / relative_path).resolve()
            if root == get_settings().project_root:
                replacement = f"@{path}"
                display = replacement
            else:
                replacement = f"@{absolute_path.as_posix()}"
                display = f"@{absolute_path.as_posix()}"
            ext = relative_path.suffix.lower()
            meta = ext[1:] if ext else "file"
            suggestions.append(
                FileCompletionMatch(
                    replacement=replacement,
                    display=display,
                    meta=meta,
                )
            )
            if len(suggestions) >= MAX_SLASH_SUGGESTIONS:
                return suggestions
    return suggestions


class SlashCommandCompleter(Completer):
    def __init__(
        self,
        *,
        cwd: Path | None = None,
        state_provider: callable | None = None,
    ) -> None:
        self._cwd = cwd or Path.cwd()
        self._state_provider = state_provider

    def get_completions(self, document: Document, _complete_event: object) -> list[Completion]:
        text = document.text_before_cursor
        for summary in match_slash_commands(text):
            yield Completion(
                summary.command,
                start_position=-len(text),
                display=summary.command,
                display_meta=summary.description,
            )
        state = self._state_provider() if self._state_provider is not None else None
        file_matches = match_file_mentions(
            text,
            cwd=self._cwd,
            roots=state.file_completion_roots if state is not None else None,
        )
        fragment_info = extract_file_mention_fragment(text)
        if fragment_info is None:
            return
        _, fragment = fragment_info
        for match in file_matches:
            yield Completion(
                match.replacement,
                start_position=-len(fragment),
                display=match.display,
                display_meta=match.meta,
            )


def apply_selected_completion(buffer: object) -> bool:
    complete_state = getattr(buffer, "complete_state", None)
    current_completion = getattr(complete_state, "current_completion", None)
    if current_completion is None:
        return False
    buffer.apply_completion(current_completion)
    return True


def completion_mode(text: str) -> str | None:
    if (
        text.startswith("/")
        and " " not in text
        and parse_pasted_path_payload(text, allow_leading_path=True) is None
    ):
        return "slash"
    if extract_file_mention_fragment(text) is not None:
        return "file"
    return None


def build_prompt_session(state_provider: callable) -> PromptSession[str]:
    bindings = KeyBindings()

    @bindings.add("enter")
    def handle_enter(event: object) -> None:
        buffer = event.current_buffer
        mode = completion_mode(buffer.document.text_before_cursor)
        if apply_selected_completion(buffer):
            if mode == "slash":
                buffer.validate_and_handle()
            return
        buffer.validate_and_handle()

    @bindings.add("right")
    def handle_right(event: object) -> None:
        buffer = event.current_buffer
        if apply_selected_completion(buffer):
            return
        buffer.cursor_right(count=1)

    @bindings.add("left")
    def handle_left(event: object) -> None:
        buffer = event.current_buffer
        if buffer.complete_state is not None:
            buffer.cancel_completion()
            return
        buffer.cursor_left(count=1)

    @bindings.add("down")
    def handle_down(event: object) -> None:
        buffer = event.current_buffer
        if completion_mode(buffer.document.text_before_cursor) is not None:
            if buffer.complete_state is None:
                buffer.start_completion(select_first=True)
            else:
                buffer.complete_next()
            return
        buffer.auto_down(count=1)

    @bindings.add("up")
    def handle_up(event: object) -> None:
        buffer = event.current_buffer
        if buffer.complete_state is not None:
            buffer.complete_previous()
            return
        buffer.auto_up(count=1)

    return PromptSession[str](
        completer=SlashCommandCompleter(state_provider=state_provider),
        complete_while_typing=True,
        complete_style=CompleteStyle.COLUMN,
        reserve_space_for_menu=8,
        key_bindings=bindings,
        style=SLASH_PROMPT_STYLE,
    )


async def request_directory_access(root: Path) -> bool:
    return await yes_no_dialog(
        title="Grant Directory Access",
        text=(
            "Allow file autocomplete to search this directory?\n\n"
            f"{root.as_posix()}\n\n"
            "This path is outside the project root."
        ),
        yes_text="Allow",
        no_text="Cancel",
        style=SLASH_PROMPT_STYLE,
    ).run_async()


async def add_file_completion_root(
    state: ChatRuntimeState,
    root: Path,
    *,
    interactive: bool,
) -> bool:
    project_root = get_settings().project_root.resolve()
    root = root.resolve()

    if not root.exists() or not root.is_dir():
        print(f"Directory not found: {root}")
        return False

    if root.as_posix() in state.file_completion_roots:
        print(f"Already added: {root}")
        return False

    if not is_relative_to(root, project_root):
        if not interactive:
            print(f"Permission required to add external directory: {root}")
            return False
        allowed = await request_directory_access(root)
        if not allowed:
            print("Directory access was not granted.")
            return False

    state.file_completion_roots.append(root.as_posix())
    invalidate_file_completion_cache(root)
    print(f"Added file autocomplete root: {root}")
    return True


def print_file_completion_roots(state: ChatRuntimeState) -> None:
    print("File autocomplete roots:")
    for index, root in enumerate(state.file_completion_roots, start=1):
        scope = "project" if Path(root) == get_settings().project_root else "external"
        print(f"  {index}. [{scope}] {root}")


def collect_referenced_files(user_input: str) -> tuple[str, list[Path]]:
    prompt_text, mentioned_files = parse_file_mentions(user_input)
    referenced_files: list[Path] = []
    seen_paths: set[str] = set()

    def add_paths(paths: list[Path]) -> None:
        for path in paths:
            path_key = path.as_posix()
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            referenced_files.append(path)

    add_paths(mentioned_files)

    pasted_payload = parse_pasted_path_payload(user_input, allow_leading_path=True)
    if pasted_payload is None:
        return prompt_text, referenced_files

    add_paths(pasted_payload.paths)
    if pasted_payload.token_end is not None:
        prompt_text = prompt_text[pasted_payload.token_end :].lstrip()
    return prompt_text, referenced_files


def build_message_input(user_input: str) -> str:
    prompt_text, referenced_files = collect_referenced_files(user_input)
    if not referenced_files:
        return prompt_text

    context_parts = [prompt_text, "\n\n## Referenced Files\n"]
    for file_path in referenced_files:
        try:
            file_size = file_path.stat().st_size
            if file_size > MAX_FILE_EMBED_BYTES:
                size_kb = file_size // 1024
                context_parts.append(
                    f"\n### {file_path.name}\n"
                    f"Path: `{file_path}`\n"
                    f"Size: {size_kb}KB (too large to embed, use read_file tool to view)"
                )
                continue

            content = file_path.read_text(encoding="utf-8", errors="replace")
            context_parts.append(
                f"\n### {file_path.name}\n"
                f"Path: `{file_path}`\n```\n{content}\n```"
                )
        except Exception as exc:
            context_parts.append(f"\n### {file_path.name}\n[Error reading file: {exc}]")
    return "\n".join(context_parts)


def is_slash_command_input(user_input: str) -> bool:
    return user_input.startswith("/") and (
        parse_pasted_path_payload(user_input, allow_leading_path=True) is None
    )


async def run_command_menu(menu: SlashCommandMenu) -> str | None:
    selected_index = 0
    result: str | None = None

    def build_menu_text() -> list[tuple[str, str]]:
        available_width = max(min(terminal_width() - 4, 110), 60)
        label_width = min(max(len(action.label) for action in menu.actions) + 8, 28)
        tip_width = max(available_width - label_width - 4, 24)
        fragments: list[tuple[str, str]] = [
            ("class:menu.title", f"{menu.title}\n"),
            ("class:menu.subtitle", f"{menu.subtitle}\n\n"),
        ]
        for index, action in enumerate(menu.actions):
            selected = index == selected_index
            item_style = "class:menu.item.selected" if selected else "class:menu.item"
            tip_style = "class:menu.tip.selected" if selected else "class:menu.tip"
            prefix = "›" if selected else " "
            label = f"{prefix} {index + 1}. {action.label}"
            padding = max(label_width - len(label), 2)
            fragments.append((item_style, label))
            fragments.append((item_style, " " * padding))
            fragments.append((tip_style, textwrap.shorten(action.description, width=tip_width)))
            fragments.append(("", "\n"))

        fragments.extend(
            [
                ("", "\n"),
                (
                    "class:menu.hint",
                    "Use ↑/↓ to move, →/Enter to confirm, ←/Esc to go back",
                ),
            ]
        )
        return fragments

    bindings = KeyBindings()

    @bindings.add("down")
    def handle_down(event: object) -> None:
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(menu.actions)
        event.app.invalidate()

    @bindings.add("up")
    def handle_up(event: object) -> None:
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(menu.actions)
        event.app.invalidate()

    @bindings.add("right")
    @bindings.add("enter")
    def handle_accept(event: object) -> None:
        nonlocal result
        result = menu.actions[selected_index].command
        event.app.exit(result=result)

    @bindings.add("left")
    @bindings.add("escape")
    def handle_cancel(event: object) -> None:
        event.app.exit(result=None)

    body = FormattedTextControl(build_menu_text, focusable=True, show_cursor=False)
    app = Application[str | None](
        layout=Layout(HSplit([Window(content=body, height=D(preferred=len(menu.actions) + 5))])),
        key_bindings=bindings,
        style=SLASH_PROMPT_STYLE,
        full_screen=False,
    )
    return await app.run_async()


def print_skills_sources(state: ChatRuntimeState) -> None:
    print("Skill sources:")
    for skill_path in state.skills_paths:
        source_path = resolve_skill_source_path(skill_path)
        scope = classify_skill_scope(source_path)
        print(f"  [{scope}] {source_path}")


def print_skills_list(state: ChatRuntimeState) -> None:
    skills = discover_skills(state.skills_paths)
    print(style_text("Skills", BOLD + CYAN))
    if not skills:
        print("  No skills loaded.")
        return

    description_width = max(terminal_width() - 6, 50)
    for index, skill in enumerate(skills, start=1):
        description = skill.description or "No description provided."
        print(style_text(f"{index}. {skill.name}", BOLD))
        for line in textwrap.wrap(description, width=description_width):
            print(f"   {line}")
        if index != len(skills):
            print()


def print_mcp_list(state: ChatRuntimeState) -> None:
    print(
        f"MCP is {'enabled' if state.mcp_enabled else 'disabled'} "
        f"for this thread; loaded tools={state.tool_count}"
    )
    if state.mcp_tool_names:
        print("Loaded tool names:")
        tools = state.mcp_tools or [MCPToolMetadata(name=name) for name in state.mcp_tool_names]
        for tool in tools:
            if tool.server_name:
                print(f"  [{tool.server_name}] {tool.name}")
            else:
                print(f"  {tool.name}")

    print("Configured servers:")
    for server in load_mcp_summaries():
        print(f"  [{server.deployment}] {server.name}")
        print(f"    transport={server.transport}")
        if server.endpoint:
            print(f"    endpoint={server.endpoint}")
        if server.command:
            print(f"    command={server.command}")
        if server.args:
            print(f"    args={' '.join(server.args)}")
        if server.cwd:
            print(f"    cwd={server.cwd}")
        print(f"    headers={server.headers_count}")


async def refresh_runtime_state(
    state: ChatRuntimeState,
    *,
    mcp_enabled: bool | None = None,
    model_override: str | None = None,
) -> ChatRuntimeState:
    resolved_model_override = state.model_override if model_override is None else model_override
    context = await conversation_service.open_session(
        session_id=state.thread_id,
        mcp_enabled=state.mcp_enabled if mcp_enabled is None else mcp_enabled,
        model_override=resolved_model_override,
    )
    return ChatRuntimeState(
        thread_id=context.thread_id,
        model=context.model,
        mcp_enabled=context.mcp_enabled,
        tool_count=context.tool_count,
        mcp_tool_names=context.tool_names,
        skills_paths=context.skills_paths,
        memory_paths=context.memory_paths,
        mcp_tools=context.mcp_tools,
        trace_enabled=state.trace_enabled,
        file_completion_roots=list(state.file_completion_roots),
        model_override=resolved_model_override,
    )


def style_text(text: str, style: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{style}{text}{RESET}"


def split_tool_name(tool_name: str) -> tuple[str | None, str]:
    for server in load_mcp_summaries():
        if tool_name.startswith(f"{server.name}__"):
            return server.name, tool_name.removeprefix(f"{server.name}__")
        if tool_name.startswith(f"{server.name}_"):
            return server.name, tool_name.removeprefix(f"{server.name}_")
    return None, tool_name


def resolve_tool_metadata(state: ChatRuntimeState, tool_name: str) -> tuple[str | None, str]:
    for tool in state.mcp_tools:
        if tool.name == tool_name:
            return (tool.server_name or None), tool.name
    return split_tool_name(tool_name)


def render_preview_block(content: str, *, max_lines: int = 8, max_chars: int = 500) -> list[str]:
    normalized = content.strip()
    if not normalized:
        return ["(empty)"]

    preview = normalized[:max_chars].rstrip()
    if len(normalized) > max_chars:
        preview += "..."

    json_candidate = preview
    if json_candidate.startswith("{") or json_candidate.startswith("["):
        try:
            formatted = json.dumps(json.loads(json_candidate), ensure_ascii=False, indent=2)
            lines = formatted.splitlines()
        except Exception:
            lines = preview.splitlines()
    else:
        lines = preview.splitlines()

    trimmed = [line.rstrip() for line in lines if line.strip() or len(lines) == 1]
    if len(trimmed) > max_lines:
        remaining = len(trimmed) - max_lines
        return [*trimmed[:max_lines], f"... (+{remaining} more lines)"]
    return trimmed


def strip_markdown_inline(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.strip()


def format_assistant_response(response: str) -> str:
    lines = response.splitlines()
    rendered: list[str] = []
    in_code_block = False

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            if rendered and rendered[-1] != "":
                rendered.append("")
            in_code_block = not in_code_block
            continue

        if in_code_block:
            rendered.append(f"    {line}")
            continue

        if not stripped:
            if rendered and rendered[-1] != "":
                rendered.append("")
            continue

        if re.fullmatch(r"[-*_]{3,}", stripped):
            if rendered and rendered[-1] != "":
                rendered.append("")
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = strip_markdown_inline(heading_match.group(2))
            if rendered and rendered[-1] != "":
                rendered.append("")
            rendered.append(heading_text.upper() if level <= 2 else heading_text)
            continue

        bullet_match = re.match(r"^[-*+]\s+(.*)$", stripped)
        if bullet_match:
            bullet_text = strip_markdown_inline(bullet_match.group(1))
            label_match = re.match(r"^([^:.-]+?)\s+[—-]\s+(.*)$", bullet_text)
            if label_match:
                bullet_text = f"{label_match.group(1).strip()}: {label_match.group(2).strip()}"
            rendered.append(f"  - {bullet_text}")
            continue

        numbered_match = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if numbered_match:
            item_text = strip_markdown_inline(numbered_match.group(2))
            rendered.append(f"{numbered_match.group(1)}. {item_text}")
            continue

        rendered.append(strip_markdown_inline(stripped))

    while rendered and rendered[-1] == "":
        rendered.pop()

    return "\n".join(rendered)


def print_assistant_response(response: str) -> None:
    print(format_assistant_response(response))


def print_trace_event_card(event: ConversationTraceEvent, state: ChatRuntimeState) -> None:
    if event.kind == "status":
        print(style_text(f"> {event.title}", DIM))
        if event.detail:
            print(style_text(f"  {event.detail}", DIM))
        return

    if event.kind == "tool_call":
        server_name, _ = resolve_tool_metadata(state, event.tool_name)
        print()
        print(style_text("MCP Tool" if server_name else "Tool", BOLD + CYAN))
        if server_name:
            print(f"  {style_text('server', DIM)}: {server_name}")
        print(
            f"  {style_text('call', DIM)}: "
            f"{format_tool_display(event.tool_name, event.tool_args or {})}"
        )
        return

    if event.kind == "tool_result":
        server_name, tool_name = resolve_tool_metadata(state, event.tool_name)
        raw_preview = format_tool_message_content(event.tool_output or event.detail)
        print(style_text("MCP Result" if server_name else "Tool Result", BOLD + GREEN))
        if server_name:
            print(f"  {style_text('server', DIM)}: {server_name}")
        print(f"  {style_text('tool', DIM)}: {tool_name}")
        print(f"  {style_text('preview', DIM)}:")
        for line in render_preview_block(raw_preview):
            print(f"    {line}")
        return

    print(style_text(f"[trace] {event.title}", DIM))
    if event.detail:
        print(style_text(f"        {event.detail}", DIM))


async def handle_slash_command(
    raw_input: str,
    state: ChatRuntimeState,
    *,
    interactive: bool = False,
) -> tuple[ChatRuntimeState, bool]:
    parts = raw_input.split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if command in {"/exit", "/quit"}:
        return state, False
    if command == "/help":
        print_help()
        return state, True
    if command == "/files":
        if interactive and not argument:
            selection = await run_command_menu(SLASH_COMMAND_MENUS["/files"])
            if selection is None:
                return state, True
            return await handle_slash_command(selection, state, interactive=False)
        if argument == "roots":
            print_file_completion_roots(state)
            return state, True
        if argument == "downloads":
            await add_file_completion_root(
                state,
                downloads_root(),
                interactive=interactive,
            )
            return state, True
        if argument.startswith("add "):
            path_text = argument.removeprefix("add ").strip()
            path = Path(path_text).expanduser()
            if not path.is_absolute():
                print("Usage: /files add <absolute-path>")
                return state, True
            await add_file_completion_root(
                state,
                path.resolve(),
                interactive=interactive,
            )
            return state, True
        if argument:
            print("Usage: /files [roots|downloads|add <absolute-path>]")
            return state, True
        print("Use /files roots to inspect autocomplete directories.")
        return state, True
    if command == "/skills":
        if interactive and not argument:
            selection = await run_command_menu(SLASH_COMMAND_MENUS["/skills"])
            if selection is None:
                return state, True
            return await handle_slash_command(selection, state, interactive=False)
        if argument == "list":
            print_skills_list(state)
            return state, True
        if argument == "sources":
            print_skills_sources(state)
            return state, True
        if argument:
            print("Usage: /skills [list|sources]")
            return state, True
        print("Use /skills list to browse loaded skills.")
        return state, True
    if command == "/mcp":
        if interactive and not argument:
            selection = await run_command_menu(SLASH_COMMAND_MENUS["/mcp"])
            if selection is None:
                return state, True
            return await handle_slash_command(selection, state, interactive=False)
        if argument == "list":
            print_mcp_list(state)
            return state, True
        if not argument:
            print(f"MCP is {'enabled' if state.mcp_enabled else 'disabled'}")
            print(f"Loaded MCP tools: {state.tool_count}")
            print(f"Config: {get_settings().resolved_mcp_config_path}")
            return state, True
        if argument not in {"on", "off"}:
            print("Usage: /mcp [on|off]")
            return state, True
        state = await refresh_runtime_state(state, mcp_enabled=argument == "on")
        print(f"MCP is now {'enabled' if state.mcp_enabled else 'disabled'}")
        print(f"Loaded MCP tools: {state.tool_count}")
        return state, True
    if command == "/model":
        if interactive and not argument and command in SLASH_COMMAND_MENUS:
            selection = await run_command_menu(SLASH_COMMAND_MENUS[command])
            if selection is None:
                return state, True
            return await handle_slash_command(selection, state, interactive=False)
        if not argument:
            if state.model_override is None:
                state = await refresh_runtime_state(state)
            print(f"Current model: {state.model}")
            return state, True
        state = await refresh_runtime_state(state, model_override=argument)
        print(f"Model switched to: {state.model}")
        return state, True
    if command == "/session":
        print(f"Current thread: {state.thread_id}")
        return state, True
    if command == "/sessions":
        print("Recent threads:")
        for session in conversation_service.list_sessions():
            print(
                "  "
                f"{session['thread_id']}  "
                f"turns={session['turn_count']}  "
                f"updated={session['updated_at']}"
            )
        return state, True
    if command == "/trace":
        if interactive and not argument and command in SLASH_COMMAND_MENUS:
            selection = await run_command_menu(SLASH_COMMAND_MENUS[command])
            if selection is None:
                return state, True
            return await handle_slash_command(selection, state, interactive=False)
        if not argument:
            print(f"Trace is {'enabled' if state.trace_enabled else 'disabled'}")
            return state, True
        if argument not in {"on", "off"}:
            print("Usage: /trace [on|off]")
            return state, True
        state.trace_enabled = argument == "on"
        print(f"Trace is now {'enabled' if state.trace_enabled else 'disabled'}")
        return state, True

    print("Unknown command. Use /help.")
    return state, True


async def run_chat(args: argparse.Namespace) -> int:
    context = await conversation_service.open_session(
        session_id=args.session,
        create_new=args.new,
        mcp_enabled=False if args.no_mcp else None,
    )
    state = build_runtime_state(context, trace_enabled=not args.hide_steps)
    print_session_banner(state)

    if args.message:
        message_input = build_message_input(args.message)
        async for event in conversation_service.stream_message(
            message_input,
            session_id=state.thread_id,
            mcp_enabled=state.mcp_enabled,
            model_override=state.model_override,
        ):
            if state.trace_enabled and event.kind != "final_response":
                print_trace_event_card(event, state)
            if event.kind == "final_response":
                print()
                print_assistant_response(event.response)
        return 0

    print("Enter `/help` to list commands. Use `/exit` to leave the session.")
    session = (
        build_prompt_session(lambda: state) if sys.stdin.isatty() and sys.stdout.isatty() else None
    )
    while True:
        try:
            if session is None:
                user_input = input("> ").strip()
            else:
                user_input = (await session.prompt_async([("class:prompt", "> ")])).strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print("\nInterrupted. Use `/exit` to close the session.")
            continue

        if not user_input:
            continue
        if is_slash_command_input(user_input):
            state, should_continue = await handle_slash_command(
                user_input,
                state,
                interactive=session is not None,
            )
            if should_continue:
                continue
            return 0
        if user_input in {"exit", "quit"}:
            return 0

        message_input = build_message_input(user_input)
        async for event in conversation_service.stream_message(
            message_input,
            session_id=state.thread_id,
            mcp_enabled=state.mcp_enabled,
            model_override=state.model_override,
        ):
            if state.trace_enabled and event.kind != "final_response":
                print_trace_event_card(event, state)
            if event.kind == "final_response":
                print()
                print_assistant_response(event.response)
        state = await refresh_runtime_state(state)


def run_serve(args: argparse.Namespace) -> int:
    settings = get_settings()

    uvicorn.run(
        "bems_agent.main:app",
        host=args.host or settings.app_host,
        port=args.port or settings.app_port,
        reload=args.reload,
    )
    return 0


async def run_cli(args: argparse.Namespace) -> int:
    try:
        return await run_chat(args)
    finally:
        await agent_runtime.shutdown()


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(argv))
    if args.command == "serve":
        raise SystemExit(run_serve(args))

    try:
        raise SystemExit(asyncio.run(run_cli(args)))
    except (AgentConfigurationError, MCPConfigurationError, SessionNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
