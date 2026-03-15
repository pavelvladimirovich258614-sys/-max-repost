"""Max API (VK) client module."""

from .client import (
    MaxClient,
    MaxAPIError,
    RateLimitError,
    AttachmentNotReadyError,
    AuthenticationError,
    NotFoundError,
    UserProfile,
    ChatInfo,
    SendMessageResponse,
)

__all__ = [
    "MaxClient",
    "MaxAPIError",
    "RateLimitError",
    "AttachmentNotReadyError",
    "AuthenticationError",
    "NotFoundError",
    "UserProfile",
    "ChatInfo",
    "SendMessageResponse",
]
