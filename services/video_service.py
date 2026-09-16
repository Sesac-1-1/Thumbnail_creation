"""Video metadata and BGR sampling, with one capture per public call.

Reuse get_video_info() in extract_frames(video_info=info). The latter also
works standalone without a separate metadata call. For reports, keep the
requested_sample_count and use actual_sample_count=len(frames); divide the
actual count by info['frame_count'] for the sampling ratio. Never substitute
requested counts for successful extraction counts.
"""
from collections.abc import Mapping
from contextlib import contextmanager
import math
import hashlib
from numbers import Real
from pathlib import Path
from typing import Any, Iterator

import cv2
import numpy as np


class VideoServiceError(RuntimeError):
    """Raised when a video cannot be opened or sampled."""


@contextmanager
def _open_video(path: Path) -> Iterator[Any]:
    if not path.is_file():
        raise VideoServiceError(f"Video file does not exist: {path.name}")
    capture = None
    try:
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise VideoServiceError(f"Unable to open video: {path.name}")
        yield capture
    except cv2.error as error:
        raise VideoServiceError(f"OpenCV failed to process video: {path.name}") from error
    finally:
        if capture is not None:
            capture.release()


def _validate_sample_count(sample_count: int) -> None:
    if type(sample_count) is not int or sample_count <= 0:
        raise ValueError("sample_count must be a positive integer")


def calculate_sample_positions(total_frames: int, sample_count: int = 10) -> list[int]:
    """Return evenly spaced indexes; zero total frames returns an empty list."""
    _validate_sample_count(sample_count)
    if type(total_frames) is not int or total_frames < 0:
        raise ValueError("total_frames must be a nonnegative integer")
    count = min(total_frames, sample_count)
    if count == 0:
        return []
    if count == 1:
        return [0]
    return [round(i * (total_frames - 1) / (count - 1)) for i in range(count)]


MAX_UNKNOWN_FRAMES = 300


def get_video_id(video_path: str | Path) -> str:
    """Identify a job-local file by resolved path, size and nanosecond mtime.

    This is a cheap identity check, not a content hash. A fresh upload uses a
    fresh job path, so even identical filenames/sizes get different identities.
    """
    path = Path(video_path)
    if not path.is_file():
        raise VideoServiceError("Video file is missing")
    stat = path.stat()
    identity = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha256(identity.encode()).hexdigest()


def _read_video_info(capture: Any, path: Path) -> dict[str, Any]:
    values = [capture.get(prop) for prop in (cv2.CAP_PROP_FPS,
              cv2.CAP_PROP_FRAME_COUNT, cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT)]
    if (any(not isinstance(value, Real) or not math.isfinite(value) for value in values)
            or any(value < 0 for value in (values[0], values[2], values[3]))):
        raise VideoServiceError("Video metadata contains invalid numeric values")
    fps, frame_count, width, height = values
    # Some backends use a negative frame count for unknown length.
    frame_count, width, height = max(0, int(frame_count)), int(width), int(height)
    stat = path.stat()
    return {
        "video_id": get_video_id(path),
        "filename": path.name,
        "file_size_bytes": stat.st_size,
        "file_size_mb": round(stat.st_size / (1024 * 1024), 2),
        "duration_seconds": round(frame_count / fps, 2) if fps > 0 else 0.0,
        "fps": float(fps),  # Keep precision for timestamps (e.g. 29.97002997).
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}",
    }


def get_video_info(video_path: str | Path) -> dict[str, Any]:
    """Return metadata for reuse with the same, unchanged video file."""
    path = Path(video_path)
    with _open_video(path) as capture:
        return _read_video_info(capture, path)


def _validate_video_info(info: Mapping[str, Any], path: Path) -> None:
    if not isinstance(info, Mapping) or not {'frame_count', 'fps'} <= info.keys():
        raise ValueError("video_info must contain frame_count and fps")
    if info.get('video_id') != get_video_id(path):
        raise ValueError('video_info belongs to a different or changed video')
    calculate_sample_positions(info['frame_count'], 1)
    fps = info['fps']
    if isinstance(fps, bool) or not isinstance(fps, Real) or not math.isfinite(fps) or fps < 0:
        raise ValueError("video_info fps must be a finite nonnegative number")


def _valid_frame(frame: Any) -> bool:
    return (isinstance(frame, np.ndarray) and frame.dtype == np.uint8
            and frame.ndim == 3 and frame.shape[2] == 3 and frame.size > 0)


def iter_frames(
    video_path: str | Path,
    sample_count: int = 10,
    video_info: Mapping[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream successful BGR records, retaining only the current decoded frame.

    Consume using contextlib.closing when early termination is possible. Delete
    the consumed record before requesting the next one to avoid overlapping
    full-resolution arrays. Unknown frame counts use the first bounded N frames
    sequentially (at most MAX_UNKNOWN_FRAMES), not whole-video uniform sampling.
    """
    _validate_sample_count(sample_count)
    path = Path(video_path)
    if video_info is not None:
        _validate_video_info(video_info, path)
    successful = 0
    with _open_video(path) as capture:
        info = video_info if video_info is not None else _read_video_info(capture, path)
        known_count = info['frame_count'] > 0
        positions = (calculate_sample_positions(info['frame_count'], sample_count)
                     if known_count else range(min(sample_count, MAX_UNKNOWN_FRAMES)))
        for index in positions:
            try:
                if known_count and not capture.set(cv2.CAP_PROP_POS_FRAMES, index):
                    continue
                success, frame = capture.read()
            except cv2.error:
                continue
            if not success:
                del frame
                if not known_count:
                    break  # EOF; never decode an unbounded unknown-length source.
                continue
            if not _valid_frame(frame):
                del frame
                continue
            successful += 1
            yield {'index': index,
                   'timestamp_seconds': round(index / info['fps'], 2) if info['fps'] > 0 else 0.0,
                   'frame': frame}
            del frame
    if not successful:
        raise VideoServiceError('Unable to read any sampled frames from the video')


def extract_frames(
    video_path: str | Path,
    sample_count: int = 10,
    video_info: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Compatibility list API; the app uses iter_frames for bounded memory."""
    return list(iter_frames(video_path, sample_count, video_info))


def extract_frame_at_index(
    video_path: str | Path,
    index: int,
    video_info: Mapping[str, Any] | None = None,
) -> np.ndarray:
    """Read only one selected uint8 BGR frame; always release the capture."""
    if type(index) is not int or index < 0:
        raise ValueError('index must be a nonnegative integer')
    path = Path(video_path)
    if video_info is not None:
        _validate_video_info(video_info, path)
    with _open_video(path) as capture:
        info = video_info if video_info is not None else _read_video_info(capture, path)
        if info['frame_count'] > 0:
            if index >= info['frame_count']:
                raise ValueError('index must be less than total frames')
            if not capture.set(cv2.CAP_PROP_POS_FRAMES, index):
                raise VideoServiceError('Unable to seek the selected frame')
            success, frame = capture.read()
        else:
            if index >= MAX_UNKNOWN_FRAMES:
                raise ValueError('index exceeds the bounded unknown-length preview window')
            # Matches the sequential candidate indexes even for non-seekable files.
            for position in range(index + 1):
                success, frame = capture.read()
                if not success:
                    break
                if position < index:
                    del frame
        if not success or not _valid_frame(frame):
            raise VideoServiceError('Unable to read the selected frame')
        return frame
