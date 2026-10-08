from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from xvideo.exceptions import NoVideoError
from xvideo.models import DownloadResult, FormatSelection, VideoFormat, VideoInfo

from .conftest import make_format, make_video


def test_video_format_from_raw_ytdlp_dict() -> None:
    fmt = VideoFormat.from_ytdlp(
        {
            "format_id": "http-10368",
            "url": "https://video.twimg.com/ext_tw_video/1/pu/vid/avc1/1920x1080/a.mp4?tag=12",
            "tbr": 10368,
            "width": 1920,
            "height": 1080,
        }
    )
    assert fmt.protocol == "https"
    assert fmt.ext == "mp4"
    assert fmt.resolution == "1920x1080"
    assert fmt.has_video and fmt.has_audio  # unknown codecs: progressive MP4
    assert not fmt.is_audio_only


def test_video_format_from_hls_dict() -> None:
    video = VideoFormat.from_ytdlp(
        {
            "format_id": "hls-910",
            "url": "https://x/a.m3u8",
            "protocol": "m3u8_native",
            "ext": "mp4",
            "vcodec": "avc1.64001F",
            "acodec": "none",
            "width": 1280,
            "height": 720,
        }
    )
    audio = VideoFormat.from_ytdlp(
        {
            "format_id": "hls-audio",
            "url": "https://x/b.m3u8",
            "protocol": "m3u8_native",
            "vcodec": "none",
            "acodec": None,
            "tbr": "128",
        }
    )
    assert video.has_video and not video.has_audio and not video.is_http
    assert audio.is_audio_only
    assert audio.tbr == 128.0


def test_short_side_handles_portrait_and_unknown() -> None:
    assert make_format("a", 1920, 1080).short_side == 1080
    assert make_format("b", 720, 1280).short_side == 720
    assert make_format("c", None, 480).short_side == 480
    assert make_format("d").short_side == 0
    assert make_format("d").resolution is None


def test_estimate_size() -> None:
    assert make_format("a", tbr=800).estimate_size(10) == 1_000_000
    assert make_format("a", tbr=800, filesize=42).estimate_size(10) == 42
    assert make_format("a").estimate_size(10) is None
    assert make_format("a", tbr=800).estimate_size(None) is None


def test_format_selection_spec() -> None:
    video = make_format("hls-910", 1280, 720, acodec="none")
    audio = make_format("hls-audio", vcodec="none")
    assert FormatSelection(video).format_spec == "hls-910"
    merged = FormatSelection(video, audio)
    assert merged.format_spec == "hls-910+hls-audio"
    assert merged.needs_merge
    assert merged.ext == "mp4"
    assert merged.formats == (video, audio)


def test_video_info_file_index_and_date(video: VideoInfo) -> None:
    assert video.file_index is None
    assert make_video(index=2, count=3).file_index == 2
    assert make_video(index=1, count=3).file_index == 1
    assert make_video(index=2, count=1).file_index == 2  # URL ending in /video/2
    assert video.upload_datetime == datetime(2026, 10, 7, 0, 6, 8, tzinfo=UTC)
    assert make_video(timestamp=None).upload_datetime is None


def test_download_result_success_to_dict(tmp_path: Path) -> None:
    result = DownloadResult(
        url="https://x.com/user/status/123",
        success=True,
        path=tmp_path / "user_123.mp4",
        display_name="./downloads/user_123.mp4",
        post_id="123",
        username="user",
        video_index=1,
        video_count=1,
        width=1920,
        height=1080,
        duration=42.4,
        filesize=18_400_000,
        format_id="http-10368",
        quality="best",
    )
    data = result.to_dict()
    assert data["url"] == "https://x.com/user/status/123"
    assert data["success"] is True
    assert data["filename"] == "./downloads/user_123.mp4"
    assert data["resolution"] == "1920x1080"
    assert data["duration"] == 42
    assert data["metadata"] is False
    assert "error" not in data
    json.dumps(data)  # must be serialisable


def test_download_result_failure_to_dict() -> None:
    result = DownloadResult.failure("https://x.com/user/status/123", NoVideoError())
    data = result.to_dict()
    assert data == {
        "url": "https://x.com/user/status/123",
        "success": False,
        "error": "The post does not contain an accessible video.",
        "error_type": "no_video",
    }


def test_download_result_failure_with_video_context(video: VideoInfo) -> None:
    result = DownloadResult.failure("u", NoVideoError("boom"), video=video)
    assert result.to_dict()["post_id"] == "2107623471824138680"
    assert result.to_dict()["video_index"] == 1
