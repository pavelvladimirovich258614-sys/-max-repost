"""Text formatter for converting VK posts to Telegram format."""


class TextFormatter:
    """
    Formats text from VK posts for Telegram display.

    This is a stub class - business logic will be implemented later.
    """

    def format_post(self, text: str, author: str | None = None) -> str:
        """
        Format a post for Telegram.

        Args:
            text: Original post text
            author: Optional author name

        Returns:
            Formatted text ready for Telegram
        """
        pass

    def escape_html(self, text: str) -> str:
        """
        Escape HTML special characters.

        Args:
            text: Text to escape

        Returns:
            Escaped text
        """
        pass

    def truncate(self, text: str, max_length: int = 4096) -> str:
        """
        Truncate text to maximum length.

        Args:
            text: Text to truncate
            max_length: Maximum allowed length

        Returns:
            Truncated text
        """
        pass
