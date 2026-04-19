from typing import Literal, Optional

ErrorKind = Literal["rate_limit", "quota_exhausted", "timeout", "upstream", "malformed", "network"]


class AntiHackerError(Exception):
    """Base exception for AntiHacker server."""


class ConfigError(AntiHackerError):
    """Raised when config is invalid or missing."""


class OpenRouterError(AntiHackerError):
    """Raised on unrecoverable OpenRouter / OpenAI-compatible API failures."""

    def __init__(self, message: str, *, kind: Optional[ErrorKind] = None) -> None:
        super().__init__(message)
        self.kind: Optional[ErrorKind] = kind


class QuorumLostError(AntiHackerError):
    """Raised when fewer than 3 members remain active."""


class DebateTimeoutError(AntiHackerError):
    """Raised when the global debate timeout fires."""
