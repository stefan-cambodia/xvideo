"""Download orchestration: URL → post → format selection → file on disk.

The :class:`Downloader` never prints anything; it reports progress through a
:class:`DownloadListener`, so the same code serves the rich terminal UI,
``--quiet``, ``--json`` and library use.
"""

from __future__ import annotations

import copy
import logging
import os
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from yt_dlp import YoutubeDL

from xvideo import ffmpeg
from xvideo.exceptions import (
    DiskFullError,
    ExtractionError,
    OutputExistsError,
    PermissionDeniedError,
    XVideoError,
)
from xvideo.extractor import Extractor
from xvideo.filenames import FilenameAllocator, build_stem
from xvideo.metadata import build_tags
from xvideo.models import (
    DownloadResult,
    FormatSelection,
    PostRef,
    Quality,
    SizeInfo,
    VideoInfo,
)
from xvideo.selector import select_format
from xvideo.utils import display_path, format_size, parse_post_url
from xvideo.ytdlp import NetworkOptions, base_params, translate_error, translate_os_error

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DownloadOptions:
    output_dir: Path = field(default_factory=Path)
    quality: Quality = field(default_factory=lambda: Quality("best"))
    overwrite: bool = False
    skip_existing: bool = False
    embed_metadata: bool = False
    network: NetworkOptions = field(default_factory=NetworkOptions)
    ffmpeg_location: Path | None = None


class DownloadListener:
    """Progress callbacks. Every method is a no-op; subclasses override what they need."""

    def on_url_start(self, url: str, position: int, total: int) -> None: ...

    def on_fetch_start(self, post: PostRef) -> None: ...

    def on_retry(
        self, attempt: int, max_attempts: int, error: XVideoError, delay: float
    ) -> None: ...

    def on_videos_found(self, videos: list[VideoInfo]) -> None: ...

    def on_video(self, video: VideoInfo, selection: FormatSelection, size: SizeInfo) -> None: ...

    def on_download_start(self, total_bytes: int | None) -> None: ...

    def on_progress(self, downloaded_bytes: int, total_bytes: int | None) -> None: ...

    def on_download_end(self) -> None: ...

    def on_status(self, message: str) -> None: ...

    def on_warning(self, message: str) -> None: ...

    def on_result(self, result: DownloadResult) -> None: ...


