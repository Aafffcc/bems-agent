from __future__ import annotations

import os

from bems_agent.core.config import Settings, get_settings


def test_settings_resolve_default_local_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BEMS_HOME", str(tmp_path))
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.bems_home_path == tmp_path.resolve()
    assert settings.bems_session_dir_path == (tmp_path / "sessions").resolve()
    assert settings.bems_session_db_path == (tmp_path / "sessions.db").resolve()
    assert settings.resolved_skills_paths == [
        (settings.project_root / "skills" / "project").resolve().as_posix(),
        (tmp_path / "skills").resolve().as_posix(),
    ]
    assert settings.resolved_memory_paths == [
        (settings.project_root / ".deepagents" / "AGENTS.md").resolve().as_posix(),
        (settings.project_root / "AGENTS.md").resolve().as_posix(),
        (tmp_path / "AGENTS.md").resolve().as_posix(),
    ]

    get_settings.cache_clear()


def test_settings_resolve_custom_skills_paths(monkeypatch, tmp_path) -> None:
    first = tmp_path / "skills-a"
    second = tmp_path / "skills-b"
    monkeypatch.setenv("BEMS_SKILLS_PATHS", os.pathsep.join([str(first), str(second)]))
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.resolved_skills_paths == [
        first.resolve().as_posix(),
        second.resolve().as_posix(),
    ]

    get_settings.cache_clear()


def test_settings_resolve_agent_model_from_anthropic_model(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
    settings = Settings(_env_file=None)

    assert settings.agent_model is None
    assert settings.anthropic_model is not None
    assert settings.resolved_agent_model == settings.anthropic_model


def test_settings_resolve_anthropic_api_key_from_auth_token(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-from-auth")
    settings = Settings(_env_file=None)

    assert settings.anthropic_api_key is None
    assert settings.anthropic_auth_token == "token-from-auth"
    assert settings.resolved_anthropic_api_key == "token-from-auth"


def test_settings_prioritize_dotenv_over_process_environment(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "AGENT_MODEL=anthropic:claude-sonnet-4-6",
                "ANTHROPIC_API_KEY=dotenv-key",
                "ANTHROPIC_BASE_URL=https://dotenv.example",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_MODEL", "anthropic:claude-haiku-4-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "process-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://process.example")

    settings = Settings(_env_file=env_file)

    assert settings.agent_model == "anthropic:claude-sonnet-4-6"
    assert settings.resolved_anthropic_api_key == "dotenv-key"
    assert settings.anthropic_base_url == "https://dotenv.example"
