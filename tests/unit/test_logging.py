"""Unit tests for secret redaction and structured logging."""

import logging

from issuelens.core.logging import SecretRedactionFilter, redact_secrets


def test_redact_github_pat() -> None:
    """Ensure GitHub PATs are masked."""
    token = "ghp_1234567890abcdefghijklmnopqrstuvwxyzAB"
    log_msg = f"Fetching repo with token {token} for user"
    redacted = redact_secrets(log_msg)
    assert token not in redacted
    assert "[REDACTED_SECRET]" in redacted


def test_redact_openai_key() -> None:
    """Ensure OpenAI / provider keys are masked."""
    key = "sk-abcdef1234567890abcdef1234567890"
    log_msg = f"Connecting to provider using key {key}"
    redacted = redact_secrets(log_msg)
    assert key not in redacted
    assert "[REDACTED_SECRET]" in redacted


def test_redact_database_url_password() -> None:
    """Ensure passwords in DB connection strings are masked."""
    db_url = "postgresql+psycopg://dbuser:super_secret_pw@localhost:5432/issuelens"
    redacted = redact_secrets(db_url)
    assert "super_secret_pw" not in redacted
    assert "dbuser:***@" in redacted


def test_logging_filter_integration() -> None:
    """Ensure SecretRedactionFilter automatically redacts records."""
    log_filter = SecretRedactionFilter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="API called with key sk-1234567890abcdef1234567890",
        args=(),
        exc_info=None,
    )
    result = log_filter.filter(record)
    assert result is True
    assert "sk-1234567890abcdef1234567890" not in str(record.msg)
    assert "[REDACTED_SECRET]" in str(record.msg)
