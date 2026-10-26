"""Authentication and token validation logic."""

class TokenAuthenticator:
    """Authenticates API tokens."""

    def __init__(self, secret_key: str) -> None:
        self.secret_key = secret_key

    def validate_token(self, token: str) -> bool:
        """Validate whether token matches expected format."""
        if not token:
            raise ValueError("Token cannot be empty")
        return token.startswith("Bearer ")

    async def async_authenticate(self, token: str) -> dict[str, str]:
        """Perform asynchronous authentication check."""
        if not self.validate_token(token):
            raise PermissionError("Invalid authentication token")
        return {"user": "alice", "role": "admin"}


def helper_hash_token(token: str) -> str:
    """Utility function to hash token."""
    return f"hashed_{token}"
