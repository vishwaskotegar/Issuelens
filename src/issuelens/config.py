"""Configuration management for IssueLens using Pydantic Settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration and execution limits."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General Environment
    app_env: Literal["local", "test", "production"] = Field(
        default="local",
        description="Application running environment",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging level",
    )

    # Execution limits and timeouts
    request_timeout_seconds: int = Field(
        default=15,
        ge=1,
        le=300,
        description="Timeout for external API requests in seconds",
    )
    max_repository_bytes: int = Field(
        default=50_000_000,
        ge=1000,
        description="Maximum allowed repository archive size in bytes (default: 50MB)",
    )
    max_files_scanned: int = Field(
        default=10_000,
        ge=1,
        description="Maximum count of files to inspect during repository search",
    )
    max_file_bytes: int = Field(
        default=500_000,
        ge=1000,
        description="Maximum individual file size to read or search in bytes",
    )
    max_tool_calls: int = Field(
        default=12,
        ge=1,
        le=50,
        description="Maximum allowed tool calls per investigation run",
    )
    max_retrieval_rounds: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum follow-up retrieval iterations in agent loop",
    )
    max_report_tokens: int = Field(
        default=2500,
        ge=100,
        description="Maximum allowed token estimate for generated reports",
    )
    snapshot_cache_dir: str = Field(
        default=".cache/issuelens/snapshots",
        description="Directory path for storing commit-pinned repository snapshots",
    )
    max_cached_snapshots: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of distinct repository snapshots to keep in cache",
    )
    max_cache_total_bytes: int = Field(
        default=500_000_000,
        ge=10_000,
        description="Maximum total bytes across all cached snapshots (default: 500MB)",
    )

    # Service & Client Secrets (Placeholders for upcoming phases)
    database_url: str | None = Field(
        default=None,
        description="PostgreSQL connection string for persistence",
    )
    llm_provider: str | None = Field(
        default=None,
        description="LLM provider name (e.g., anthropic, openai)",
    )
    llm_model: str | None = Field(
        default=None,
        description="LLM model name identifier",
    )
    llm_api_key: SecretStr | None = Field(
        default=None,
        description="API key for LLM provider",
    )
    github_token: SecretStr | None = Field(
        default=None,
        description="GitHub personal access token for higher rate limits",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Dependency injection helper returning cached Settings singleton."""
    return Settings()
