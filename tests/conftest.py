from __future__ import annotations

from typing import Any

import pytest

from xvideo.models import PostRef, VideoFormat, VideoInfo


def make_format(
    format_id: str,
    width: int | None = None,
    height: int | None = None,
    *,
    protocol: str = "https",
    tbr: float | None = None,
    vcodec: str | None = None,
    acodec: str | None = None,
    has_drm: bool = False,
    filesize: int | None = None,
) -> VideoFormat:
    return VideoFormat(
        format_id=format_id,
        url=f"https://video.twimg.com/{format_id}.mp4",
        protocol=protocol,
        ext="mp4",
        width=width,
        height=height,
        tbr=tbr,
        vcodec=vcodec,
        acodec=acodec,
        has_drm=has_drm,
        filesize=filesize,
    )


def x_formats() -> list[VideoFormat]:
    """Formats as X typically offers them: progressive MP4 + HLS video-only + HLS audio."""
    return [
        make_format("hls-audio-128000-Audio", protocol="m3u8_native", tbr=128, vcodec="none"),
        make_format(
            "hls-910", 1280, 720, protocol="m3u8_native", tbr=910, acodec="none", vcodec="avc1"
        ),
        make_format(
            "hls-3048", 1920, 1080, protocol="m3u8_native", tbr=3048, acodec="none", vcodec="avc1"
        ),
        make_format("http-256", 480, 270, tbr=256),
        make_format("http-832", 640, 360, tbr=832),
        make_format("http-2176", 1280, 720, tbr=2176),
        make_format("http-10368", 1920, 1080, tbr=10368),
    ]


def make_video(post: PostRef | None = None, **overrides: Any) -> VideoInfo:
    post = post or PostRef("KillaXBT", "2107623471824138680")
    values: dict[str, Any] = {
        "post": post,
        "post_id": post.post_id,
        "username": post.username,
        "index": 1,
        "count": 1,
        "title": "Killa - Coming soon... $BTC",
        "description": "Coming soon... $BTC",
        "uploader": "Killa",
        "uploader_id": "KillaXBT",
        "timestamp": 1791331568,
        "duration": 25.166,
        "webpage_url": post.url,
        "formats": x_formats(),
    }
    values.update(overrides)
    return VideoInfo(**values)


@pytest.fixture
def video() -> VideoInfo:
    return make_video()
