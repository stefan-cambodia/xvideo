"""Safe, collision-free output file names (Windows, macOS and Linux)."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from xvideo.exceptions import OutputExistsError

# Characters invalid on Windows (a superset of macOS/Linux restrictions) and control chars.
_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WHITESPACE_RE = re.compile(r"\s+")
_UNDERSCORES_RE = re.compile(r"_{2,}")

_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# Most file systems limit a name to 255 bytes; keep room for "_123.mp4", ".part" etc.
MAX_STEM_BYTES = 200
_MAX_COLLISION_SUFFIX = 9999


def sanitize_component(
    name: str, *, fallback: str = "video", max_bytes: int = MAX_STEM_BYTES
) -> str:
    """Make ``name`` safe to use as a file name component on every major OS."""
    text = unicodedata.normalize("NFC", name)
    text = _INVALID_CHARS_RE.sub("_", text)
    text = _WHITESPACE_RE.sub("_", text)
    text = _UNDERSCORES_RE.sub("_", text)
    # Windows strips trailing dots/spaces; a leading dot hides the file on Unix.
    text = text.strip(" ._")
    text = _truncate_utf8(text, max_bytes).rstrip(" .")
    if not text:
        text = fallback
    if text.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        text = f"_{text}"
    return text


def build_stem(username: str | None, post_id: str, video_index: int | None = None) -> str:
    """Build the file stem ``USERNAME_POSTID`` (``_v2`` suffix for multi-video posts)."""
    user = sanitize_component(username or "x", fallback="x", max_bytes=60)
    stem = f"{user}_{sanitize_component(post_id, fallback='post', max_bytes=40)}"
    if video_index is not None:
        stem += f"_v{video_index}"
    return stem


class FilenameAllocator:
    """Pick output paths that never collide with existing files or with each other.

    Paths handed out during this run are remembered so two videos of the same
    batch can never be written to the same file, even with ``overwrite=True``.
    """

    def __init__(self, *, overwrite: bool = False) -> None:
        self.overwrite = overwrite
        self._reserved: set[str] = set()

    def allocate(self, directory: Path, stem: str, ext: str) -> Path:
        """Return a free path ``directory/stem.ext`` (or ``stem_1.ext``, ``stem_2.ext``…)."""
        ext = sanitize_component(ext.lstrip("."), fallback="mp4", max_bytes=10)
        for n in range(_MAX_COLLISION_SUFFIX + 1):
            name = f"{stem}.{ext}" if n == 0 else f"{stem}_{n}.{ext}"
            path = directory / name
            if self._is_reserved(path):
                continue
            if self.overwrite and not path.is_dir():
                break
            if not path.exists() and not path.is_symlink():
                break
        else:
            raise OutputExistsError(
                f"Could not find a free file name for {stem}.{ext} in {directory}."
            )
        self._reserved.add(self._key(path))
        return path

    def release(self, path: Path) -> None:
        """Forget a reservation (e.g. when the download failed before creating the file)."""
        self._reserved.discard(self._key(path))

    def _is_reserved(self, path: Path) -> bool:
        return self._key(path) in self._reserved

    @staticmethod
    def _key(path: Path) -> str:
        # casefold: macOS and Windows file systems are case-insensitive by default.
        return str(path.absolute()).casefold()


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")
