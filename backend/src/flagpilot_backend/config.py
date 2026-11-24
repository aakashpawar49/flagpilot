"""Runtime settings, read from environment variables or backend/.env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Claude
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"

    # Flagsmith Admin API. The admin key is an Organisation API key
    # (Organisation Settings > API Keys). It never leaves the backend.
    flagsmith_api_url: str = "https://api.flagsmith.com/api/v1"
    flagsmith_app_url: str = "https://app.flagsmith.com"
    flagsmith_admin_key: str = ""
    flagsmith_project_id: int | None = None
    # Client-side environment key. Identifies the environment in Admin API paths
    # and is safe to ship to browsers.
    flagsmith_environment_key: str = ""
    # Optional server-side key for the traffic simulator. Created on demand if empty.
    flagsmith_server_key: str = ""

    # GitHub: a fine-grained personal access token with Contents and
    # Pull requests read/write on the target repository.
    github_token: str = ""
    github_api_url: str = "https://api.github.com"
    default_repo: str = ""
    default_base_branch: str = "main"
    default_app_path: str = ""

    data_dir: str = "data"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
