"""Exception classes for LLM provider operations and natural language SQL generation."""

class LLMBaseException(Exception):
    """Base exception for all LLM-related operations."""
    pass


class LLMConfigurationError(LLMBaseException):
    """Raised when the LLM provider is misconfigured (e.g. missing API key)."""
    pass


class LLMProviderError(LLMBaseException):
    """Raised when the external LLM provider encounters an error."""
    pass


class LLMTimeoutError(LLMBaseException):
    """Raised when the LLM API call times out."""
    pass


class LLMResponseValidationError(LLMBaseException):
    """Raised when the LLM output cannot be parsed or validated against the response schema."""
    pass
