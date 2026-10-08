"""CLI tests: the Downloader is replaced by a fake, so no network is used."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from xvideo import cli
from xvideo.downloader import DownloadListener, DownloadOptions
from xvideo.exceptions import NoVideoError
from xvideo.models import DownloadResult

runner = CliRunner()


class FakeDownloader:
    instances: ClassVar[list[FakeDownloader]] = []

    def __init__(self, options: DownloadOptions, listener: DownloadListener) -> None:
        self.options = options
        self.listener = listener
        self.ffmpeg_path = Path("/usr/bin/ffmpeg")
        self.urls: list[str] = []
        FakeDownloader.instances.append(self)

    def __enter__(self) -> FakeDownloader:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def run(self, urls: Iterable[str]) -> list[DownloadResult]:
        results = []
        for url in urls:
            self.urls.append(url)
            if "fail" in url:
                result = DownloadResult.failure(url, NoVideoError())
            else:
                result = DownloadResult(
                    url=url,
                    success=True,
                    path=Path("/tmp/out/user_1.mp4"),
                    display_name="./user_1.mp4",
                    width=1280,
                    height=720,
                    duration=42.0,
                )
            self.listener.on_result(result)
            results.append(result)
        return results


@pytest.fixture(autouse=True)
def fake_downloader(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDownloader.instances = []
    monkeypatch.setattr(cli, "Downloader", FakeDownloader)


def invoke(*args: str, **kwargs: Any) -> Any:
    return runner.invoke(cli.app, list(args), **kwargs)


def test_success_exit_code_and_output(tmp_path: Path) -> None:
    result = invoke("https://x.com/user/status/1", "-o", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "X Video Downloader" in result.output
    assert "./user_1.mp4" in result.output


def test_failure_exit_code_and_message(tmp_path: Path) -> None:
    result = invoke("https://x.com/user/status/fail", "-o", str(tmp_path))
    assert result.exit_code == 1
    assert "Unable to download video" in result.output
    assert "The post does not contain an accessible video." in result.output
    assert "Traceback" not in result.output


def test_partial_failure_exit_code(tmp_path: Path) -> None:
    result = invoke("https://x.com/a/status/1", "https://x.com/b/status/fail", "-o", str(tmp_path))
    assert result.exit_code == 1
    assert "1 downloaded" in result.output and "1 failed" in result.output


def test_json_output_is_json_lines(tmp_path: Path) -> None:
    result = invoke(
        "https://x.com/a/status/1", "https://x.com/b/status/fail", "--json", "-o", str(tmp_path)
    )
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines[0]["success"] is True
    assert lines[0]["filename"] == "./user_1.mp4"
    assert lines[0]["resolution"] == "1280x720"
    assert lines[0]["duration"] == 42
    assert lines[1] == {
        "url": "https://x.com/b/status/fail",
        "success": False,
        "error": "The post does not contain an accessible video.",
        "error_type": "no_video",
    }


def test_quiet_prints_nothing_on_success(tmp_path: Path) -> None:
    result = invoke("https://x.com/user/status/1", "--quiet", "-o", str(tmp_path))
    assert result.exit_code == 0
    assert result.output == ""


def test_url_file(tmp_path: Path) -> None:
    urls = tmp_path / "urls.txt"
    urls.write_text("# comment\n\nhttps://x.com/a/status/1\n  # other\nhttps://x.com/b/status/2\n")
    result = invoke("--file", str(urls), "https://x.com/c/status/3", "-o", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert FakeDownloader.instances[0].urls == [
        "https://x.com/c/status/3",
        "https://x.com/a/status/1",
        "https://x.com/b/status/2",
    ]


def test_url_file_from_stdin(tmp_path: Path) -> None:
    result = invoke("--file", "-", "-o", str(tmp_path), input="https://x.com/a/status/1\n")
    assert result.exit_code == 0, result.output
    assert FakeDownloader.instances[0].urls == ["https://x.com/a/status/1"]


def test_options_are_passed_to_the_downloader(tmp_path: Path) -> None:
    out = tmp_path / "new" / "dir"
    result = invoke(
        "https://x.com/a/status/1",
        "-o",
        str(out),
        "-q",
        "720p",
        "--overwrite",
        "--metadata",
        "--timeout",
        "12",
        "--retries",
        "5",
    )
    assert result.exit_code == 0, result.output
    options = FakeDownloader.instances[0].options
    assert out.is_dir()  # created up front
    assert str(options.quality) == "720p"
    assert options.overwrite and options.embed_metadata
    assert options.network.timeout == 12 and options.network.retries == 5


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["https://x.com/a/status/1", "--quality", "ultra"],
        ["https://x.com/a/status/1", "--quiet", "--verbose"],
        ["https://x.com/a/status/1", "--overwrite", "--skip-existing"],
        ["https://x.com/a/status/1", "--timeout", "0"],
        ["--file", "/does/not/exist.txt"],
    ],
)
def test_usage_errors(args: list[str]) -> None:
    result = invoke(*args)
    assert result.exit_code == 2


def test_unusable_output_directory(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    result = invoke("https://x.com/a/status/1", "-o", str(blocker), "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error_type"] == "file_exists"


def test_unexpected_exception_has_no_traceback_without_verbose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: FakeDownloader, urls: Iterable[str]) -> list[DownloadResult]:
        raise RuntimeError("internal bug")

    monkeypatch.setattr(FakeDownloader, "run", boom)
    result = invoke("https://x.com/a/status/1", "-o", str(tmp_path))
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    verbose = invoke("https://x.com/a/status/1", "-o", str(tmp_path), "--verbose")
    assert "Traceback" in verbose.output


def test_version() -> None:
    result = invoke("--version")
    assert result.exit_code == 0
    assert result.output.startswith("xvideo ")
