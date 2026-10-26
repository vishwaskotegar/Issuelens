"""Core domain exceptions for IssueLens."""


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

    def __init__(self, message: str) -> None:
        super().__init__(message, code="RATE_LIMIT_EXCEEDED", status_code=429)


class UpstreamTimeoutError(IssueLensError):
    """Raised when an upstream request times out."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="TIMEOUT", status_code=504)


class SafetyViolationError(IssueLensError):
    """Raised when an untrusted execution or safety boundary is violated."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="SAFETY_VIOLATION", status_code=400)
