"""Structured logging with automatic secret redaction."""

import logging
import re

# Regular expressions for common secret and token patterns
SECRET_PATTERNS = [
    # GitHub Personal Access Tokens (classic and fine-grained)
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,255}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{22,255}"),
    # Generic API Keys (e.g. OpenAI, Anthropic, Google)
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z-_]{35}"),
    # Authorization header tokens
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-_\.]{16,}", re.IGNORECASE),
    # Database connection passwords: scheme://user:pass@host
    re.compile(r"(://[^:]+:)([^@]+)(@)"),
]


def redact_secrets(message: str) -> str:
    """Mask known secret patterns from the provided string message."""
    redacted = message
    for pattern in SECRET_PATTERNS:
        if pattern.pattern.startswith("(://"):
            # URL password group replacement
            redacted = pattern.sub(r"\1***\3", redacted)
        elif "Bearer" in pattern.pattern:
            redacted = pattern.sub(r"\1[REDACTED_TOKEN]", redacted)
        else:
            redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    return redacted


class SecretRedactionFilter(logging.Filter):
    """Logging filter that sanitizes records to avoid accidental secret leakage."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (redact_secrets(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_secrets(arg) if isinstance(arg, str) else arg for arg in record.args
                )
        return True


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with secret redaction and structured format."""
    root_logger = logging.getLogger()
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(numeric_level)

    # Avoid duplicate handlers during re-initialization
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.addFilter(SecretRedactionFilter())
        root_logger.addHandler(handler)
    else:
        for existing_handler in root_logger.handlers:
            existing_handler.setLevel(numeric_level)
            if not any(isinstance(f, SecretRedactionFilter) for f in existing_handler.filters):
                existing_handler.addFilter(SecretRedactionFilter())


def get_logger(name: str) -> logging.Logger:
    """Return a logger configured with redaction filter."""
    logger = logging.getLogger(name)
    if not any(isinstance(f, SecretRedactionFilter) for f in logger.filters):
        logger.addFilter(SecretRedactionFilter())
    return logger