class Downloader:
    """Download the videos of X posts. Use as a context manager."""

    def __init__(self, options: DownloadOptions, listener: DownloadListener | None = None) -> None:
        self.options = options
        self.listener = listener or DownloadListener()
        self.ffmpeg_path = ffmpeg.find_ffmpeg(options.ffmpeg_location)
        self._allocator = FilenameAllocator(overwrite=options.overwrite)
        self._extractor = Extractor(options.network, on_retry=self.listener.on_retry)

    def __enter__(self) -> Self:
        self._extractor.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._extractor.__exit__(exc_type, exc, tb)

    # ------------------------------------------------------------------ public API

    def run(self, urls: Iterable[str]) -> list[DownloadResult]:
        """Download every URL; a failing URL never stops the batch."""
        jobs: list[tuple[str, PostRef | XVideoError]] = []
        seen: set[tuple[str, int | None]] = set()
        for url in urls:
            try:
                post = parse_post_url(url)
            except XVideoError as error:
                jobs.append((url, error))
                continue
            if post.key in seen:
                log.info("Skipping duplicate URL: %s", url)
                continue
            seen.add(post.key)
            jobs.append((url, post))

        results: list[DownloadResult] = []
        for position, (url, job) in enumerate(jobs, 1):
            self.listener.on_url_start(url, position, len(jobs))
            if isinstance(job, XVideoError):
                url_results = [DownloadResult.failure(url, job)]
            else:
                url_results = self.download_post(job, url=url)
            for result in url_results:
                self.listener.on_result(result)
            results.extend(url_results)
        return results

    def download_post(self, post: PostRef, *, url: str | None = None) -> list[DownloadResult]:
        """Download every video of one post; one result per video (or one failure)."""
        source_url = url or post.url
        self.listener.on_fetch_start(post)
        try:
            videos = self._extractor.extract(post)
        except Exception as exc:
            return [DownloadResult.failure(source_url, translate_error(exc), exception=exc)]
        self.listener.on_videos_found(videos)
        return [self._download_video(source_url, video) for video in videos]

    # ------------------------------------------------------------------ internals

    def _download_video(self, url: str, video: VideoInfo) -> DownloadResult:
        options = self.options
        warnings: list[str] = []
        target: Path | None = None

        def warn(message: str) -> None:
            warnings.append(message)
            self.listener.on_warning(message)

        try:
            selection = select_format(
                video.formats, options.quality, can_merge=self.ffmpeg_path is not None
            )
            directory = ensure_directory(options.output_dir)
            stem = build_stem(video.username, video.post_id, video.file_index)
            existing = directory / f"{stem}.{selection.ext}"
            if options.skip_existing and existing.is_file():
                return self._result(url, video, None, existing, warnings, skipped=True)

            if selection.fallback:
                lower = selection.video.short_side < (options.quality.height or 0)
                warn(
                    f"{options.quality} is not available; using the "
                    f"{'closest lower' if lower else 'lowest available'} quality "
                    f"({selection.resolution or 'unknown resolution'})."
                )
            size = self._size_of(selection, video.duration)
            self.listener.on_video(video, selection, size)

            target = self._allocator.allocate(directory, stem, selection.ext)
            needs_room_twice = selection.needs_merge or options.embed_metadata
            check_free_space(directory, size.bytes, factor=2 if needs_room_twice else 1)

            path = self._fetch(video, selection, target, size)
            embedded = options.embed_metadata and self._embed_metadata(video, selection, path, warn)
            return self._result(url, video, selection, path, warnings, metadata=embedded)
        except Exception as exc:
            if target is not None and not target.exists():
                self._allocator.release(target)
            error = translate_error(exc, phase="download")
            result = DownloadResult.failure(url, error, exception=exc, video=video)
            result.warnings = warnings
            return result

    def _size_of(self, selection: FormatSelection, duration: float | None) -> SizeInfo:
        total = 0
        exact = True
        for fmt in selection.formats:
            size = self._extractor.probe_size(fmt) or fmt.filesize
            if size is None:
                exact = False
                size = fmt.estimate_size(duration)
            if size is None:
                return SizeInfo(None)
            total += size
        return SizeInfo(total, exact=exact)

    def _fetch(
        self, video: VideoInfo, selection: FormatSelection, target: Path, size: SizeInfo
    ) -> Path:
        """Download ``selection`` to ``target`` with yt-dlp (resumes ``.part`` files)."""
        expected = {fmt.format_id: fmt.filesize for fmt in selection.formats}
        if len(selection.formats) == 1 and size.bytes:
            expected[selection.video.format_id] = size.bytes
        tracker = _ProgressTracker(self.listener, expected)

        params = base_params(self.options.network, ffmpeg_location=self.ffmpeg_path)
        params.update(
            {
                "format": selection.format_spec,
                # Exact output path; '%' must be escaped in yt-dlp output templates.
                "outtmpl": {"default": str(target.absolute()).replace("%", "%%")},
                "overwrites": True if self.options.overwrite else None,
                "merge_output_format": "mp4",
                "fixup": "detect_or_warn",
                "progress_hooks": [tracker.hook],
                "postprocessor_hooks": [self._postprocessor_hook],
            }
        )
        self.listener.on_download_start(size.bytes)
        try:
            with YoutubeDL(params) as ydl:
                info = ydl.process_ie_result(copy.deepcopy(video.raw), download=True)
        finally:
            self.listener.on_download_end()

        path = _downloaded_path(info) or target
        if not path.is_file() or path.stat().st_size == 0:
            raise ExtractionError(
                "The download finished but the video file is missing or empty.",
                detail=f"Expected file: {path}",
            )
        return path

    def _postprocessor_hook(self, status: dict[str, Any]) -> None:
        if status.get("status") != "started":
            return
        name = status.get("postprocessor")
        if name == "Merger":
            self.listener.on_status("Merging audio and video…")
        elif name and str(name).startswith("Fixup"):
            self.listener.on_status("Fixing the video container…")

    def _embed_metadata(
        self,
        video: VideoInfo,
        selection: FormatSelection,
        path: Path,
        warn: Callable[[str], None],
    ) -> bool:
        if self.ffmpeg_path is None:
            warn("ffmpeg was not found: the video was saved without embedded metadata.")
            return False
        self.listener.on_status("Embedding metadata…")
        try:
            ffmpeg.embed_metadata(self.ffmpeg_path, path, build_tags(video, selection))
        except Exception as exc:
            error = translate_error(exc, phase="download")
            log.debug("Metadata embedding failed", exc_info=True)
            warn(f"Metadata could not be embedded ({error.message}); the video itself is fine.")
            return False
        return True

    def _result(
        self,
        url: str,
        video: VideoInfo,
        selection: FormatSelection | None,
        path: Path,
        warnings: list[str],
        *,
        skipped: bool = False,
        metadata: bool = False,
    ) -> DownloadResult:
        return DownloadResult(
            url=url,
            success=True,
            path=path.absolute(),
            display_name=display_path(path),
            post_id=video.post_id,
            username=video.username,
            video_index=video.index,
            video_count=video.count,
            width=selection.width if selection else None,
            height=selection.height if selection else None,
            duration=video.duration,
            filesize=path.stat().st_size,
            format_id=selection.format_spec if selection else None,
            quality=str(self.options.quality),
            metadata_embedded=metadata,
            skipped=skipped,
            warnings=warnings,
        )


