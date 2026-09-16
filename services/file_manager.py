"""Filesystem support for thumbnails; no image-library dependencies.

Directories are created on demand, never at import time.
"""
from contextlib import contextmanager
from pathlib import Path
import shutil
from typing import BinaryIO, Iterator
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMP_DIR = PROJECT_ROOT / "temp"
OUTPUT_DIR = PROJECT_ROOT / "output"
THUMBNAIL_DIR = OUTPUT_DIR / "thumbnails"


def _check_directories() -> None:
    for directory in (TEMP_DIR, OUTPUT_DIR, THUMBNAIL_DIR):
        if directory.is_symlink():
            raise ValueError(f"Managed directory must not be a symbolic link: {directory.name}")
        if not directory.resolve().is_relative_to(PROJECT_ROOT.resolve()):
            raise ValueError(f"Managed directory must remain inside PROJECT_ROOT: {directory.name}")


def ensure_directories() -> None:
    """Create required directories idempotently; propagate filesystem errors."""
    _check_directories()
    for directory in (TEMP_DIR, OUTPUT_DIR, THUMBNAIL_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def generate_thumbnail_path(extension: str = "jpg") -> Path:
    """Return an unreserved UUID path; no files or directories are created."""
    if not isinstance(extension, str):
        raise TypeError("Thumbnail extension must be a string")
    extension = extension.lower().removeprefix(".")
    if extension not in {"jpg", "jpeg", "png"}:
        raise ValueError("Thumbnail extension must be jpg, jpeg, or png")
    _check_directories()
    return THUMBNAIL_DIR / f"thumbnail_{uuid4().hex}.{extension}"


@contextmanager
def _open_thumbnail_file(path: Path) -> Iterator[BinaryIO]:
    """Exclusively create an output file, removing partial writes on failure."""
    ensure_directories()
    if path.parent != THUMBNAIL_DIR:
        raise ValueError("Thumbnail destination must be directly inside THUMBNAIL_DIR")
    stream = path.open("xb")
    try:
        with stream:
            yield stream
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def cleanup_temp_files() -> None:
    """Remove TEMP_DIR contents, including subdirectories, but keep TEMP_DIR.

    TEMP_DIR is exclusively project-owned. Symlinks within it are unlinked,
    never traversed; a symlink replacing TEMP_DIR itself is rejected.
    """
    _check_directories()
    if not TEMP_DIR.exists():
        return
    for entry in TEMP_DIR.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            entry.unlink(missing_ok=True)
        else:
            shutil.rmtree(entry)
