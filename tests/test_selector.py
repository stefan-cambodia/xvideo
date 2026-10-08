from __future__ import annotations

import pytest

from xvideo.exceptions import FFmpegNotFoundError, NoVideoError, UnsupportedFormatError
from xvideo.models import Quality
from xvideo.selector import select_format

from .conftest import make_format, x_formats


def select(quality: str, *, can_merge: bool = True, formats=None):  # type: ignore[no-untyped-def]
    return select_format(formats or x_formats(), Quality.parse(quality), can_merge=can_merge)


def test_best_prefers_progressive_mp4() -> None:
    selection = select("best")
    assert selection.format_spec == "http-10368"
    assert not selection.needs_merge
    assert not selection.fallback


def test_worst() -> None:
    assert select("worst").format_spec == "http-256"


@pytest.mark.parametrize(
    ("quality", "expected", "fallback"),
    [
        ("1080p", "http-10368", False),
        ("720p", "http-2176", False),
        ("360p", "http-832", False),
        ("480p", "http-832", True),  # not available: best lower quality
        ("4k", "http-10368", True),
        ("144p", "http-256", True),  # nothing lower: lowest available
    ],
)
def test_max_resolution(quality: str, expected: str, fallback: bool) -> None:
    selection = select(quality)
    assert selection.format_spec == expected
    assert selection.fallback is fallback


def test_portrait_videos_use_short_side() -> None:
    formats = [
        make_format("http-1", 720, 1280, tbr=2000),
        make_format("http-2", 1080, 1920, tbr=8000),
        make_format("http-3", 360, 640, tbr=600),
    ]
    assert select("720p", formats=formats).format_spec == "http-1"
    assert select("best", formats=formats).format_spec == "http-2"


def test_merge_used_only_when_it_adds_resolution() -> None:
    formats = [
        make_format("http-2176", 1280, 720, tbr=2176),
        make_format("hls-3048", 1920, 1080, protocol="m3u8_native", tbr=3048, acodec="none"),
        make_format("hls-audio", protocol="m3u8_native", tbr=128, vcodec="none"),
    ]
    selection = select("best", formats=formats)
    assert selection.format_spec == "hls-3048+hls-audio"
    assert selection.needs_merge
    assert select("720p", formats=formats).format_spec == "http-2176"


def test_without_ffmpeg_falls_back_to_progressive() -> None:
    formats = [
        make_format("http-2176", 1280, 720, tbr=2176),
        make_format("hls-3048", 1920, 1080, protocol="m3u8_native", tbr=3048, acodec="none"),
        make_format("hls-audio", protocol="m3u8_native", tbr=128, vcodec="none"),
    ]
    selection = select("best", formats=formats, can_merge=False)
    assert selection.format_spec == "http-2176"


def test_without_ffmpeg_and_only_split_streams() -> None:
    formats = [
        make_format("hls-3048", 1920, 1080, protocol="m3u8_native", acodec="none"),
        make_format("hls-audio", protocol="m3u8_native", vcodec="none"),
    ]
    with pytest.raises(FFmpegNotFoundError):
        select("best", formats=formats, can_merge=False)


def test_silent_video_only_stream_needs_no_merge() -> None:
    formats = [make_format("hls-1", 1280, 720, protocol="m3u8_native", acodec="none")]
    selection = select("best", formats=formats, can_merge=False)
    assert selection.format_spec == "hls-1"


def test_no_video_formats() -> None:
    with pytest.raises(NoVideoError):
        select("best", formats=[make_format("audio", vcodec="none")])
    with pytest.raises(NoVideoError):
        select_format([], Quality.parse("best"), can_merge=True)


def test_drm_only() -> None:
    with pytest.raises(UnsupportedFormatError):
        select("best", formats=[make_format("dash-1", 1280, 720, has_drm=True)])


def test_unknown_resolution_only_used_when_nothing_else() -> None:
    formats = [make_format("http-unknown", tbr=99999), make_format("http-360", 640, 360, tbr=800)]
    assert select("best", formats=formats).format_spec == "http-360"
    assert select("worst", formats=formats).format_spec == "http-360"
    unknown = select("720p", formats=[make_format("http-unknown")])
    assert unknown.format_spec == "http-unknown"
    assert not unknown.fallback


def test_same_resolution_prefers_http_then_bitrate() -> None:
    formats = [
        make_format("hls-hi", 1280, 720, protocol="m3u8_native", tbr=5000),
        make_format("http-lo", 1280, 720, tbr=1000),
        make_format("http-hi", 1280, 720, tbr=2000),
    ]
    assert select("best", formats=formats).format_spec == "http-hi"
    assert select("worst", formats=formats).format_spec == "http-lo"
