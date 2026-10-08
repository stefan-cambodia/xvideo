from __future__ import annotations

from pathlib import Path

import pytest

from xvideo.exceptions import InvalidURLError
from xvideo.models import PostRef, Quality
from xvideo.utils import parse_post_url, parse_url_lines, read_url_file


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://x.com/KillaXBT/status/2107623471824138680",
            PostRef("KillaXBT", "2107623471824138680"),
        ),
        ("https://twitter.com/user_1/status/123", PostRef("user_1", "123")),
        ("http://www.twitter.com/user/status/123/", PostRef("user", "123")),
        ("https://mobile.x.com/user/status/123", PostRef("user", "123")),
        ("https://m.twitter.com/user/status/123", PostRef("user", "123")),
        ("x.com/user/status/123", PostRef("user", "123")),
        ("  https://x.com/user/status/123  ", PostRef("user", "123")),
        ("https://x.com/user/status/123?s=20&t=abc", PostRef("user", "123")),
        ("https://x.com/user/status/123#frag", PostRef("user", "123")),
        ("https://X.COM/user/status/123", PostRef("user", "123")),
        ("https://x.com/user/statuses/123", PostRef("user", "123")),
        ("https://x.com/i/status/123", PostRef(None, "123")),
        ("https://x.com/i/web/status/123", PostRef(None, "123")),
        ("https://twitter.com/statuses/123", PostRef(None, "123")),
        ("https://x.com/user/status/123/video/2", PostRef("user", "123", 2)),
        ("https://x.com/user/status/123/photo/1", PostRef("user", "123", 1)),
    ],
)
def test_parse_valid_urls(url: str, expected: PostRef) -> None:
    assert parse_post_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not a url",
        "https://example.com/user/status/123",
        "https://x.com.evil.com/user/status/123",
        "https://evilx.com/user/status/123",
        "ftp://x.com/user/status/123",
        "https://x.com/user",
        "https://x.com/user/status/",
        "https://x.com/user/status/abc",
        "https://x.com/user/likes",
        "https://x.com/user/status/123/video/0",
        "https://x.com/user/status/123/extra",
        "https://x.com/bad-name!/status/123",
        "https://x.com/i/spaces/1ABCDEF",
    ],
)
def test_parse_invalid_urls(url: str) -> None:
    with pytest.raises(InvalidURLError):
        parse_post_url(url)


def test_post_ref_canonical_url() -> None:
    assert PostRef("user", "1").url == "https://x.com/user/status/1"
    assert PostRef(None, "1").url == "https://x.com/i/status/1"
    assert PostRef("user", "1", 2).url == "https://x.com/user/status/1/video/2"


def test_post_ref_key_ignores_username() -> None:
    a = parse_post_url("https://x.com/User/status/1")
    b = parse_post_url("https://twitter.com/i/status/1?s=20")
    assert a.key == b.key


def test_parse_url_lines_skips_blank_lines_and_comments() -> None:
    lines = [
        "# header comment\n",
        "\n",
        "https://x.com/user1/status/123\n",
        "   # indented comment\n",
        "  https://x.com/user2/status/456  \n",
        "\t\n",
        "https://x.com/user3/status/789",
    ]
    assert parse_url_lines(lines) == [
        "https://x.com/user1/status/123",
        "https://x.com/user2/status/456",
        "https://x.com/user3/status/789",
    ]


def test_read_url_file_handles_bom_and_crlf(tmp_path: Path) -> None:
    path = tmp_path / "urls.txt"
    path.write_bytes(
        b"\xef\xbb\xbfhttps://x.com/a/status/1\r\n# comment\r\n\r\nhttps://x.com/b/status/2\r\n"
    )
    assert read_url_file(path) == ["https://x.com/a/status/1", "https://x.com/b/status/2"]


def test_read_url_file_missing(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        read_url_file(tmp_path / "missing.txt")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("best", Quality("best")),
        ("BEST", Quality("best")),
        ("worst", Quality("worst")),
        ("1080p", Quality("max", 1080)),
        ("720", Quality("max", 720)),
        (" 480P ", Quality("max", 480)),
        ("4k", Quality("max", 2160)),
    ],
)
def test_quality_parse(value: str, expected: Quality) -> None:
    assert Quality.parse(value) == expected


@pytest.mark.parametrize("value", ["", "high", "0p", "p", "1080i", "-720p", "99999p"])
def test_quality_parse_invalid(value: str) -> None:
    with pytest.raises(ValueError, match="invalid quality"):
        Quality.parse(value)


def test_quality_str() -> None:
    assert str(Quality.parse("720")) == "720p"
    assert str(Quality.parse("best")) == "best"
