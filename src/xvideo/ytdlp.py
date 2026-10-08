"""yt-dlp integration: shared options, logging bridge and error translation.

yt-dlp does the X-specific heavy lifting (API calls, format extraction,
resumable downloads). This module keeps every yt-dlp detail that is not
specific to extraction or downloading in one place.
"""

from __future__ import annotations

import errno
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from yt_dlp.networking.exceptions import HTTPError, SSLError, TransportError

from xvideo.exceptions import (
    DiskFullError,
    ExtractionError,
    FFmpegNotFoundError,
    HTTPStatusError,
    InvalidURLError,
    NetworkError,
    NetworkTimeoutError,
    NoVideoError,
    PermissionDeniedError,
    PostNotFoundError,
    PrivateContentError,
    RateLimitError,
    UnsupportedFormatError,
    VideoUnavailableError,
    XVideoError,
)

log = logging.getLogger("xvideo.yt_dlp")

Phase = Literal["extract", "download"]


@dataclass(frozen=True, slots=True)
class NetworkOptions:
    """Network behaviour shared by extraction and downloads."""

    timeout: float = 30.0
    retries: int = 3


def base_params(network: NetworkOptions, *, ffmpeg_location: Path | None = None) -> dict[str, Any]:
    """yt-dlp parameters common to every YoutubeDL instance we create."""
    params: dict[str, Any] = {
        "logger": YtDlpLogger(log),
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "color": "no_color",
        "socket_timeout": network.timeout,
        # Retries with exponential backoff, capped at 30 s.
        "retries": network.retries,
        "fragment_retries": network.retries,
        "file_access_retries": 3,
        "extractor_retries": network.retries,
        "retry_sleep_functions": {
            "http": _backoff,
            "fragment": _backoff,
            "extractor": _backoff,
        },
        # Resume interrupted downloads from their .part file.
        "continuedl": True,
        "noplaylist": False,
        "cachedir": False,
        "updatetime": False,
        "check_formats": False,
        # Never read cookies or credentials: only public content is supported.
        "cookiefile": None,
        "cookiesfrombrowser": None,
    }
    if ffmpeg_location is not None:
        params["ffmpeg_location"] = str(ffmpeg_location)
    return params


def _backoff(attempt: int) -> float:
    return float(min(2**attempt, 30))


class YtDlpLogger:
    """Forward yt-dlp messages to :mod:`logging` (visible with ``--verbose``)."""

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def debug(self, msg: str) -> None:
        self._log.debug(msg.removeprefix("[debug] "))

    def info(self, msg: str) -> None:
        self._log.debug(msg)

    def warning(self, msg: str) -> None:
        self._log.warning(msg.removeprefix("WARNING: "))

    def error(self, msg: str) -> None:
        # Errors are re-raised as exceptions and reported by xvideo itself.
        self._log.debug(msg.removeprefix("ERROR: "))


# --------------------------------------------------------------------------- errors

_PREFIX_RE = re.compile(r"^(?:ERROR:\s*)?(?:\[[\w:]+\]\s*)?(?:[\w-]+:\s+)?")

# (pattern, factory) checked in order against the lower-cased yt-dlp message.
_MESSAGE_RULES: list[tuple[re.Pattern[str], Callable[[str], XVideoError]]] = [
    (
        re.compile(r"no video could be found|is not a video|video #\d+ is unavailable"),
        lambda d: NoVideoError(detail=d),
    ),
    (
        re.compile(r"ffmpeg.*(?:not (?:found|installed)|could not be found)|ffprobe.*not found"),
        lambda d: FFmpegNotFoundError(detail=d),
    ),
    (
        re.compile(r"nsfw|age.?restrict|sensitive"),
        lambda d: PrivateContentError(
            "This post is marked as sensitive/age-restricted and is only visible when signed in. "
            "Only publicly accessible posts are supported.",
            detail=d,
        ),
    ),
    (
        re.compile(r"protected|not authorized|login|log in|sign in|authenticat|private"),
        lambda d: PrivateContentError(detail=d),
    ),
    (
        re.compile(r"suspended"),
        lambda d: VideoUnavailableError(
            "The account that published this post is suspended.", detail=d
        ),
    ),
    (
        re.compile(r"twitter api says:"),
        lambda d: PostNotFoundError(_x_reason(d), detail=d),
    ),
    (
        re.compile(r"tweet (?:is )?unavailable|notfound|not found|does not exist|deleted"),
        lambda d: PostNotFoundError(detail=d),
    ),
    (
        re.compile(r"geo.?restrict|not available in your (?:country|location)|withheld"),
        lambda d: VideoUnavailableError(
            "This video is not available in your country (withheld or geo-restricted).", detail=d
        ),
    ),
    (re.compile(r"rate.?limit|too many requests"), lambda d: RateLimitError(detail=d)),
    (re.compile(r"timed? ?out"), lambda d: NetworkTimeoutError(detail=d)),
    (
        re.compile(
            r"getaddrinfo|name or service not known|name resolution|nodename nor servname"
            r"|network is unreachable|connection (?:refused|reset|aborted)|no route to host"
            r"|unable to connect|failed to resolve|remote end closed"
        ),
        lambda d: NetworkError(detail=d),
    ),
    (re.compile(r"no space left|disk full|not enough space"), lambda d: DiskFullError(detail=d)),
    (
        re.compile(r"permission denied|access is denied|read-only file system"),
        lambda d: PermissionDeniedError(detail=d),
    ),
    (
        re.compile(r"\bdrm\b"),
        lambda d: UnsupportedFormatError(
            "The video is protected by DRM, which is not supported.", detail=d
        ),
    ),
    (
        re.compile(r"requested format is not available|no video formats found"),
        lambda d: UnsupportedFormatError(detail=d),
    ),
    (re.compile(r"unsupported url"), lambda d: InvalidURLError(detail=d)),
    (
        re.compile(r"guest token"),
        lambda d: ExtractionError(
            "X refused anonymous access to this post (no guest token). Try again later.", detail=d
        ),
    ),
]


