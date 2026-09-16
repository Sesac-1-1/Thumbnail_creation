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


def _read_video_info(capture: Any, path: Path) -> dict[str, Any]:
    values = [capture.get(prop) for prop in (cv2.CAP_PROP_FPS,
              cv2.CAP_PROP_FRAME_COUNT, cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT)]
    if any(not isinstance(value, Real) or not math.isfinite(value) or value < 0
           for value in values):
        raise VideoServiceError("Video metadata contains invalid numeric values")
    fps, frame_count, width, height = values
    frame_count, width, height = int(frame_count), int(width), int(height)
    stat = path.stat()
    return {
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


def _validate_video_info(info: Mapping[str, Any]) -> None:
    if not isinstance(info, Mapping) or not {'frame_count', 'fps'} <= info.keys():
        raise ValueError("video_info must contain frame_count and fps")
    calculate_sample_positions(info['frame_count'], 1)
    fps = info['fps']
    if isinstance(fps, bool) or not isinstance(fps, Real) or not math.isfinite(fps) or fps < 0:
        raise ValueError("video_info fps must be a finite nonnegative number")


def extract_frames(
    video_path: str | Path,
    sample_count: int = 10,
    video_info: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return successful {index, timestamp_seconds, frame} records only.

    Each frame is a nonempty uint8 (H,W,3) BGR ndarray. Failed seeks/reads or
    invalid frames are skipped; failure of every sample raises VideoServiceError.
    Supplied metadata must belong to this unchanged video. Without it, metadata
    is read from the extraction capture, without opening the file again.
    """
    _validate_sample_count(sample_count)
    if video_info is not None:
        _validate_video_info(video_info)
    result = []
    path = Path(video_path)
    with _open_video(path) as capture:
        info = video_info if video_info is not None else _read_video_info(capture, path)
        positions = calculate_sample_positions(info['frame_count'], sample_count)
        if not positions:
            raise VideoServiceError("The video has no readable frames")
        for index in positions:
            try:
                if not capture.set(cv2.CAP_PROP_POS_FRAMES, index):
                    continue
                success, frame = capture.read()
            except cv2.error:
                continue  # Individual codec/seek failures are recoverable.
            if (not success or not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
                    or frame.ndim != 3 or frame.shape[2] != 3 or frame.size == 0):
                continue
            result.append({
                "index": index,
                "timestamp_seconds": round(index / info['fps'], 2) if info['fps'] > 0 else 0.0,
                "frame": frame,
            })
    if not result:
        raise VideoServiceError("Unable to read any sampled frames from the video")
    return result
