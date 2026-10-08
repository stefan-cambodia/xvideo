from __future__ import annotations

import errno
import io

import pytest
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError, TransportError
from yt_dlp.utils import DownloadError, ExtractorError

from xvideo.exceptions import (
    DiskFullError,
    ExtractionError,
    FFmpegNotFoundError,
    HTTPStatusError,
    NetworkError,
    NetworkTimeoutError,
    NoVideoError,
    PermissionDeniedError,
    PostNotFoundError,
    PrivateContentError,
    RateLimitError,
    VideoUnavailableError,
    XVideoError,
)
from xvideo.ytdlp import clean_message, translate_error


def http_error(status: int) -> HTTPError:
    response = Response(io.BytesIO(b""), url="https://x.com/x", headers={}, status=status)
    return HTTPError(response)


def wrapped(cause: BaseException, message: str = "ERROR: [twitter] 1: failed") -> DownloadError:
    """Mimic yt-dlp: DownloadError carrying the original exception in exc_info."""
    try:
        raise cause
    except BaseException:
        import sys

        return DownloadError(message, sys.exc_info())


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("ERROR: [twitter] 123: No video could be found in this tweet", NoVideoError),
        ("ERROR: [twitter] 123: Media #2 is not a video", NoVideoError),
        ("ERROR: [twitter] 123: NSFW tweet requires authentication", PrivateContentError),
        (
            "ERROR: [twitter] 123: You are not authorized to view this protected tweet",
            PrivateContentError,
        ),
        ("ERROR: [twitter] 123: Requested tweet is unavailable", PostNotFoundError),
        (
            "ERROR: [twitter] 123: Twitter API says: This Post was deleted by the Post author",
            PostNotFoundError,
        ),
        (
            "ERROR: [twitter] 123: Twitter API says: This Post is from a suspended account",
            VideoUnavailableError,
        ),
        ("ERROR: unable to write data: [Errno 28] No space left on device", DiskFullError),
        ("ERROR: unable to open for writing: [Errno 13] Permission denied", PermissionDeniedError),
        ("ERROR: ffmpeg not found. Please install or provide the path", FFmpegNotFoundError),
        ("ERROR: [twitter] 123: Rate-limit exceeded", RateLimitError),
        ("ERROR: Unable to download JSON: The read operation timed out", NetworkTimeoutError),
        ("ERROR: <urlopen error [Errno -3] Temporary failure in name resolution>", NetworkError),
        ("ERROR: something nobody anticipated", ExtractionError),
    ],
)
def test_translate_by_message(message: str, expected: type[XVideoError]) -> None:
    error = translate_error(DownloadError(message))
    assert type(error) is expected
    assert error.detail  # the original message is kept for --verbose


def test_x_reason_is_surfaced() -> None:
    error = translate_error(
        DownloadError("ERROR: [twitter] 1: Twitter API says: This Post was deleted by the author")
    )
    assert error.message == "X says: This Post was deleted by the author."


@pytest.mark.parametrize(
    ("status", "phase", "expected"),
    [
        (404, "extract", PostNotFoundError),
        (403, "extract", PrivateContentError),
        (401, "extract", PrivateContentError),
        (429, "extract", RateLimitError),
        (503, "extract", HTTPStatusError),
        (418, "extract", HTTPStatusError),
        (404, "download", VideoUnavailableError),
        (403, "download", VideoUnavailableError),
    ],
)
def test_translate_http_errors(status: int, phase: str, expected: type[XVideoError]) -> None:
    error = translate_error(wrapped(http_error(status)), phase=phase)  # type: ignore[arg-type]
    assert type(error) is expected


def test_http_error_inside_extractor_error() -> None:
    try:
        try:
            raise http_error(404)
        except HTTPError as exc:
            raise ExtractorError("Unable to download JSON", cause=exc) from exc
    except ExtractorError as outer:
        error = translate_error(wrapped(outer))
    assert isinstance(error, PostNotFoundError)


def test_translate_os_errors() -> None:
    disk = OSError(errno.ENOSPC, "No space left on device", "/tmp/a.mp4")
    assert isinstance(translate_error(wrapped(disk)), DiskFullError)
    denied = PermissionError(errno.EACCES, "Permission denied", "/root/a.mp4")
    error = translate_error(wrapped(denied))
    assert isinstance(error, PermissionDeniedError)
    assert "/root/a.mp4" in error.message


def test_translate_network_errors() -> None:
    timeout = TransportError("read timed out", cause=TimeoutError("timed out"))
    assert isinstance(translate_error(wrapped(timeout)), NetworkTimeoutError)
    refused = TransportError("Connection refused", cause=ConnectionRefusedError())
    error = translate_error(wrapped(refused))
    assert type(error) is NetworkError
    assert isinstance(translate_error(TimeoutError()), NetworkTimeoutError)


def test_xvideo_errors_pass_through() -> None:
    original = NoVideoError("custom")
    assert translate_error(original) is original


def test_clean_message() -> None:
    assert clean_message("ERROR: [twitter] 123: No video") == "No video"
    assert clean_message("ERROR: [twitter:broadcast] abc: Gone") == "Gone"
    assert clean_message("plain message") == "plain message"
    assert clean_message("first line\nsecond line") == "first line"
