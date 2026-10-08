"""Orchestration tests: no network, yt-dlp extraction and transfer are faked."""

from __future__ import annotations

import errno
import shutil
import sys
from collections import namedtuple
from pathlib import Path
from typing import Any

import pytest
from yt_dlp.utils import DownloadError

from xvideo import downloader as downloader_module
from xvideo.downloader import Downloader, DownloadListener, DownloadOptions
from xvideo.exceptions import NetworkError, NoVideoError, XVideoError
from xvideo.extractor import Extractor
from xvideo.models import FormatSelection, PostRef, Quality, VideoFormat, VideoInfo
from xvideo.ytdlp import NetworkOptions

from .conftest import make_video


class FakeExtractor:
    def __init__(self, videos: dict[str, list[VideoInfo] | XVideoError]) -> None:
        self.videos = videos
        self.calls: list[str] = []

    def __enter__(self) -> FakeExtractor:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def extract(self, post: PostRef) -> list[VideoInfo]:
        self.calls.append(post.post_id)
        value = self.videos[post.post_id]
        if isinstance(value, XVideoError):
            raise value
        return value

    def probe_size(self, fmt: VideoFormat) -> int | None:
        return 1234


class RecordingListener(DownloadListener):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.warnings: list[str] = []

    def on_url_start(self, url: str, position: int, total: int) -> None:
        self.events.append(f"start {position}/{total}")

    def on_video(self, video: VideoInfo, selection: FormatSelection, size: Any) -> None:
        self.events.append(f"video {selection.format_spec} {size.bytes}")

    def on_warning(self, message: str) -> None:
        self.warnings.append(message)


def make_downloader(
    tmp_path: Path,
    videos: dict[str, list[VideoInfo] | XVideoError],
    *,
    listener: DownloadListener | None = None,
    fetch_error: BaseException | None = None,
    ffmpeg: Path | None = None,
    **options: Any,
) -> Downloader:
    opts = DownloadOptions(output_dir=tmp_path / "out", **options)
    downloader = Downloader(opts, listener)
    downloader._extractor = FakeExtractor(videos)  # type: ignore[assignment]
    downloader.ffmpeg_path = ffmpeg

    def fake_fetch(video: VideoInfo, selection: Any, target: Path, size: Any) -> Path:
        if fetch_error is not None:
            raise fetch_error
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"video-data")
        return target

    downloader._fetch = fake_fetch  # type: ignore[method-assign]
    return downloader


def post_video(user: str, post_id: str, **kwargs: Any) -> VideoInfo:
    return make_video(PostRef(user, post_id), uploader_id=user, **kwargs)


def test_single_download(tmp_path: Path) -> None:
    listener = RecordingListener()
    downloader = make_downloader(tmp_path, {"1": [post_video("alice", "1")]}, listener=listener)
    results = downloader.run(["https://x.com/alice/status/1"])

    assert len(results) == 1
    result = results[0]
    assert result.success
    assert result.path == (tmp_path / "out" / "alice_1.mp4").absolute()
    assert result.path.read_bytes() == b"video-data"
    assert (result.width, result.height) == (1920, 1080)
    assert result.format_id == "http-10368"
    assert listener.events == ["start 1/1", "video http-10368 1234"]


def test_batch_continues_after_failures(tmp_path: Path) -> None:
    downloader = make_downloader(
        tmp_path,
        {"1": NoVideoError(), "2": NetworkError(), "3": [post_video("carol", "3")]},
    )
    results = downloader.run(
        [
            "https://example.com/not-x",
            "https://x.com/alice/status/1",
            "https://x.com/bob/status/2",
            "https://x.com/carol/status/3",
        ]
    )
    assert [r.success for r in results] == [False, False, False, True]
    assert [r.error.code for r in results if r.error] == ["invalid_url", "no_video", "network"]


def test_duplicate_urls_are_downloaded_once(tmp_path: Path) -> None:
    downloader = make_downloader(tmp_path, {"1": [post_video("alice", "1")]})
    results = downloader.run(
        ["https://x.com/alice/status/1", "https://twitter.com/Alice/status/1?s=20"]
    )
    assert len(results) == 1
    assert downloader._extractor.calls == ["1"]  # type: ignore[attr-defined]


