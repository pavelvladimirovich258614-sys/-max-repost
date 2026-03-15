"""Max API (VK) client for interacting with platform-api.max.ru."""

import asyncio
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger

from config.settings import settings
from bot.core.rate_limiter import TokenBucketRateLimiter


# =============================================================================
# Exceptions
# =============================================================================


class MaxAPIError(Exception):
    """Base exception for Max API errors."""

    def __init__(self, message: str, status_code: int | None = None, response_data: dict | None = None) -> None:
        """
        Initialize Max API error.

        Args:
            message: Error message
            status_code: HTTP status code
            response_data: Raw response data
        """
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}


class RateLimitError(MaxAPIError):
    """Raised when rate limit is exceeded (HTTP 429)."""

    def __init__(self, message: str = "Rate limit exceeded", retry_after: int | None = None) -> None:
        """
        Initialize rate limit error.

        Args:
            message: Error message
            retry_after: Suggested retry delay in seconds
        """
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class AttachmentNotReadyError(MaxAPIError):
    """Raised when attachment is not ready for sending."""

    def __init__(self, message: str = "Attachment not ready") -> None:
        """Initialize attachment not ready error."""
        super().__init__(message, status_code=400)


class AuthenticationError(MaxAPIError):
    """Raised when authentication fails (HTTP 401)."""

    def __init__(self, message: str = "Authentication failed") -> None:
        """Initialize authentication error."""
        super().__init__(message, status_code=401)


class NotFoundError(MaxAPIError):
    """Raised when resource is not found (HTTP 404)."""

    def __init__(self, message: str = "Resource not found") -> None:
        """Initialize not found error."""
        super().__init__(message, status_code=404)


# =============================================================================
# Response Models
# =============================================================================


@dataclass
class UserProfile:
    """User profile information from /me endpoint."""

    id: str
    name: str
    username: str | None = None


@dataclass
class ChatInfo:
    """Chat/channel information from /chats endpoint."""

    id: str
    name: str
    type: str  # "channel", "chat", "dm"
    username: str | None = None


@dataclass
class SendMessageResponse:
    """Response from sending a message."""

    message_id: str
    chat_id: str
    timestamp: int | None = None


@dataclass
class UploadResponse:
    """Response from file upload initiation."""

    url: str
    token: str
    upload_token: str | None = None  # For some media types


# =============================================================================
# Max API Client
# =============================================================================


class MediaType(Enum):
    """Supported media types for upload."""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    FILE = "file"


