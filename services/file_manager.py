"""Filesystem support for thumbnails; no image-library dependencies.

Directories are created on demand, never at import time.
"""
from contextlib import contextmanager
from pathlib import Path
import shutil
import re
import stat
import time
from typing import BinaryIO, Iterator
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMP_DIR = PROJECT_ROOT / "temp"
OUTPUT_DIR = PROJECT_ROOT / "output"
THUMBNAIL_DIR = OUTPUT_DIR / "thumbnails"
_THUMBNAIL_PREFIX = "thumbnail_"


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
    return THUMBNAIL_DIR / f"{_THUMBNAIL_PREFIX}{uuid4().hex}.{extension}"


@contextmanager
def open_thumbnail_file(path: Path) -> Iterator[BinaryIO]:
    """Exclusively create an output file, removing partial writes on failure."""
    path = _thumbnail_path(path)
    ensure_directories()
    stream = path.open("xb")
    try:
        with stream:
            yield stream
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _managed_child(path: Path, directory: Path) -> Path:
    """Accept only absolute direct children, without traversal or symlinks."""
    _check_directories()
    path = Path(path)
    if (not path.is_absolute() or ".." in path.parts
            or path.parent != directory or path.is_symlink()
            or "\\" in path.name):
        raise ValueError(f"Path must be a direct, non-symlink child of {directory.name}")
    return path


def _validate_job_id(job_id: str) -> None:
    if not isinstance(job_id, str) or re.fullmatch(r"[0-9a-f]{32}", job_id) is None:
        raise ValueError("job_id must be 32 lowercase hexadecimal characters (uuid4().hex)")


def create_temp_job_dir(job_id: str | None = None) -> Path:
    """Create/reuse temp/<job_id>; return an absolute path for session_state.

    Omit job_id for a fresh UUID. Store returned path.name as job_id in the
    caller's session and reuse it on reruns. Only reuse IDs owned by that
    session. The service keeps no per-session state or ownership registry.
    """
    if job_id is None:
        job_id = uuid4().hex
    _validate_job_id(job_id)
    path = _managed_child(TEMP_DIR / job_id, TEMP_DIR)
    ensure_directories()
    path.mkdir(exist_ok=True)
    return path


def cleanup_temp_files(job_dir: Path) -> None:
    """Remove only the specified job directory; missing jobs are a no-op.

    Supply the path returned by create_temp_job_dir for the caller's job.
    Nested symlinks are unlinked, never traversed. Managed directories must
    not be concurrently renamed/replaced by external processes during cleanup.
    """
    path = _managed_child(job_dir, TEMP_DIR)
    _validate_job_id(path.name)
    if not path.exists():
        return
    if not path.is_dir():
        raise ValueError("job_dir must be a job directory")
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        if path.exists():
            raise


def _thumbnail_path(path: Path) -> Path:
    path = _managed_child(path, THUMBNAIL_DIR)
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("Thumbnail path must have a jpg, jpeg, or png extension")
    return path


def delete_thumbnail(path: Path) -> None:
    """Delete one managed image file, idempotently; never follow symlinks.

    The caller decides when the result is no longer needed for display or
    download. This function does not delete directories or other file types.
    """
    path = _thumbnail_path(path)
    if path.exists() and not path.is_file():
        raise ValueError("Thumbnail path must refer to a regular file")
    path.unlink(missing_ok=True)


def cleanup_stale_thumbnails(max_age_seconds: int) -> int:
    """Remove generated image files older than a positive integer age in seconds.

    Only direct regular files named by generate_thumbnail_path are eligible.
    Symlinks, directories, unrelated names and fresh files are left untouched.
    The caller chooses when to run this and a retention period long enough for
    downloads; no automatic cleanup runs on imports, saves or Streamlit reruns.
    """
    if type(max_age_seconds) is not int or max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be a positive integer")
    _check_directories()
    if not THUMBNAIL_DIR.exists():
        return 0
    cutoff = time.time() - max_age_seconds
    deleted = 0
    for path in THUMBNAIL_DIR.iterdir():
        if re.fullmatch(re.escape(_THUMBNAIL_PREFIX) + r"[0-9a-f]{32}\.(jpg|jpeg|png)", path.name) is None:
            continue
        try:
            info = path.lstat()  # Never follow symbolic links.
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_mtime >= cutoff:
            continue
        path = _thumbnail_path(path)
        try:
            path.unlink()
        except FileNotFoundError:
            continue  # Another cleanup already removed this file; don't count it.
        deleted += 1
    return deleted


def save_uploaded_video(job_dir: Path, upload: BinaryIO, suffix: str = '.mp4') -> Path:
    """Stream an upload into its existing job directory with a fixed safe name."""
    job_dir = _managed_child(job_dir, TEMP_DIR)
    _validate_job_id(job_dir.name)
    if not job_dir.is_dir():
        raise ValueError('Upload job directory must already exist')
    if suffix not in {'.mp4', '.m4v', '.mov', '.avi', '.mkv', '.webm', '.mpeg', '.mpg'}:
        raise ValueError('Unsupported video filename extension')
    path = job_dir / ('uploaded_video' + suffix)
    position = upload.tell()
    upload.seek(0)
    try:
        stream = path.open('xb')
        try:
            with stream:
                shutil.copyfileobj(upload, stream, length=1024 * 1024)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    finally:
        upload.seek(position)
    return path
