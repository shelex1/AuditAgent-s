class AntiHackerError(Exception):
    """Base exception for AntiHacker server."""


class ConfigError(AntiHackerError):
    """Raised when config is invalid or missing."""


class OpenRouterError(AntiHackerError):
    """Raised on unrecoverable OpenRouter failures."""


class QuorumLostError(AntiHackerError):
    """Raised when fewer than 3 members remain active."""


class DebateTimeoutError(AntiHackerError):
    """Raised when the global debate timeout fires."""
