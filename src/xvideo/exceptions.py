"""Exception hierarchy.

Every error that can reach the user is an :class:`XVideoError` carrying a
human-readable ``message`` (always shown) and an optional technical ``detail``
(shown only in verbose mode). ``code`` is a stable identifier exposed in the
JSON output so scripts can react to specific failures.
"""

from __future__ import annotations


class XVideoError(Exception):
    """Base class for all expected xvideo errors."""

    code = "error"
    default_message = "An unexpected error occurred."

    def __init__(self, message: str | None = None, *, detail: str | None = None) -> None:
        self.message = message or self.default_message
        self.detail = detail
        super().__init__(self.message)


class InvalidURLError(XVideoError):
    code = "invalid_url"
    default_message = (
        "This is not a valid X post URL. Expected something like "
        "https://x.com/<username>/status/<post id>."
    )


class PostNotFoundError(XVideoError):
    code = "not_found"
    default_message = "The post does not exist, has been deleted or is unavailable."


class NoVideoError(XVideoError):
    code = "no_video"
    default_message = "The post does not contain an accessible video."


class PrivateContentError(XVideoError):
    code = "private"
    default_message = (
        "This post is private, protected or requires signing in. "
        "Only publicly accessible posts are supported."
    )


class VideoUnavailableError(XVideoError):
    code = "unavailable"
    default_message = "The video is not accessible (removed, withheld or restricted)."


class NetworkError(XVideoError):
    code = "network"
    default_message = "Network error: unable to reach X. Check your internet connection."


class NetworkTimeoutError(NetworkError):
    code = "timeout"
    default_message = "The connection timed out. Try again or increase --timeout."


class HTTPStatusError(XVideoError):
    code = "http_error"

    def __init__(
        self, message: str | None = None, *, status: int | None = None, detail: str | None = None
    ) -> None:
        self.status = status
        if message is None:
            message = f"X returned an HTTP error ({status})." if status else "HTTP error."
        super().__init__(message, detail=detail)


class RateLimitError(HTTPStatusError):
    code = "rate_limited"

    def __init__(self, message: str | None = None, *, detail: str | None = None) -> None:
        super().__init__(
            message or "X is rate limiting requests (HTTP 429). Wait a few minutes and try again.",
            status=429,
            detail=detail,
        )


class FFmpegNotFoundError(XVideoError):
    code = "ffmpeg_missing"
    default_message = (
        "ffmpeg is required for this video but was not found. "
        "Install it (https://ffmpeg.org) or pass --ffmpeg-location."
    )


class FFmpegError(XVideoError):
    code = "ffmpeg_failed"
    default_message = "ffmpeg failed to process the video."


class UnsupportedFormatError(XVideoError):
    code = "unsupported_format"
    default_message = "The video is only available in a format that is not supported."


class DiskFullError(XVideoError):
    code = "disk_full"
    default_message = "Not enough free disk space to save the video."


class PermissionDeniedError(XVideoError):
    code = "permission_denied"
    default_message = "Permission denied while writing the output file."


class OutputExistsError(XVideoError):
    code = "file_exists"
    default_message = "The output file already exists."


class ExtractionError(XVideoError):
    code = "extraction_failed"
    default_message = (
        "Unable to extract the video information from X. If the problem persists, "
        "try updating yt-dlp (pip install -U yt-dlp)."
    )
