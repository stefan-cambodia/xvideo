"""Terminal and JSON reporting (implementations of :class:`DownloadListener`)."""

from __future__ import annotations

import json
import sys
from typing import TextIO

from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.traceback import Traceback

from xvideo.downloader import DownloadListener
from xvideo.exceptions import XVideoError
from xvideo.models import DownloadResult, FormatSelection, PostRef, SizeInfo, VideoInfo
from xvideo.utils import format_duration, format_size

OK = "[green]✓[/green]"
FAIL = "[red]✗[/red]"
WARN = "[yellow]![/yellow]"


class ConsoleReporter(DownloadListener):
    """Human-friendly output with a progress bar (default mode)."""

    def __init__(self, console: Console, err_console: Console, *, verbose: bool = False) -> None:
        self.console = console
        self.err_console = err_console
        self.verbose = verbose
        self._progress: Progress | None = None
        self._task: TaskID | None = None
        self._multi_video = False

    def header(self) -> None:
        self.console.print("[bold]X Video Downloader[/bold]")

    def on_url_start(self, url: str, position: int, total: int) -> None:
        self.console.print()
        if total > 1:
            self.console.print(f"[bold cyan]\\[{position}/{total}][/bold cyan] {escape(url)}")

    def on_fetch_start(self, post: PostRef) -> None:
        self.console.print("Fetching post...")

    def on_retry(self, attempt: int, max_attempts: int, error: XVideoError, delay: float) -> None:
        reason = {"timeout": "Connection timed out", "network": "Network error"}.get(
            error.code, "Server error"
        )
        self.console.print(
            f"{WARN} {reason}, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_attempts})..."
        )

    def on_videos_found(self, videos: list[VideoInfo]) -> None:
        self._multi_video = len(videos) > 1
        if self._multi_video:
            self.console.print(f"{OK} {len(videos)} videos found")

    def on_video(self, video: VideoInfo, selection: FormatSelection, size: SizeInfo) -> None:
        if self._multi_video:
            self.console.print()
            self.console.print(f"{OK} Video {video.index}/{video.count}")
        else:
            self.console.print(f"{OK} Video found")
        size_text = format_size(size.bytes)
        if size.bytes is not None and not size.exact:
            size_text = f"~{size_text}"
        self.console.print(f"  Resolution: {selection.resolution or 'unknown'}")
        self.console.print(f"  Duration: {format_duration(video.duration)}")
        self.console.print(f"  Size: {size_text}")
        if self.verbose:
            self.console.print(f"  [dim]Format: {escape(selection.format_spec)}[/dim]")

    def on_download_start(self, total_bytes: int | None) -> None:
        self.console.print()
        self._progress = Progress(
            TextColumn("Downloading"),
            BarColumn(bar_width=30),
            TaskProgressColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=self.console,
        )
        self._progress.start()
        self._task = self._progress.add_task("download", total=total_bytes)

    def on_progress(self, downloaded_bytes: int, total_bytes: int | None) -> None:
        if self._progress is not None and self._task is not None:
            total = max(total_bytes, downloaded_bytes) if total_bytes else None
            self._progress.update(self._task, completed=downloaded_bytes, total=total)

    def on_download_end(self) -> None:
        if self._progress is not None:
            self._progress.stop()
        self._progress = None
        self._task = None

    def on_status(self, message: str) -> None:
        # Post-processing starts once the transfer is over: freeze the progress bar first.
        self.on_download_end()
        self.console.print(f"  [dim]{escape(message)}[/dim]")

    def on_warning(self, message: str) -> None:
        self.console.print(f"{WARN} [yellow]{escape(message)}[/yellow]")

    def on_result(self, result: DownloadResult) -> None:
        if not result.success:
            print_error(self.err_console, result, verbose=self.verbose)
            return
        self.console.print()
        label = "Already downloaded (skipped)" if result.skipped else "Saved"
        self.console.print(f"{OK} {label}:")
        self.console.print(f"  {escape(result.display_name or str(result.path))}", soft_wrap=True)
        if result.metadata_embedded:
            self.console.print("  [dim]Metadata embedded[/dim]")

    def summary(self, results: list[DownloadResult]) -> None:
        downloaded = sum(1 for r in results if r.success and not r.skipped)
        skipped = sum(1 for r in results if r.skipped)
        failed = sum(1 for r in results if not r.success)
        parts = [f"{downloaded} downloaded"]
        if skipped:
            parts.append(f"{skipped} skipped")
        if failed:
            parts.append(f"[red]{failed} failed[/red]")
        self.console.print()
        self.console.print(f"[bold]Done:[/bold] {', '.join(parts)}")


class QuietReporter(DownloadListener):
    """``--quiet``: no output at all except a one-line message per failure (stderr)."""

    def __init__(self, err_console: Console, *, verbose: bool = False) -> None:
        self.err_console = err_console
        self.verbose = verbose

    def on_result(self, result: DownloadResult) -> None:
        if not result.success:
            message = result.error.message if result.error else "Unknown error"
            line = f"{FAIL} {escape(result.url)}: {escape(message)}"
            self.err_console.print(line, soft_wrap=True)


class JsonReporter(DownloadListener):
    """``--json``: one JSON object per line on stdout (JSON Lines), nothing else."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stdout

    def on_result(self, result: DownloadResult) -> None:
        self.stream.write(json.dumps(result.to_dict()) + "\n")
        self.stream.flush()


def print_error(
    console: Console,
    result: DownloadResult,
    *,
    verbose: bool = False,
    title: str = "Unable to download video",
) -> None:
    """Print a failed result as a readable block (traceback only when verbose)."""
    error = result.error
    console.print()
    console.print(f"{FAIL} [bold red]{escape(title)}[/bold red]")
    console.print()
    console.print("[bold]Reason:[/bold]")
    console.print(escape(error.message if error else "Unknown error"), soft_wrap=True)
    if verbose and error is not None and error.detail and error.detail != error.message:
        console.print()
        console.print("[bold]Details:[/bold]")
        console.print(escape(error.detail), soft_wrap=True)
    console.print()
    console.print("[bold]URL:[/bold]")
    console.print(escape(result.url), soft_wrap=True)
    exc = result.exception
    if verbose and exc is not None and exc.__traceback__ is not None:
        console.print()
        console.print(Traceback.from_exception(type(exc), exc, exc.__traceback__))
