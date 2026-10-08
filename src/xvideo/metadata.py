"""Metadata embedded in downloaded videos (``--metadata``).

Only tags that the MP4 container supports natively are used (title, artist,
date, description, comment…), so every player keeps reading the file
normally. Details without a dedicated MP4 tag (post ID, URL, resolution,
duration) are written as ``Key: value`` lines in the ``comment`` tag.
"""

from __future__ import annotations

from xvideo.models import FormatSelection, VideoInfo
from xvideo.utils import format_duration

_MAX_TAG_LENGTH = 4000


def build_tags(video: VideoInfo, selection: FormatSelection) -> dict[str, str]:
    """Return the ffmpeg ``-metadata`` tags describing ``video``."""
    author = _author(video)
    date = video.upload_datetime
    source_url = video.webpage_url or video.post.url

    details = {
        "Author": author,
        "Post ID": video.post_id,
        "URL": source_url,
        "Date": date.strftime("%Y-%m-%d %H:%M:%S UTC") if date else None,
        "Resolution": selection.resolution,
        "Duration": format_duration(video.duration) if video.duration else None,
        "Video": f"{video.index} of {video.count}" if video.count > 1 else None,
    }
    tags = {
        "title": video.title,
        "artist": author,
        "date": date.strftime("%Y-%m-%dT%H:%M:%SZ") if date else None,
        "description": video.description,
        "synopsis": video.description,
        "comment": "\n".join(f"{k}: {v}" for k, v in details.items() if v),
    }
    return {key: _clean(value) for key, value in tags.items() if value}


def _author(video: VideoInfo) -> str | None:
    handle = video.uploader_id or video.username
    if video.uploader and handle:
        return f"{video.uploader} (@{handle})"
    if handle:
        return f"@{handle}"
    return video.uploader


def _clean(value: str) -> str:
    # NUL bytes are rejected by subprocess on every OS; keep tags reasonably short.
    text = value.replace("\x00", "").strip()
    return text[:_MAX_TAG_LENGTH]
