"""Fetch the videos of an X post through yt-dlp (no download)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.networking import Request

from xvideo.exceptions import (
    HTTPStatusError,
    NetworkError,
    NoVideoError,
    PostNotFoundError,
    RateLimitError,
    XVideoError,
)
from xvideo.models import PostRef, VideoFormat, VideoInfo, float_or_none, int_or_none
from xvideo.ytdlp import NetworkOptions, base_params, translate_error

log = logging.getLogger(__name__)

RetryCallback = Callable[[int, int, XVideoError, float], None]
"""Called as ``on_retry(attempt, max_attempts, error, delay_seconds)``."""

_X_HOST_SUFFIXES = ("x.com", "twitter.com", "twimg.com")
_MAX_REDIRECT_DEPTH = 2


class Extractor:
    """Extract video information from X posts.

    One instance is meant to be reused for a whole batch (it keeps a single
    yt-dlp session, so X's guest token and connections are reused).
    """

    def __init__(self, network: NetworkOptions, *, on_retry: RetryCallback | None = None) -> None:
        self._network = network
        self._on_retry = on_retry
        self._ydl: Any = None

    def __enter__(self) -> Self:
        params = base_params(self._network)
        # Return posts without video instead of failing, to tell them apart from missing posts.
        params["ignore_no_formats_error"] = True
        self._ydl = YoutubeDL(params)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._ydl is not None:
            self._ydl.close()
            self._ydl = None

    @property
    def ydl(self) -> Any:
        if self._ydl is None:
            raise RuntimeError("Extractor must be used as a context manager")
        return self._ydl

    # ------------------------------------------------------------------ extraction

    def extract(self, post: PostRef) -> list[VideoInfo]:
        """Return every video of ``post`` (usually one).

        Raises:
            XVideoError: if the post cannot be fetched or contains no video.
        """
        raw = self._extract_with_retries(post.url)
        found = list(self._video_entries(raw, depth=0))
        entries = [e for e in found if e.get("formats") or e.get("url")]
        if not entries:
            # X answers with an empty result for posts that do not exist: there is
            # then no author or date at all, unlike a real post without video.
            if not any(e.get("uploader_id") or e.get("timestamp") for e in [raw, *found]):
                raise PostNotFoundError(detail="X returned no data for this post")
            raise NoVideoError(detail="No video could be found in this post")
        count = len(entries)
        return [self._to_video(post, entry, index, count) for index, entry in enumerate(entries, 1)]

    def _extract_with_retries(self, url: str, ie_key: str | None = None) -> dict[str, Any]:
        attempts = self._network.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                info = self.ydl.extract_info(url, download=False, process=False, ie_key=ie_key)
            except Exception as exc:
                error = translate_error(exc, phase="extract")
                if attempt >= attempts or not _is_transient(error):
                    raise error from exc
                delay = float(min(2 ** (attempt - 1), 10))
                log.debug(
                    "Attempt %d/%d failed (%s); retrying in %.0fs",
                    attempt,
                    attempts,
                    error.detail or error.message,
                    delay,
                )
                if self._on_retry is not None:
                    self._on_retry(attempt, attempts, error, delay)
                time.sleep(delay)
            else:
                if not isinstance(info, dict):
                    raise NoVideoError(detail="yt-dlp returned no information")
                return info
        raise AssertionError("unreachable")

    def _video_entries(self, info: dict[str, Any], *, depth: int) -> Iterator[dict[str, Any]]:
        """Flatten playlists and follow references to other X media (e.g. broadcasts)."""
        kind = info.get("_type", "video")
        if kind in ("playlist", "multi_video", "compat_list"):
            for entry in info.get("entries") or []:
                if isinstance(entry, dict):
                    yield from self._video_entries(entry, depth=depth)
        elif kind in ("url", "url_transparent"):
            url = str(info.get("url") or "")
            if depth >= _MAX_REDIRECT_DEPTH or not _is_x_url(url):
                # Link cards to external sites (YouTube, articles…) are not X videos.
                log.debug("Ignoring non-X media reference: %s", url or "<empty>")
                return
            nested = self._extract_with_retries(url, ie_key=info.get("ie_key"))
            for entry in self._video_entries(nested, depth=depth + 1):
                for key in ("title", "description", "uploader", "uploader_id", "timestamp"):
                    if not entry.get(key) and info.get(key):
                        entry[key] = info[key]
                yield entry
        else:
            yield info

    @staticmethod
    def _to_video(post: PostRef, entry: dict[str, Any], index: int, count: int) -> VideoInfo:
        raw_formats = entry.get("formats") or ([entry] if entry.get("url") else [])
        formats = [VideoFormat.from_ytdlp(f) for f in raw_formats if isinstance(f, dict)]
        if post.media_index is not None and count == 1:
            index = post.media_index
        # Prefer the author's real handle casing ("KillaXBT") over the URL's ("killaxbt").
        handle = _str_or_none(entry.get("uploader_id"))
        username = post.username
        if handle and (username is None or username.lower() == handle.lower()):
            username = handle
        return VideoInfo(
            post=post,
            post_id=post.post_id,
            username=username,
            index=index,
            count=count,
            title=_str_or_none(entry.get("title")),
            description=_str_or_none(entry.get("description")),
            uploader=_str_or_none(entry.get("uploader")),
            uploader_id=_str_or_none(entry.get("uploader_id")),
            timestamp=int_or_none(entry.get("timestamp")),
            duration=float_or_none(entry.get("duration")),
            webpage_url=post.url,
            formats=formats,
            raw=entry,
        )

    # ------------------------------------------------------------------ size probing

    def probe_size(self, fmt: VideoFormat) -> int | None:
        """Exact size of an HTTP(S) format via a HEAD request; ``None`` if unknown.

        X's advertised bitrates are poor size estimates (often 2-3x too high),
        so asking the server is worth one small request.
        """
        if not fmt.is_http:
            return None
        try:
            request = Request(fmt.url, headers=fmt.http_headers, method="HEAD")
            with self.ydl.urlopen(request) as response:
                length = response.headers.get("Content-Length")
                return int(length) if response.status == 200 and length else None
        except Exception as exc:  # best effort only
            log.debug("Could not get the size of format %s: %s", fmt.format_id, exc)
            return None


def _is_transient(error: XVideoError) -> bool:
    if isinstance(error, RateLimitError):
        return False  # retrying quickly would only make it worse
    if isinstance(error, HTTPStatusError):
        return error.status is not None and error.status >= 500
    return isinstance(error, NetworkError)


def _is_x_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == s or host.endswith(f".{s}") for s in _X_HOST_SUFFIXES)


def _str_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