class MaxClient:
    """
    Async HTTP client for Max API (platform-api.max.ru).

    Features:
    - Automatic retry with exponential backoff for 429 and 5xx
    - Rate limiting via token bucket algorithm
    - Two-step file upload (initiate -> upload file -> get token)
    - Automatic pause after upload for attachment processing

    Example:
        async with MaxClient() as client:
            profile = await client.get_me()
            chats = await client.get_chats()
            token = await client.upload_image("photo.jpg")
            await client.send_message(chat_id, "Hello!", attachments=[token])
    """

    BASE_URL = "https://platform-api.max.ru"

    def __init__(
        self,
        access_token: str | None = None,
        base_url: str | None = None,
        rate_limiter: TokenBucketRateLimiter | None = None,
        upload_pause: float = 2.0,
        max_retries: int = 5,
    ) -> None:
        """
        Initialize Max API client.

        Args:
            access_token: Max API access token (defaults to settings)
            base_url: API base URL (defaults to production)
            rate_limiter: Rate limiter instance (creates own if None)
            upload_pause: Seconds to pause after upload (default 2.0)
            max_retries: Maximum retry attempts for failed requests
        """
        self.access_token = access_token or settings.max_access_token
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.upload_pause = upload_pause
        self.max_retries = max_retries
        self._rate_limiter = rate_limiter
        self._owned_rate_limiter = rate_limiter is None
        self._session: aiohttp.ClientSession | None = None
        self._closed = False

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Get or create HTTP session.

        Returns:
            Active aiohttp ClientSession
        """
        if self._session is None or self._session.closed:
            headers = {
                "Authorization": self.access_token,
                "Accept": "application/json",
                "User-Agent": "max-repost/1.0",
            }
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=timeout,
            )
        return self._session

    async def _get_rate_limiter(self) -> TokenBucketRateLimiter:
        """
        Get or create rate limiter.

        Returns:
            Active rate limiter instance
        """
        if self._rate_limiter is None:
            self._rate_limiter = TokenBucketRateLimiter(
                max_rps=settings.max_rps,
            )
        return self._rate_limiter

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs: Any,
    ) -> dict:
        """
        Make HTTP request with retry logic and rate limiting.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (without base URL)
            **kwargs: Additional arguments for aiohttp

        Returns:
            Parsed JSON response

        Raises:
            MaxAPIError: On API errors
            RateLimitError: On rate limit
        """
        if self._closed:
            raise RuntimeError("Client is closed")

        url = f"{self.base_url}{endpoint}"
        rate_limiter = await self._get_rate_limiter()

        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            # Wait for rate limit token
            await rate_limiter.wait()

            session = await self._get_session()

            try:
                logger.debug(f"Max API request: {method} {endpoint} (attempt {attempt + 1})")

                async with session.request(method, url, **kwargs) as response:
                    # Parse response
                    data: Any = None
                    try:
                        data = await response.json() if response.content_length else {}
                    except aiohttp.ContentTypeError:
                        data = await response.text() if response.content_length else ""

                    # Handle status codes
                    if response.status == 200:
                        logger.debug(f"Max API success: {method} {endpoint}")
                        return data if isinstance(data, dict) else {}

                    elif response.status == 201:
                        # Created - common for uploads
                        return data if isinstance(data, dict) else {}

                    elif response.status == 202:
                        # Accepted - request processing
                        return data if isinstance(data, dict) else {}

                    elif response.status == 401:
                        raise AuthenticationError(
                            data.get("error", "Authentication failed") if isinstance(data, dict) else "Auth failed"
                        )

                    elif response.status == 404:
                        raise NotFoundError(
                            data.get("error", "Resource not found") if isinstance(data, dict) else "Not found"
                        )

                    elif response.status == 429:
                        retry_after = response.headers.get("Retry-After")
                        if retry_after:
                            retry_after_int = int(retry_after)
                        else:
                            retry_after_int = None

                        # Calculate exponential backoff
                        delay = min(0.5 * (2 ** attempt), 4.0)
                        if retry_after_int:
                            delay = max(delay, retry_after_int)

                        logger.warning(f"Rate limited, waiting {delay}s before retry")
                        await asyncio.sleep(delay)
                        last_error = RateLimitError(retry_after=retry_after_int)

                    elif 400 <= response.status < 500:
                        # Check for attachment.not.ready error
                        error_msg = data.get("error", "") if isinstance(data, dict) else ""
                        error_code = data.get("error_code", "") if isinstance(data, dict) else ""

                        if "attachment" in error_msg.lower() and "ready" in error_msg.lower():
                            raise AttachmentNotReadyError(error_msg)

                        if error_code == "attachment.not.ready":
                            raise AttachmentNotReadyError(error_msg)

                        # Other client errors
                        raise MaxAPIError(
                            error_msg or f"Client error {response.status}",
                            status_code=response.status,
                            response_data=data if isinstance(data, dict) else None,
                        )

                    else:
                        # Server errors - retry
                        delay = min(0.5 * (2 ** attempt), 4.0)
                        logger.warning(f"Server error {response.status}, retrying in {delay}s")
                        await asyncio.sleep(delay)
                        last_error = MaxAPIError(
                            f"Server error {response.status}",
                            status_code=response.status,
                        )

            except aiohttp.ClientError as e:
                delay = min(0.5 * (2 ** attempt), 4.0)
                logger.warning(f"Network error: {e}, retrying in {delay}s")
                await asyncio.sleep(delay)
                last_error = MaxAPIError(f"Network error: {e}")

        # All retries exhausted
        if last_error:
            raise last_error

        raise MaxAPIError("Request failed after all retries")

    # ========================================================================
    # API Methods
    # ========================================================================

    async def get_me(self) -> UserProfile:
        """
        Get current bot profile information.

        Returns:
            UserProfile with bot information

        Raises:
            AuthenticationError: If token is invalid
            MaxAPIError: On other API errors
        """
        data = await self._request("GET", "/me")

        return UserProfile(
            id=data.get("id", ""),
            name=data.get("name", ""),
            username=data.get("username"),
        )

    async def get_chats(self) -> list[ChatInfo]:
        """
        Get list of chats/channels accessible to the bot.

        Returns:
            List of ChatInfo objects

        Raises:
            AuthenticationError: If token is invalid
            MaxAPIError: On other API errors
        """
        data = await self._request("GET", "/chats")

        chats = data.get("chats", data.get("items", []))
        return [
            ChatInfo(
                id=chat.get("id", ""),
                name=chat.get("name", ""),
                type=chat.get("type", "chat"),
                username=chat.get("username"),
            )
            for chat in chats
        ]

    async def send_message(
        self,
        chat_id: str,
        text: str,
        attachments: list[dict] | None = None,
        format: str = "html",
    ) -> SendMessageResponse:
        """
        Send a message to a chat.

        Args:
            chat_id: Target chat ID
            text: Message text (max 4000 chars)
            attachments: List of attachment objects in format:
                         [{"type": "image", "payload": {"token": "..."}}, ...]
            format: Text format ("html" or "markdown")

        Returns:
            SendMessageResponse with sent message info

        Raises:
            MaxAPIError: On API errors
            AttachmentNotReadyError: If attachment not processed yet
        """
        if len(text) > 4000:
            logger.warning(f"Message text exceeds 4000 chars ({len(text)}), truncating")
            text = text[:4000]

        # Build request body according to Max API spec
        payload: dict[str, Any] = {
            "text": text,
        }

        if attachments:
            payload["attachments"] = attachments

        if format in ("html", "markdown"):
            payload["format"] = format

        # Use query parameter for chat_id, not in body
        endpoint = f"/messages?chat_id={chat_id}"
        data = await self._request("POST", endpoint, json=payload)

        return SendMessageResponse(
            message_id=data.get("message_id", ""),
            chat_id=data.get("chat_id", chat_id),
            timestamp=data.get("timestamp"),
        )

    async def _upload_initiate(self, media_type: MediaType) -> UploadResponse:
        """
        Initiate file upload process.

        Args:
            media_type: Type of media to upload

        Returns:
            UploadResponse with upload URL and tokens

        Raises:
            MaxAPIError: On API errors
        """
        data = await self._request("POST", f"/uploads?type={media_type.value}")

        return UploadResponse(
            url=data.get("url", ""),
            token=data.get("token", ""),
            upload_token=data.get("upload_token"),
        )

    async def _upload_file(self, url: str, file_path: str | Path | bytes) -> dict:
        """
        Upload file content to the provided URL using multipart/form-data.

        Max API spec: POST {url} with multipart/form-data, field name="data"

        Args:
            url: Upload URL from initiate step
            file_path: Path to file or file content as bytes

        Returns:
            Response JSON data (contains token for image/file types)

        Raises:
            MaxAPIError: On upload failure
        """
        # Prepare file content
        if isinstance(file_path, (str, Path)):
            file_path_str = str(file_path)
            if not os.path.exists(file_path_str):
                raise MaxAPIError(f"File not found: {file_path_str}")

            with open(file_path_str, "rb") as f:
                file_content = f.read()
        else:
            file_content = file_path

        # Upload using POST with multipart/form-data
        session = await self._get_session()

        # Prepare multipart form data
        data = aiohttp.FormData()
        data.add_field('data', file_content, filename='file')

        async with session.post(url, data=data) as response:
            if response.status not in (200, 201, 202):
                error_text = await response.text()
                raise MaxAPIError(
                    f"Upload failed with status {response.status}: {error_text}",
                    status_code=response.status,
                )

            # Parse response JSON
            try:
                response_data = await response.json()
            except aiohttp.ContentTypeError:
                response_data = {}

            return response_data if isinstance(response_data, dict) else {}

    async def upload_image(self, file_path: str | Path | bytes) -> str:
        """
        Upload an image and return its token.

        For image: token comes from step 2 (upload response)

        Args:
            file_path: Path to image file or bytes content

        Returns:
            Attachment token for use in send_message

        Raises:
            MaxAPIError: On upload failure
        """
        # Step 1: Get upload URL
        upload_info = await self._upload_initiate(MediaType.IMAGE)

        # Step 2: Upload file and get token from response
        upload_response = await self._upload_file(upload_info.url, file_path)
        token = upload_response.get("token", "")

        if not token:
            raise MaxAPIError("Upload response did not contain token")

        # Pause for attachment processing
        await asyncio.sleep(self.upload_pause)

        logger.debug(f"Image uploaded, token: {token}")
        return token

    async def upload_video(self, file_path: str | Path | bytes) -> str:
        """
        Upload a video and return its token.

        Note: For video, token comes in first response but file
        must still be uploaded to the URL.

        Args:
            file_path: Path to video file or bytes content

        Returns:
            Attachment token for use in send_message

        Raises:
            MaxAPIError: On upload failure
        """
        upload_info = await self._upload_initiate(MediaType.VIDEO)
        await self._upload_file(upload_info.url, file_path)

        # Pause for attachment processing
        await asyncio.sleep(self.upload_pause)

        logger.debug(f"Video uploaded, token: {upload_info.token}")
        return upload_info.token

    async def upload_audio(self, file_path: str | Path | bytes) -> str:
        """
        Upload an audio file and return its token.

        Args:
            file_path: Path to audio file or bytes content

        Returns:
            Attachment token for use in send_message

        Raises:
            MaxAPIError: On upload failure
        """
        upload_info = await self._upload_initiate(MediaType.AUDIO)
        await self._upload_file(upload_info.url, file_path)

        # Pause for attachment processing
        await asyncio.sleep(self.upload_pause)

        logger.debug(f"Audio uploaded, token: {upload_info.token}")
        return upload_info.token

    async def upload_file(self, file_path: str | Path | bytes) -> str:
        """
        Upload a generic file and return its token.

        For file: token comes from step 2 (upload response)

        Args:
            file_path: Path to file or bytes content

        Returns:
            Attachment token for use in send_message

        Raises:
            MaxAPIError: On upload failure
        """
        # Step 1: Get upload URL
        upload_info = await self._upload_initiate(MediaType.FILE)

        # Step 2: Upload file and get token from response
        upload_response = await self._upload_file(upload_info.url, file_path)
        token = upload_response.get("token", "")

        if not token:
            raise MaxAPIError("Upload response did not contain token")

        # Pause for attachment processing
        await asyncio.sleep(self.upload_pause)

        logger.debug(f"File uploaded, token: {token}")
        return token

    async def upload_media(self, file_path: str | Path | bytes, media_type: str) -> str:
        """
        Universal media upload method.

        Dispatches to the appropriate upload_* method based on media_type.

        Args:
            file_path: Path to file or bytes content
            media_type: Media type ("image", "video", "audio", "file")

        Returns:
            Attachment token for use in send_message

        Raises:
            MaxAPIError: On upload failure or invalid media type
        """
        media_type_lower = media_type.lower()

        if media_type_lower in ("image", "img", "photo", "picture"):
            return await self.upload_image(file_path)
        elif media_type_lower in ("video", "vid"):
            return await self.upload_video(file_path)
        elif media_type_lower in ("audio", "voice", "sound"):
            return await self.upload_audio(file_path)
        elif media_type_lower in ("file", "document", "doc"):
            return await self.upload_file(file_path)
        else:
            raise MaxAPIError(f"Unsupported media type: {media_type}")

    # ========================================================================
    # Lifecycle
    # ========================================================================

    async def close(self) -> None:
        """Close client and cleanup resources."""
        self._closed = True

        if self._session and not self._session.closed:
            await self._session.close()

        if self._owned_rate_limiter and self._rate_limiter:
            await self._rate_limiter.close()

        logger.debug("MaxClient closed")

    async def __aenter__(self) -> "MaxClient":
        """Context manager entry."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Context manager exit."""
        await self.close()
