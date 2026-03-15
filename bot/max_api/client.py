"""Max API (VK) client for interacting with vk.com API."""

from config.settings import settings


class MaxClient:
    """
    Client for interacting with Max API (vk.com).

    This is a stub class - business logic will be implemented later.
    """

    def __init__(self, access_token: str | None = None) -> None:
        """
        Initialize MaxClient.

        Args:
            access_token: Max API access token (defaults to settings)
        """
        self.access_token = access_token or settings.max_access_token

    async def get_post(self, post_id: str) -> dict:
        """
        Get a post by ID.

        Args:
            post_id: Post identifier

        Returns:
            Post data as dictionary
        """
        pass

    async def get_group_posts(self, group_id: str, count: int = 10) -> list[dict]:
        """
        Get recent posts from a group.

        Args:
            group_id: Group identifier
            count: Number of posts to fetch

        Returns:
            List of post data
        """
        pass
