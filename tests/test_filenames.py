from __future__ import annotations

from pathlib import Path

import pytest

from xvideo.filenames import FilenameAllocator, build_stem, sanitize_component


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("KillaXBT", "KillaXBT"),
        ('a<b>c:d"e/f\\g|h?i*j', "a_b_c_d_e_f_g_h_i_j"),
        ("tab\tnew\nline", "tab_new_line"),
        ("ctrl\x00\x1f\x7fchars", "ctrl_chars"),
        ("trailing dots...", "trailing_dots"),
        ("  spaced  out  ", "spaced_out"),
        (".hidden", "hidden"),
        ("CON", "_CON"),
        ("nul", "_nul"),
        ("com1.txt", "_com1.txt"),
        ("LPT9", "_LPT9"),
        ("CONSOLE", "CONSOLE"),
        ("", "video"),
        ("...", "video"),
        ("???", "video"),
        ("café", "café"),
    ],
)
def test_sanitize_component(name: str, expected: str) -> None:
    assert sanitize_component(name) == expected


def test_sanitize_component_truncates_by_utf8_bytes() -> None:
    result = sanitize_component("é" * 300, max_bytes=50)
    assert len(result.encode("utf-8")) <= 50
    assert result == "é" * 25


def test_build_stem() -> None:
    assert build_stem("KillaXBT", "2107623471824138680") == "KillaXBT_2107623471824138680"
    assert build_stem("user", "123", 2) == "user_123_v2"
    assert build_stem(None, "123") == "x_123"
    assert build_stem("../../etc", "123") == "etc_123"


def test_allocator_returns_plain_name_when_free(tmp_path: Path) -> None:
    allocator = FilenameAllocator()
    assert allocator.allocate(tmp_path, "user_1", "mp4") == tmp_path / "user_1.mp4"


def test_allocator_adds_numeric_suffix_on_collision(tmp_path: Path) -> None:
    (tmp_path / "user_1.mp4").write_bytes(b"x")
    (tmp_path / "user_1_1.mp4").write_bytes(b"x")
    allocator = FilenameAllocator()
    assert allocator.allocate(tmp_path, "user_1", "mp4") == tmp_path / "user_1_2.mp4"


def test_allocator_never_hands_out_the_same_path_twice(tmp_path: Path) -> None:
    allocator = FilenameAllocator()
    first = allocator.allocate(tmp_path, "user_1", "mp4")
    second = allocator.allocate(tmp_path, "user_1", "mp4")
    assert first != second
    assert second == tmp_path / "user_1_1.mp4"


def test_allocator_reservations_are_case_insensitive(tmp_path: Path) -> None:
    allocator = FilenameAllocator()
    allocator.allocate(tmp_path, "User_1", "mp4")
    assert allocator.allocate(tmp_path, "user_1", "mp4") == tmp_path / "user_1_1.mp4"


def test_allocator_overwrite_reuses_existing_name(tmp_path: Path) -> None:
    (tmp_path / "user_1.mp4").write_bytes(b"x")
    allocator = FilenameAllocator(overwrite=True)
    assert allocator.allocate(tmp_path, "user_1", "mp4") == tmp_path / "user_1.mp4"
    # ...but never overwrites a file written earlier in the same run.
    assert allocator.allocate(tmp_path, "user_1", "mp4") == tmp_path / "user_1_1.mp4"


def test_allocator_overwrite_skips_directories(tmp_path: Path) -> None:
    (tmp_path / "user_1.mp4").mkdir()
    allocator = FilenameAllocator(overwrite=True)
    assert allocator.allocate(tmp_path, "user_1", "mp4") == tmp_path / "user_1_1.mp4"


def test_allocator_release(tmp_path: Path) -> None:
    allocator = FilenameAllocator()
    path = allocator.allocate(tmp_path, "user_1", "mp4")
    allocator.release(path)
    assert allocator.allocate(tmp_path, "user_1", "mp4") == path


def test_allocator_sanitizes_extension(tmp_path: Path) -> None:
    allocator = FilenameAllocator()
    assert allocator.allocate(tmp_path, "user_1", ".mp4").name == "user_1.mp4"