def test_existing_file_gets_a_suffix(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "alice_1.mp4").write_bytes(b"old")
    downloader = make_downloader(tmp_path, {"1": [post_video("alice", "1")]})
    result = downloader.run(["https://x.com/alice/status/1"])[0]
    assert result.path is not None and result.path.name == "alice_1_1.mp4"
    assert (tmp_path / "out" / "alice_1.mp4").read_bytes() == b"old"


def test_overwrite(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "alice_1.mp4").write_bytes(b"old")
    downloader = make_downloader(tmp_path, {"1": [post_video("alice", "1")]}, overwrite=True)
    result = downloader.run(["https://x.com/alice/status/1"])[0]
    assert result.path is not None and result.path.name == "alice_1.mp4"
    assert result.path.read_bytes() == b"video-data"


def test_skip_existing(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "alice_1.mp4").write_bytes(b"old")
    downloader = make_downloader(tmp_path, {"1": [post_video("alice", "1")]}, skip_existing=True)
    result = downloader.run(["https://x.com/alice/status/1"])[0]
    assert result.success and result.skipped
    assert result.path is not None and result.path.read_bytes() == b"old"


def test_multi_video_post(tmp_path: Path) -> None:
    videos = [post_video("alice", "1", index=i, count=2) for i in (1, 2)]
    downloader = make_downloader(tmp_path, {"1": videos})
    results = downloader.run(["https://x.com/alice/status/1"])
    assert [r.path.name for r in results if r.path] == ["alice_1_v1.mp4", "alice_1_v2.mp4"]
    assert [r.video_count for r in results] == [2, 2]


def test_quality_fallback_warns(tmp_path: Path) -> None:
    listener = RecordingListener()
    downloader = make_downloader(
        tmp_path,
        {"1": [post_video("alice", "1")]},
        listener=listener,
        quality=Quality.parse("480p"),
    )
    result = downloader.run(["https://x.com/alice/status/1"])[0]
    assert result.success
    assert result.height == 360
    assert result.warnings and "480p is not available" in result.warnings[0]
    assert listener.warnings == result.warnings


def test_metadata_without_ffmpeg_is_a_warning(tmp_path: Path) -> None:
    downloader = make_downloader(
        tmp_path, {"1": [post_video("alice", "1")]}, embed_metadata=True, ffmpeg=None
    )
    result = downloader.run(["https://x.com/alice/status/1"])[0]
    assert result.success
    assert not result.metadata_embedded
    assert any("ffmpeg" in w for w in result.warnings)


def test_metadata_embedded_with_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path, dict[str, str]]] = []
    monkeypatch.setattr(
        downloader_module.ffmpeg,
        "embed_metadata",
        lambda exe, path, tags: calls.append((path, tags)),
    )
    downloader = make_downloader(
        tmp_path,
        {"1": [post_video("alice", "1")]},
        embed_metadata=True,
        ffmpeg=Path("/usr/bin/ffmpeg"),
    )
    result = downloader.run(["https://x.com/alice/status/1"])[0]
    assert result.metadata_embedded
    assert calls and calls[0][1]["artist"] == "Killa (@alice)"


def test_disk_full_detected_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(100, 100, 10))
    downloader = make_downloader(tmp_path, {"1": [post_video("alice", "1")]})
    result = downloader.run(["https://x.com/alice/status/1"])[0]
    assert not result.success
    assert result.error is not None and result.error.code == "disk_full"


def test_disk_full_during_download(tmp_path: Path) -> None:
    try:
        raise OSError(errno.ENOSPC, "No space left on device")
    except OSError:
        error = DownloadError("ERROR: unable to write data", sys.exc_info())
    downloader = make_downloader(tmp_path, {"1": [post_video("alice", "1")]}, fetch_error=error)
    result = downloader.run(["https://x.com/alice/status/1"])[0]
    assert result.error is not None and result.error.code == "disk_full"
    # The name reserved for the failed download is free again.
    assert downloader._allocator.allocate(tmp_path / "out", "alice_1", "mp4").name == "alice_1.mp4"


def test_output_path_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "out").write_text("not a directory")
    downloader = make_downloader(tmp_path, {"1": [post_video("alice", "1")]})
    result = downloader.run(["https://x.com/alice/status/1"])[0]
    assert result.error is not None and result.error.code == "file_exists"


