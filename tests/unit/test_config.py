"""Unit tests for configuration loading and validation."""

import pytest
from pydantic import SecretStr, ValidationError

from issuelens.config import Settings


def test_default_settings() -> None:
    """Verify default limits align with BUILD_SPECIFICATION.md."""
    settings = Settings()
    assert settings.app_env in ["local", "test", "production"]
    assert settings.request_timeout_seconds == 15
    assert settings.max_repository_bytes == 50_000_000
    assert settings.max_files_scanned == 10_000
    assert settings.max_file_bytes == 500_000
    assert settings.max_tool_calls == 12
    assert settings.max_retrieval_rounds == 3
    assert settings.max_report_tokens == 2500


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