def clean_message(message: str) -> str:
    """Strip yt-dlp's ``ERROR: [twitter] 123:`` prefixes from a message."""
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    return _PREFIX_RE.sub("", first_line, count=1).strip() or message.strip()


def translate_error(exc: BaseException, *, phase: Phase = "extract") -> XVideoError:
    """Convert any exception raised by yt-dlp (or the OS) into an :class:`XVideoError`."""
    if isinstance(exc, XVideoError):
        return exc
    detail = clean_message(str(exc)) or type(exc).__name__

    for cause in _iter_causes(exc):
        if isinstance(cause, XVideoError):
            return cause
        if isinstance(cause, HTTPError):
            return _from_http_status(cause.status, phase=phase, detail=detail)
        if isinstance(cause, TimeoutError):
            return NetworkTimeoutError(detail=detail)
        if isinstance(cause, SSLError):
            return NetworkError(
                "A secure (TLS) connection to X could not be established.", detail=detail
            )
        if isinstance(cause, TransportError):
            timed_out = "timed out" in str(cause).lower() or any(
                isinstance(c, TimeoutError) for c in _iter_causes(cause)
            )
            return NetworkTimeoutError(detail=detail) if timed_out else NetworkError(detail=detail)
        if isinstance(cause, OSError) and (os_error := translate_os_error(cause)) is not None:
            return os_error

    lowered = str(exc).lower()
    for pattern, factory in _MESSAGE_RULES:
        if pattern.search(lowered):
            return factory(detail)

    if isinstance(exc, ConnectionError):
        return NetworkError(detail=detail)
    return ExtractionError(detail=detail)


def translate_os_error(exc: OSError) -> XVideoError | None:
    """Map file-system errors (disk full, permission denied…) to xvideo errors."""
    where = f": {exc.filename}" if exc.filename else ""
    if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)):
        return DiskFullError(f"The disk is full{where}.", detail=str(exc))
    if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
        return PermissionDeniedError(f"Permission denied{where}.", detail=str(exc))
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return None  # handled as network errors
    if exc.errno in (errno.ENAMETOOLONG,):
        return PermissionDeniedError(f"The output path is too long{where}.", detail=str(exc))
    return None


def _from_http_status(status: int, *, phase: Phase, detail: str) -> XVideoError:
    if status == 429:
        return RateLimitError(detail=detail)
    if phase == "download":
        if status in (403, 404, 410):
            return VideoUnavailableError(
                f"The video file is no longer accessible on X's servers (HTTP {status}).",
                detail=detail,
            )
    else:
        if status in (404, 410):
            return PostNotFoundError(detail=detail)
        if status in (401, 403):
            return PrivateContentError(
                f"Access denied by X (HTTP {status}). The post may be private or restricted.",
                detail=detail,
            )
    if status >= 500:
        return HTTPStatusError(
            f"X returned a server error (HTTP {status}). Try again later.",
            status=status,
            detail=detail,
        )
    return HTTPStatusError(status=status, detail=detail)


def _iter_causes(exc: BaseException) -> Iterator[BaseException]:
    """Walk the chain of underlying causes (yt-dlp wraps errors several times)."""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        exc_info = getattr(current, "exc_info", None)
        wrapped = exc_info[1] if isinstance(exc_info, tuple) and len(exc_info) > 1 else None
        for nested in (
            wrapped,
            *(getattr(current, a, None) for a in ("cause", "__cause__", "__context__")),
        ):
            if isinstance(nested, BaseException):
                stack.append(nested)


def _x_reason(detail: str) -> str:
    """Turn ``Twitter API says: This Post was deleted…`` into a sentence for the user."""
    match = re.search(r"twitter api says:\s*(.+)", detail, re.IGNORECASE)
    reason = match.group(1).strip().rstrip(".") if match else ""
    if not reason or reason.lower() == "unknown error":
        return PostNotFoundError.default_message
    return f"X says: {reason}."
