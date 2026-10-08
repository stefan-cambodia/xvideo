"""Locating and running ffmpeg.

ffmpeg is optional: it is only used to merge separate audio/video streams
(when no progressive MP4 is available) and to embed metadata (``--metadata``).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from xvideo.exceptions import DiskFullError, FFmpegError, FFmpegNotFoundError
from xvideo.ytdlp import translate_os_error

log = logging.getLogger(__name__)

_EXE_SUFFIX = ".exe" if os.name == "nt" else ""
_DEFAULT_TIMEOUT = 600.0


def find_executable(name: str, location: Path | None = None) -> Path | None:
    """Find ``name`` (``ffmpeg`` or ``ffprobe``) in ``location`` or on ``PATH``.

    ``location`` may be the executable itself or the directory containing it.
    """
    if location is not None:
        if location.is_dir():
            candidate = location / f"{name}{_EXE_SUFFIX}"
            return candidate if candidate.is_file() else None
        if location.is_file():
            if location.stem.lower() == name:
                return location
            sibling = location.with_name(f"{name}{location.suffix}")
            return sibling if sibling.is_file() else None
        return None
    found = shutil.which(name)
    return Path(found) if found else None


def find_ffmpeg(location: Path | None = None) -> Path | None:
    return find_executable("ffmpeg", location)


def run(ffmpeg: Path, args: Sequence[str], *, timeout: float = _DEFAULT_TIMEOUT) -> None:
    """Run ffmpeg with ``args``; raise :class:`FFmpegError` with its stderr on failure."""
    command = [str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", *args]
    log.debug("Running: %s", subprocess.list2cmdline(command))
    try:
        completed = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise FFmpegNotFoundError(detail=str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"ffmpeg did not finish within {timeout:.0f} seconds.") from exc
    except OSError as exc:
        raise translate_os_error(exc) or FFmpegError(detail=str(exc)) from exc

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        if "No space left on device" in stderr:
            raise DiskFullError(detail=stderr)
        raise FFmpegError(
            f"ffmpeg failed with exit code {completed.returncode}.",
            detail=stderr[-2000:] or None,
        )


def embed_metadata(ffmpeg: Path, video: Path, tags: Mapping[str, str]) -> None:
    """Write ``tags`` into ``video`` in place, without re-encoding.

    The streams are copied into a temporary file next to the video which then
    atomically replaces the original, so the video is never left half-written.
    """
    tmp = video.with_name(f".{video.stem}.xvideo-tmp{video.suffix}")
    args: list[str] = [
        "-y",
        "-i",
        str(video),
        "-map",
        "0",
        "-dn",
        "-ignore_unknown",
        "-c",
        "copy",
        "-map_metadata",
        "0",
    ]
    for key, value in tags.items():
        args += ["-metadata", f"{key}={value}"]
    if video.suffix.lower() in (".mp4", ".m4v", ".mov"):
        # Move the index to the start of the file so players can start immediately.
        args += ["-movflags", "+faststart"]
    args.append(str(tmp))
    try:
        run(ffmpeg, args)
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise FFmpegError("ffmpeg produced an empty file while embedding metadata.")
        tmp.replace(video)
    finally:
        tmp.unlink(missing_ok=True)
