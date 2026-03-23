import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="BEMS Agent", alias="APP_NAME")
    app_env: str = Field(default="local", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=9933, alias="APP_PORT")
    app_debug: bool = Field(default=True, alias="APP_DEBUG")
    api_v1_prefix: str = Field(default="/api/v1", alias="API_V1_PREFIX")
    postgres_dsn: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/bems_agent",
        alias="POSTGRES_DSN",
    )
    agent_model: str | None = Field(default=None, alias="AGENT_MODEL")
    anthropic_model: str | None = Field(default=None, alias="ANTHROPIC_MODEL")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_auth_token: str | None = Field(default=None, alias="ANTHROPIC_AUTH_TOKEN")
    anthropic_base_url: str | None = Field(default=None, alias="ANTHROPIC_BASE_URL")
    mcp_enabled: bool = Field(default=True, alias="MCP_ENABLED")
    mcp_config_path: str = Field(default="config/mcp_servers.json", alias="MCP_CONFIG_PATH")
    bems_home: str = Field(default="~/.bems-agent", alias="BEMS_HOME")
    bems_session_dir: str | None = Field(default=None, alias="BEMS_SESSION_DIR")
    bems_skills_paths: str | None = Field(default=None, alias="BEMS_SKILLS_PATHS")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            dotenv_settings,
            env_settings,
            file_secret_settings,
        )

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[3]

    @property
    def resolved_agent_model(self) -> str | None:
        if self.agent_model:
            return self.agent_model
        if self.anthropic_model:
            return self.anthropic_model
        return None

    @property
    def resolved_anthropic_api_key(self) -> str | None:
        if self.anthropic_api_key:
            return self.anthropic_api_key
        if self.anthropic_auth_token:
            return self.anthropic_auth_token
        return None

    @property
    def resolved_mcp_config_path(self) -> Path:
        path = Path(self.mcp_config_path).expanduser()
        if path.is_absolute():
            return path
        return (self.project_root / path).resolve()

    @property
    def bems_home_path(self) -> Path:
        return Path(self.bems_home).expanduser().resolve()

    @property
    def bems_session_dir_path(self) -> Path:
        if self.bems_session_dir:
            path = Path(self.bems_session_dir).expanduser()
            if path.is_absolute():
                return path.resolve()
            return (self.bems_home_path / path).resolve()
        return (self.bems_home_path / "sessions").resolve()

    @property
    def bems_session_db_path(self) -> Path:
        return (self.bems_home_path / "sessions.db").resolve()

    @property
    def resolved_skills_paths(self) -> list[str]:
        if self.bems_skills_paths:
            paths = []
            for item in self.bems_skills_paths.split(os.pathsep):
                if not item.strip():
                    continue
                candidate = Path(item).expanduser()
                if not candidate.is_absolute():
                    candidate = self.project_root / candidate
                paths.append(candidate.resolve().as_posix())
            return paths

        project_skills_path = (self.project_root / "skills" / "project").resolve()
        user_skills_path = (self.bems_home_path / "skills").resolve()
        return [
            project_skills_path.as_posix(),
            user_skills_path.as_posix(),
        ]

    @property
    def resolved_memory_paths(self) -> list[str]:
        return [
            (self.project_root / ".deepagents" / "AGENTS.md").resolve().as_posix(),
            (self.project_root / "AGENTS.md").resolve().as_posix(),
            (self.bems_home_path / "AGENTS.md").resolve().as_posix(),
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
