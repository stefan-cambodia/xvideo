"""Data models shared across the package."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from xvideo.exceptions import XVideoError

_HTTP_PROTOCOLS = frozenset({"http", "https"})


@dataclass(frozen=True, slots=True)
class PostRef:
    """A reference to an X post, parsed from a URL."""

    username: str | None
    post_id: str
    media_index: int | None = None

    @property
    def url(self) -> str:
        """Canonical post URL (``/video/<n>`` is kept to target a single video)."""
        user = self.username or "i"
        url = f"https://x.com/{user}/status/{self.post_id}"
        if self.media_index is not None:
            url += f"/video/{self.media_index}"
        return url

    @property
    def key(self) -> tuple[str, int | None]:
        """Identity used to detect duplicate URLs in a batch."""
        return (self.post_id, self.media_index)


_QUALITY_ALIASES = {"4k": 2160, "2k": 1440, "8k": 4320}
_QUALITY_RE = re.compile(r"^(\d{2,4})p?$")


@dataclass(frozen=True, slots=True)
class Quality:
    """Requested quality: ``best``, ``worst`` or a maximum resolution (``720p``).

    Resolutions refer to the short side of the frame, so ``720p`` matches both
    1280x720 and 720x1280 (portrait) videos.
    """

    mode: Literal["best", "worst", "max"]
    height: int | None = None

    @classmethod
    def parse(cls, value: str) -> Quality:
        """Parse ``best``, ``worst``, ``1080p``, ``1080`` or ``4k``.

        Raises:
            ValueError: if the value is not a recognised quality.
        """
        text = value.strip().lower()
        if text in ("best", "worst"):
            return cls(mode=text)  # type: ignore[arg-type]
        if text in _QUALITY_ALIASES:
            return cls(mode="max", height=_QUALITY_ALIASES[text])
        match = _QUALITY_RE.match(text)
        if match and int(match.group(1)) > 0:
            return cls(mode="max", height=int(match.group(1)))
        raise ValueError(
            f"invalid quality {value!r}: use 'best', 'worst' or a resolution such as '720p'"
        )

    def __str__(self) -> str:
        return self.mode if self.mode != "max" else f"{self.height}p"


@dataclass(frozen=True, slots=True)
class VideoFormat:
    """One media format offered by X for a video."""

    format_id: str
    url: str
    protocol: str
    ext: str
    width: int | None = None
    height: int | None = None
    tbr: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    filesize: int | None = None
    has_drm: bool = False
    http_headers: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_ytdlp(cls, fmt: dict[str, Any]) -> VideoFormat:
        """Build a format from a (raw or processed) yt-dlp format dict."""
        from yt_dlp.utils import determine_ext, determine_protocol

        url = str(fmt.get("url") or "")
        return cls(
            format_id=str(fmt.get("format_id") or "unknown"),
            url=url,
            protocol=str(fmt.get("protocol") or determine_protocol(fmt)),
            ext=str(fmt.get("ext") or determine_ext(url, default_ext="mp4")),
            width=int_or_none(fmt.get("width")),
            height=int_or_none(fmt.get("height")),
            tbr=float_or_none(fmt.get("tbr")),
            vcodec=fmt.get("vcodec"),
            acodec=fmt.get("acodec"),
            filesize=int_or_none(fmt.get("filesize")),
            has_drm=bool(fmt.get("has_drm")),
            http_headers=dict(fmt.get("http_headers") or {}),
        )

    # yt-dlp uses the string "none" for "no such stream" and None for "unknown".
    # X progressive MP4 variants have unknown codecs but always carry audio.
    @property
    def has_video(self) -> bool:
        return self.vcodec != "none"

    @property
    def has_audio(self) -> bool:
        return self.acodec != "none"

    @property
    def is_audio_only(self) -> bool:
        return not self.has_video and self.has_audio

    @property
    def is_http(self) -> bool:
        return self.protocol in _HTTP_PROTOCOLS

    @property
    def short_side(self) -> int:
        """Resolution class (``1080`` for both 1920x1080 and 1080x1920); 0 if unknown."""
        dims = [d for d in (self.width, self.height) if d]
        return min(dims) if dims else 0

    @property
    def resolution(self) -> str | None:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        if self.height:
            return f"{self.height}p"
        return None

    def estimate_size(self, duration: float | None) -> int | None:
        """Exact size if known, otherwise an estimate from bitrate and duration."""
        if self.filesize:
            return self.filesize
        if self.tbr and duration:
            return int(self.tbr * 1000 / 8 * duration)
        return None


@dataclass(frozen=True, slots=True)
class FormatSelection:
    """The format(s) chosen for a download: a single stream, or video + audio to merge."""

    video: VideoFormat
    audio: VideoFormat | None = None
    fallback: bool = False
    """True when the requested resolution was unavailable and a lower/closest one was used."""

    @property
    def format_spec(self) -> str:
        """yt-dlp format selector for exactly this selection."""
        if self.audio is None:
            return self.video.format_id
        return f"{self.video.format_id}+{self.audio.format_id}"

    @property
    def needs_merge(self) -> bool:
        return self.audio is not None

    @property
    def formats(self) -> tuple[VideoFormat, ...]:
        return (self.video,) if self.audio is None else (self.video, self.audio)

    @property
    def width(self) -> int | None:
        return self.video.width

    @property
    def height(self) -> int | None:
        return self.video.height

    @property
    def resolution(self) -> str | None:
        return self.video.resolution

    @property
    def ext(self) -> str:
        return "mp4" if self.needs_merge else (self.video.ext or "mp4")


@dataclass(frozen=True, slots=True)
class SizeInfo:
    """Size of a download, flagged as exact (from the server) or estimated."""

    bytes: int | None
    exact: bool = False


@dataclass(slots=True)
class VideoInfo:
    """One downloadable video of a post (a post can contain several videos)."""

    post: PostRef
    post_id: str
    username: str | None
    index: int
    count: int
    title: str | None = None
    description: str | None = None
    uploader: str | None = None
    uploader_id: str | None = None
    timestamp: int | None = None
    duration: float | None = None
    webpage_url: str | None = None
    formats: list[VideoFormat] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    """Unprocessed yt-dlp info dict, re-used for the actual download."""

    @property
    def file_index(self) -> int | None:
        """Video number used in the file name (only for posts with several videos)."""
        return self.index if self.count > 1 or self.index > 1 else None

    @property
    def upload_datetime(self) -> datetime | None:
        if self.timestamp is None:
            return None
        return datetime.fromtimestamp(self.timestamp, tz=UTC)


@dataclass(slots=True)
class DownloadResult:
    """Outcome of downloading one video (or of a URL that failed before any video was found)."""

    url: str
    success: bool
    path: Path | None = None
    display_name: str | None = None
    post_id: str | None = None
    username: str | None = None
    video_index: int | None = None
    video_count: int | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    filesize: int | None = None
    format_id: str | None = None
    quality: str | None = None
    metadata_embedded: bool = False
    skipped: bool = False
    warnings: list[str] = field(default_factory=list)
    error: XVideoError | None = None
    exception: BaseException | None = field(default=None, repr=False)
    """Original exception (for tracebacks in verbose mode)."""

    @classmethod
    def failure(
        cls,
        url: str,
        error: XVideoError,
        *,
        exception: BaseException | None = None,
        video: VideoInfo | None = None,
    ) -> DownloadResult:
        result = cls(url=url, success=False, error=error, exception=exception or error)
        if video is not None:
            result.post_id = video.post_id
            result.username = video.username
            result.video_index = video.index
            result.video_count = video.count
        return result

    @property
    def resolution(self) -> str | None:
        if self.width and self.height:
            return f"{self.width}x{self.height}"
        return None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable representation (stable keys, used by ``--json``)."""
        data: dict[str, Any] = {"url": self.url, "success": self.success}
        if not self.success:
            data["error"] = self.error.message if self.error else "Unknown error"
            data["error_type"] = self.error.code if self.error else "error"
            for key in ("post_id", "video_index"):
                if (value := getattr(self, key)) is not None:
                    data[key] = value
            return data
        data.update(
            {
                "filename": self.display_name,
                "path": str(self.path) if self.path else None,
                "resolution": self.resolution,
                "width": self.width,
                "height": self.height,
                "duration": round(self.duration) if self.duration is not None else None,
                "filesize": self.filesize,
                "post_id": self.post_id,
                "username": self.username,
                "video_index": self.video_index,
                "video_count": self.video_count,
                "format_id": self.format_id,
                "quality": self.quality,
                "metadata": self.metadata_embedded,
                "skipped": self.skipped,
                "warnings": list(self.warnings),
            }
        )
        return data


def int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
