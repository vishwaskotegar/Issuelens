"""Unit tests for configuration loading and validation."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from issuelens.config import Settings


def test_default_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify code defaults align with docs/BUILD_SPECIFICATION.md.

    Hermetic: ignores any local .env file and clears environment overrides.
    """
    for field_name in Settings.model_fields:
        monkeypatch.delenv(field_name.upper(), raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.app_env == "local"
    assert settings.request_timeout_seconds == 15
    assert settings.max_repository_bytes == 50_000_000
    assert settings.max_files_scanned == 10_000
    assert settings.max_file_bytes == 500_000
    assert settings.max_tool_calls == 12
    assert settings.max_retrieval_rounds == 3
    assert settings.max_report_tokens == 2500
    assert settings.snapshot_cache_dir == ".cache/issuelens/snapshots"
    assert settings.max_cached_snapshots == 10
    assert settings.max_cache_total_bytes == 500_000_000


def test_invalid_settings_limits() -> None:
    """Verify that invalid numeric bounds raise validation errors."""
    with pytest.raises(ValidationError):
        Settings(request_timeout_seconds=0)  # ge=1

    with pytest.raises(ValidationError):
        Settings(max_tool_calls=100)  # le=50


def test_secrets_masked_in_representation() -> None:
    """Verify SecretStr prevents accidental credential printing."""
    settings = Settings(
        llm_api_key=SecretStr("secret-key-12345"),
        github_token=SecretStr("ghp_fake1234567890abcdef"),
    )
    assert settings.llm_api_key is not None
    assert str(settings.llm_api_key) == "**********"
    assert settings.llm_api_key.get_secret_value() == "secret-key-12345"
    assert "secret-key-12345" not in repr(settings)


def test_production_mode_requires_auth_and_api_key() -> None:
    """Verify that production environment requires auth_required=True and a valid api_key."""
    # Production without auth_required -> fails
    with pytest.raises(ValidationError, match="auth_required.*must be True"):
        Settings(app_env="production", auth_required=False, api_key=SecretStr("valid-key"))

    # Production with auth_required=True but missing api_key -> fails
    with pytest.raises(ValidationError, match="api_key.*must be configured"):
        Settings(app_env="production", auth_required=True, api_key=None)

    with pytest.raises(ValidationError, match="api_key.*must be configured"):
        Settings(app_env="production", auth_required=True, api_key=SecretStr("   "))

    # Production with valid auth_required and api_key -> succeeds
    prod_settings = Settings(
        app_env="production",
        auth_required=True,
        api_key=SecretStr("valid-production-key"),
    )
    assert prod_settings.app_env == "production"
    assert prod_settings.auth_required is True
    assert prod_settings.api_key is not None
    assert prod_settings.api_key.get_secret_value() == "valid-production-key"


def test_comma_separated_list_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: comma-separated list env vars must parse instead of raising SettingsError.

    pydantic-settings JSON-decodes complex fields before field validators run, so
    ``TRUSTED_PROXIES=127.0.0.1,::1`` previously crashed application startup.
    """
    monkeypatch.setenv("TRUSTED_PROXIES", "127.0.0.1,::1, 10.0.0.0/8")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://a.test,http://b.test")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.trusted_proxies == ["127.0.0.1", "::1", "10.0.0.0/8"]
    assert settings.cors_allowed_origins == ["http://a.test", "http://b.test"]


def test_json_array_list_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON array form remains supported for list settings."""
    monkeypatch.setenv("TRUSTED_PROXIES", '["10.0.0.1", "10.0.0.2"]')
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.trusted_proxies == ["10.0.0.1", "10.0.0.2"]


def test_env_example_file_loads() -> None:
    """Regression: Quickstart `cp .env.example .env` must produce valid settings."""
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    settings = Settings(_env_file=env_example)  # type: ignore[call-arg]
    assert settings.trusted_proxies == ["127.0.0.1", "::1"]
    assert "http://localhost:3000" in settings.cors_allowed_origins