class _ProgressTracker:
    """Aggregate yt-dlp progress over all the files of a download (video + audio)."""

    def __init__(self, listener: DownloadListener, expected: dict[str, int | None]) -> None:
        self._listener = listener
        self._totals: dict[str, int | None] = dict(expected)
        self._done: dict[str, int] = {}

    def hook(self, status: dict[str, Any]) -> None:
        if status.get("status") not in ("downloading", "finished"):
            return
        info = status.get("info_dict") or {}
        key = str(info.get("format_id") or status.get("filename") or "")
        downloaded = int(status.get("downloaded_bytes") or 0)
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        if status.get("status") == "finished":
            total = total or downloaded
            downloaded = int(total)
        if total:
            self._totals[key] = int(total)
        self._done[key] = downloaded
        known_totals = [t for t in self._totals.values() if t]
        self._listener.on_progress(
            sum(self._done.values()), sum(known_totals) if known_totals else None
        )


def ensure_directory(path: Path) -> Path:
    """Create the output directory if needed and check that it is writable."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except FileExistsError as exc:
        raise OutputExistsError(f"The output path exists and is not a directory: {path}") from exc
    except OSError as exc:
        raise translate_os_error(exc) or PermissionDeniedError(
            f"Cannot create the output directory: {path}", detail=str(exc)
        ) from exc
    if not os.access(path, os.W_OK | os.X_OK):
        raise PermissionDeniedError(f"The output directory is not writable: {path}")
    return path


def check_free_space(directory: Path, size: int | None, *, factor: int = 1) -> None:
    """Fail early when the video obviously cannot fit on the disk."""
    if not size:
        return
    try:
        free = shutil.disk_usage(directory).free
    except OSError:
        return
    required = size * factor
    if free < required:
        raise DiskFullError(
            f"Not enough free disk space in {directory}: about {format_size(required)} "
            f"needed, {format_size(free)} available."
        )


def _downloaded_path(info: Any) -> Path | None:
    if not isinstance(info, dict):
        return None
    for download in info.get("requested_downloads") or []:
        filepath = download.get("filepath") or download.get("_filename")
        if filepath:
            return Path(filepath)
    return None
