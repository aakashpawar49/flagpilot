"""Runtime settings, read from environment variables or backend/.env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM: any OpenAI-compatible chat completions endpoint. Defaults to Google
    # Gemini's free tier (key from https://aistudio.google.com). Groq, OpenRouter
    # and local Ollama work by changing these three values.
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    llm_api_key: str = ""
    llm_model: str = "gemini-flash-latest"
    # Upper bound on repository text sent to the model in one request.
    llm_max_context_chars: int = 120_000

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
