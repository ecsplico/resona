"""Exception hierarchy for resona-cloud-stt."""


class CloudSTTError(Exception):
    """Base class for all cloud-stt errors."""


class MissingAPIKeyError(CloudSTTError):
    """Raised when the provider's API key env var is not set.

    Attributes:
        env_var: Name of the missing environment variable.
    """

    def __init__(self, env_var: str) -> None:
        self.env_var = env_var
        super().__init__(
            f"Missing API key — set the {env_var} environment variable."
        )


class ProviderHTTPError(CloudSTTError):
    """Raised when a provider returns a non-2xx HTTP response.

    Attributes:
        provider: Provider name (``deepgram``/``elevenlabs``/``openai``).
        status_code: HTTP status code returned.
        body: Response body text (provider error message).
    """

    def __init__(self, provider: str, status_code: int, body: str) -> None:
        self.provider = provider
        self.status_code = status_code
        self.body = body
        super().__init__(
            f"{provider} returned HTTP {status_code}: {body}"
        )
