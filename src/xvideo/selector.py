"""Choose the format to download according to the requested quality.

X usually offers each resolution twice: as a progressive MP4 over HTTPS
(audio + video in one file, no ffmpeg needed) and as HLS with separate audio
and video streams (needs ffmpeg to merge). Progressive MP4 is preferred; HLS
is only used when it provides a resolution not otherwise available and
ffmpeg is installed.
"""

from __future__ import annotations

from collections.abc import Sequence

from xvideo.exceptions import FFmpegNotFoundError, NoVideoError, UnsupportedFormatError
from xvideo.models import FormatSelection, Quality, VideoFormat


def select_format(
    formats: Sequence[VideoFormat], quality: Quality, *, can_merge: bool
) -> FormatSelection:
    """Select the best format matching ``quality``.

    For ``max`` qualities, the best resolution lower than or equal to the
    requested one is used; if every available resolution is higher, the
    lowest one is used and the selection is flagged as a fallback.

    Args:
        formats: available formats of the video.
        quality: requested quality.
        can_merge: whether ffmpeg is available to merge separate audio/video streams.

    Raises:
        NoVideoError: no video stream at all.
        UnsupportedFormatError: only DRM-protected or unusable streams.
        FFmpegNotFoundError: only streams that require ffmpeg, and ffmpeg is missing.
    """
    usable = [f for f in formats if f.url and not f.has_drm]
    videos = [f for f in usable if f.has_video]
    if not videos:
        if any(f.has_video for f in formats):
            raise UnsupportedFormatError(
                "The video is only available in a protected (DRM) format, which is not supported."
            )
        raise NoVideoError()

    audio = _best_audio(usable)
    candidates: list[FormatSelection] = []
    needs_ffmpeg = False
    for fmt in videos:
        if fmt.has_audio or audio is None:
            # Progressive stream, or a genuinely silent video (no audio track offered).
            candidates.append(FormatSelection(fmt))
        elif can_merge:
            candidates.append(FormatSelection(fmt, audio))
        else:
            needs_ffmpeg = True
    if not candidates:
        assert needs_ffmpeg
        raise FFmpegNotFoundError(
            "This video is only available as separate audio and video streams, "
            "which require ffmpeg to be merged. Install ffmpeg (https://ffmpeg.org) "
            "or pass --ffmpeg-location."
        )

    # Unknown resolutions (0) are only considered when no resolution is known.
    all_resolutions = sorted({c.video.short_side for c in candidates})
    resolutions = [r for r in all_resolutions if r > 0] or all_resolutions
    fallback = False
    if quality.mode == "best":
        target = resolutions[-1]
    elif quality.mode == "worst":
        target = resolutions[0]
    else:
        eligible = [r for r in resolutions if r <= (quality.height or 0)]
        target = eligible[-1] if eligible else resolutions[0]
        fallback = target not in (quality.height, 0)

    at_target = [c for c in candidates if c.video.short_side == target]
    lowest = quality.mode == "worst"
    best = min(at_target, key=lambda c: _preference(c, lowest=lowest))
    return FormatSelection(best.video, best.audio, fallback=fallback)


def _preference(selection: FormatSelection, *, lowest: bool) -> tuple[bool, bool, float]:
    """Sort key (smaller is better): no merge, then HTTP over HLS, then bitrate."""
    bitrate = selection.video.tbr or 0.0
    return (selection.needs_merge, not selection.video.is_http, bitrate if lowest else -bitrate)


def _best_audio(formats: Sequence[VideoFormat]) -> VideoFormat | None:
    audios = [f for f in formats if f.is_audio_only]
    if not audios:
        return None
    return max(audios, key=lambda f: (f.tbr or 0.0, f.is_http))