# --------------------------------------------------------------------------- extractor


class FakeYDL:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def extract_info(self, url: str, **kwargs: Any) -> Any:
        self.calls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def make_extractor(responses: list[Any], retries: int = 2) -> Extractor:
    extractor = Extractor(NetworkOptions(retries=retries))
    extractor._ydl = FakeYDL(responses)
    return extractor


RAW_FORMAT = {
    "format_id": "http-832",
    "url": "https://video.twimg.com/a/640x360/a.mp4",
    "width": 640,
    "height": 360,
    "tbr": 832,
}


def test_extractor_single_video_uses_real_handle_casing() -> None:
    extractor = make_extractor(
        [{"id": "9", "uploader_id": "KillaXBT", "timestamp": 1, "formats": [RAW_FORMAT]}]
    )
    videos = extractor.extract(PostRef("killaxbt", "1"))
    assert len(videos) == 1
    assert videos[0].username == "KillaXBT"
    assert videos[0].formats[0].resolution == "640x360"


def test_extractor_keeps_url_username_for_other_authors() -> None:
    extractor = make_extractor([{"uploader_id": "someoneelse", "formats": [RAW_FORMAT]}])
    assert extractor.extract(PostRef("alice", "1"))[0].username == "alice"
    extractor = make_extractor([{"uploader_id": "someoneelse", "formats": [RAW_FORMAT]}])
    assert extractor.extract(PostRef(None, "1"))[0].username == "someoneelse"


def test_extractor_playlist_and_external_links() -> None:
    playlist = {
        "_type": "playlist",
        "entries": [
            {"uploader_id": "a", "formats": [RAW_FORMAT]},
            {"_type": "url", "url": "https://www.youtube.com/watch?v=x"},
            {"uploader_id": "a", "formats": [RAW_FORMAT]},
        ],
    }
    videos = make_extractor([playlist]).extract(PostRef("a", "1"))
    assert [(v.index, v.count) for v in videos] == [(1, 2), (2, 2)]


def test_extractor_follows_x_references() -> None:
    reference = {
        "_type": "url",
        "url": "https://x.com/i/broadcasts/1abc",
        "uploader": "A",
        "uploader_id": "a",
    }
    broadcast = {"title": "Live", "formats": [RAW_FORMAT]}
    videos = make_extractor([reference, broadcast]).extract(PostRef("a", "1"))
    assert videos[0].uploader == "A"
    assert videos[0].title == "Live"


def test_extractor_post_without_video() -> None:
    extractor = make_extractor([{"uploader_id": "jack", "timestamp": 1, "formats": []}])
    with pytest.raises(NoVideoError):
        extractor.extract(PostRef("jack", "20"))


def test_extractor_missing_post() -> None:
    from xvideo.exceptions import PostNotFoundError

    extractor = make_extractor([{"id": "1", "title": "", "formats": []}])
    with pytest.raises(PostNotFoundError):
        extractor.extract(PostRef("jack", "1"))


def test_extractor_retries_transient_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("xvideo.extractor.time.sleep", lambda s: None)
    retries: list[int] = []
    extractor = make_extractor(
        [DownloadError("ERROR: Connection refused"), {"uploader_id": "a", "formats": [RAW_FORMAT]}]
    )
    extractor._on_retry = lambda attempt, total, error, delay: retries.append(attempt)
    assert extractor.extract(PostRef("a", "1"))
    assert retries == [1]


def test_extractor_does_not_retry_permanent_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("xvideo.extractor.time.sleep", lambda s: None)
    extractor = make_extractor(
        [DownloadError("ERROR: [twitter] 1: Requested tweet is unavailable")]
    )
    from xvideo.exceptions import PostNotFoundError

    with pytest.raises(PostNotFoundError):
        extractor.extract(PostRef("a", "1"))
    assert len(extractor._ydl.calls) == 1


def test_extractor_gives_up_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("xvideo.extractor.time.sleep", lambda s: None)
    extractor = make_extractor([DownloadError("ERROR: timed out")] * 3, retries=2)
    from xvideo.exceptions import NetworkTimeoutError

    with pytest.raises(NetworkTimeoutError):
        extractor.extract(PostRef("a", "1"))
    assert len(extractor._ydl.calls) == 3
