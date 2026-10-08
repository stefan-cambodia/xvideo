"""Small, dependency-free helpers: URL parsing, URL files and human formatting."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit

from xvideo.exceptions import InvalidURLError
from xvideo.models import PostRef

_ALLOWED_HOSTS = frozenset(
    {
        "x.com",
        "www.x.com",
        "mobile.x.com",
        "m.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
        "m.twitter.com",
    }
)

# /<user>/status/<id>, /i/status/<id>, /i/web/status/<id>, /statuses/<id>,
# optionally followed by /video/<n> or /photo/<n>.
_POST_PATH_RE = re.compile(
    r"""
    ^/
    (?:
        i/web/status
      | (?P<user>[A-Za-z0-9_]{1,50})/status(?:es)?
      | statuses
    )
    /(?P<id>\d{1,25})
    (?:/(?:video|photo)/(?P<index>[1-9]\d?))?
    /?$
    """,
    re.VERBOSE,
)


def parse_post_url(url: str) -> PostRef:
    """Validate an X post URL and return a :class:`PostRef`.

    Accepts x.com and twitter.com (with www./mobile./m. prefixes), with or
    without scheme; query strings and fragments are ignored.

    Raises:
        InvalidURLError: if the URL is not an X post URL.
    """
    candidate = url.strip()
    if not candidate:
        raise InvalidURLError("The URL is empty.")
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").lower()
    except ValueError as exc:
        raise InvalidURLError(detail=str(exc)) from exc

    if parts.scheme.lower() not in ("http", "https") or host not in _ALLOWED_HOSTS:
        raise InvalidURLError(detail=f"Unsupported host or scheme: {url!r}")

    match = _POST_PATH_RE.match(parts.path)
    if match is None:
        raise InvalidURLError(detail=f"Unsupported path: {parts.path!r}")

    user = match.group("user")
    if user is not None and user.lower() == "i":
        user = None
    index = match.group("index")
    return PostRef(
        username=user,
        post_id=match.group("id"),
        media_index=int(index) if index else None,
    )


def parse_url_lines(lines: Iterable[str]) -> list[str]:
    """Return the URLs from ``lines``, skipping blank lines and ``#`` comments."""
    urls: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            urls.append(stripped)
    return urls


def read_url_file(path: Path) -> list[str]:
    """Read URLs from a text file (``-`` reads standard input).

    Raises:
        OSError: if the file cannot be read.
    """
    if str(path) == "-":
        return parse_url_lines(sys.stdin)
    # utf-8-sig transparently strips the BOM that some Windows editors add.
    with path.open(encoding="utf-8-sig", errors="replace") as handle:
        return parse_url_lines(handle)


def format_duration(seconds: float | None) -> str:
    """Format a duration as ``MM:SS`` (or ``H:MM:SS`` above one hour)."""
    if seconds is None or seconds < 0:
        return "unknown"
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_size(num_bytes: float | None) -> str:
    """Format a byte count with decimal units (``18.4 MB``)."""
    if num_bytes is None or num_bytes < 0:
        return "unknown"
    value = float(num_bytes)
    for unit in ("B", "kB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    raise AssertionError("unreachable")


def display_path(path: Path, base: Path | None = None) -> str:
    """Return ``path`` relative to ``base`` (default: cwd) as ``./name``, else absolute."""
    base = (base or Path.cwd()).resolve()
    absolute = path.resolve()
    try:
        relative = absolute.relative_to(base)
    except ValueError:
        return str(absolute)
    return f".{os.sep}{relative}"
