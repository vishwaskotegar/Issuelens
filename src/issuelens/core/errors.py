"""Core domain exceptions for IssueLens."""

from issuelens.core.logging import redact_secrets


class IssueLensError(Exception):
    """Base exception for all IssueLens domain errors."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


class ConfigurationError(IssueLensError):
    """Raised when environment or configuration is invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="CONFIG_ERROR", status_code=500)


class InvalidInputError(IssueLensError):
    """Raised when user input (such as an issue URL) fails validation."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_INPUT", status_code=400)


class ResourceNotFoundError(IssueLensError):
    """Raised when an external resource or internal entity is not found."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="NOT_FOUND", status_code=404)


class RateLimitExceededError(IssueLensError):
    """Raised when GitHub or LLM API rate limits are encountered."""

    def __init__(self, message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(message, code="RATE_LIMIT_EXCEEDED", status_code=429)
        self.retry_after_seconds = retry_after_seconds


class UpstreamTimeoutError(IssueLensError):
    """Raised when an upstream request times out."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="TIMEOUT", status_code=504)


class SafetyViolationError(IssueLensError):
    """Raised when an untrusted execution or safety boundary is violated."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="SAFETY_VIOLATION", status_code=400)


class AuthenticationError(IssueLensError):
    """Raised when client API key or authentication token is missing or invalid."""

    def __init__(self, message: str = "Invalid or missing API key") -> None:
        super().__init__(message, code="UNAUTHORIZED", status_code=401)


class LLMOutputValidationError(IssueLensError):
    """Raised when LLM response fails schema or JSON validation."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="LLM_OUTPUT_VALIDATION_ERROR", status_code=502)


MAX_PUBLIC_ERROR_CHARS = 500


def public_error_message(exc: BaseException) -> str:
    """Return an error string that is safe to persist and return to API clients.

    Domain errors carry messages we wrote, but some embed upstream exception text (for
    example LLM SDK errors), so they are secret-redacted and truncated. Any other exception
    may contain internal paths, SQL or library state, so only a generic message is kept;
    the full exception is in the server logs.
    """
    if isinstance(exc, IssueLensError):
        text = f"{exc.code}: {redact_secrets(exc.message)}"
    else:
        text = "INTERNAL_ERROR: The investigation failed unexpectedly. See server logs."
    return _truncate(text)


def diagnostic_error_message(exc: BaseException) -> str:
    """Return exception type and message, secret-redacted and truncated.

    Used for per-node tool traces and evaluation failure notes, where the detail is needed
    to debug a run but the text may still be stored or committed.
    """
    return _truncate(redact_secrets(f"{type(exc).__name__}: {exc}"))


def _truncate(text: str) -> str:
    if len(text) > MAX_PUBLIC_ERROR_CHARS:
        return text[: MAX_PUBLIC_ERROR_CHARS - 3] + "..."
    return text
