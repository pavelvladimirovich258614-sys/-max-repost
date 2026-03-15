"""Media processor for handling images, videos, and documents."""


class MediaProcessor:
    """
    Handles downloading and processing media files from posts.

    This is a stub class - business logic will be implemented later.
    """

    async def download_media(self, url: str) -> bytes:
        """
        Download media from URL.

        Args:
            url: Media URL

        Returns:
            Media content as bytes
        """
        pass

    async def process_image(self, image_data: bytes) -> bytes:
        """
        Process/optimize an image.

        Args:
            image_data: Raw image data

        Returns:
            Processed image data
        """
        pass

    async def process_video(self, video_data: bytes) -> bytes:
        """
        Process/optimize a video.

        Args:
            video_data: Raw video data

        Returns:
            Processed video data
        """
        pass
