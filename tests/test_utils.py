from __future__ import annotations

import os
from pathlib import Path

import pytest

from xvideo.metadata import build_tags
from xvideo.models import FormatSelection, VideoInfo
from xvideo.utils import display_path, format_duration, format_size

from .conftest import make_format, make_video


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00"),
        (42, "00:42"),
        (25.6, "00:26"),
        (61, "01:01"),
        (3725, "1:02:05"),
        (None, "unknown"),
        (-1, "unknown"),
    ],
)
def test_format_duration(seconds: float | None, expected: str) -> None:
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (0, "0 B"),
        (999, "999 B"),
        (1000, "1.0 kB"),
        (18_400_000, "18.4 MB"),
        (2_500_000_000, "2.5 GB"),
        (None, "unknown"),
    ],
)
def test_format_size(size: int | None, expected: str) -> None:
    assert format_size(size) == expected


def test_display_path(tmp_path: Path) -> None:
    target = tmp_path / "downloads" / "a.mp4"
    assert display_path(target, tmp_path) == f".{os.sep}downloads{os.sep}a.mp4"
    assert display_path(Path("/elsewhere/a.mp4"), tmp_path) == str(
        Path("/elsewhere/a.mp4").resolve()
    )


def test_build_tags(video: VideoInfo) -> None:
    selection = FormatSelection(make_format("http-10368", 1920, 1080))
    tags = build_tags(video, selection)
    assert tags["title"] == "Killa - Coming soon... $BTC"
    assert tags["artist"] == "Killa (@KillaXBT)"
    assert tags["date"] == "2026-10-07T00:06:08Z"
    assert tags["description"] == "Coming soon... $BTC"
    comment = tags["comment"]
    assert "Post ID: 2107623471824138680" in comment
    assert "URL: https://x.com/KillaXBT/status/2107623471824138680" in comment
    assert "Resolution: 1920x1080" in comment
    assert "Duration: 00:25" in comment


def test_build_tags_skips_missing_values() -> None:
    video = make_video(title=None, description=None, uploader=None, timestamp=None, duration=None)
    tags = build_tags(video, FormatSelection(make_format("http-1")))
    assert set(tags) == {"artist", "comment"}
    assert tags["artist"] == "@KillaXBT"
    assert all("\x00" not in v for v in tags.values())
