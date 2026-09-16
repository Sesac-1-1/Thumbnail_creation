"""Video metadata analysis and lightweight frame sampling."""

from pathlib import Path
from typing import Any

import cv2


class VideoServiceError(RuntimeError):
    """Raised when a video cannot be opened or sampled."""


def get_video_info(video_path: str | Path) -> dict[str, Any]:
    """Return file size, duration, FPS, resolution, and frame count."""
    path = Path(video_path)
    if not path.is_file():
        raise VideoServiceError(f"Video file does not exist: {path}")

    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise VideoServiceError(f"Unable to open video: {path}")

        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        return {
            "filename": path.name,
            "file_size_bytes": path.stat().st_size,
            "file_size_mb": round(path.stat().st_size / (1024 * 1024), 2),
            "duration_seconds": round(frame_count / fps, 2) if fps > 0 else 0.0,
            "fps": round(fps, 2),
            "frame_count": frame_count,
            "width": width,
            "height": height,
            "resolution": f"{width}x{height}",
        }
    finally:
        capture.release()


def calculate_sample_positions(total_frames: int, sample_count: int = 10) -> list[int]:
    """Return evenly spaced frame indexes, including the first and last frame."""
    if total_frames <= 0 or sample_count <= 0:
        return []
    count = min(total_frames, sample_count)
    if count == 1:
        return [0]
    return [round(i * (total_frames - 1) / (count - 1)) for i in range(count)]


def extract_frames(
    video_path: str | Path,
    sample_count: int = 10,
) -> list[dict[str, Any]]:
    """Extract only evenly spaced frames and return them as BGR NumPy arrays.

    The returned items contain ``index``, ``timestamp_seconds``, and ``frame``.
    The BGR format is intentional: it can be passed directly to
    ``thumbnail_service.create_thumbnail``.
    """
    info = get_video_info(video_path)
    positions = calculate_sample_positions(info["frame_count"], sample_count)
    if not positions:
        raise VideoServiceError("The video has no readable frames.")

    capture = cv2.VideoCapture(str(video_path))
    result: list[dict[str, Any]] = []
    try:
        if not capture.isOpened():
            raise VideoServiceError(f"Unable to open video: {video_path}")
        for index in positions:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            success, frame = capture.read()
            if success and frame is not None:
                result.append({
                    "index": index,
                    "timestamp_seconds": round(index / info["fps"], 2)
                    if info["fps"] > 0 else 0.0,
                    "frame": frame,
                })
    finally:
        capture.release()

    if not result:
        raise VideoServiceError("Unable to read sampled frames from the video.")
    return result
