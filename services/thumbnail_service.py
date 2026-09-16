"""Transform extracted uint8 BGR frames; no video or resource dependencies.

The app streams candidate frames through create_preview(), storing only small
JPEG bytes plus index/timestamp metadata. No full-resolution candidates persist
in session state. On selection it re-reads one frame, calls create_thumbnail()
and save_thumbnail(), and reuses that saved Path on unchanged UI reruns.

Keep the job upload until it is replaced/removed so selection can re-read it.
The workflow then deletes the selected output and cleans only that job. Optional
stale-output cleanup uses a retention window longer than expected downloads;
an expired selected result can be regenerated from the retained upload.
"""
from pathlib import Path
from io import BytesIO

import numpy as np
from PIL import Image, ImageOps

from . import file_manager


def _frame_to_image(frame: np.ndarray) -> Image.Image:
    """Validate a three-channel OpenCV-style frame and convert BGR to RGB."""
    if not isinstance(frame, np.ndarray):
        raise TypeError("frame must be a NumPy ndarray in BGR channel order")
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError(f"frame must have shape (height, width, 3); got {frame.shape}")
    if frame.size == 0:
        raise ValueError("frame must have nonzero height and width")
    if frame.dtype != np.uint8:
        raise TypeError(f"frame must have dtype uint8; got {frame.dtype}")
    return Image.fromarray(np.ascontiguousarray(frame[..., ::-1]))


def create_thumbnail(
    frame: np.ndarray,
    size: tuple[int, int] = (320, 180),
    crop: bool = False,
) -> Image.Image:
    """Return an RGB thumbnail at exactly size=(width, height).

    Preserve aspect ratio: crop=False adds centered black padding; crop=True
    center-crops to fill. Small frames are upscaled. Input is never modified.
    Only uint8 arrays with three BGR channels are accepted.
    """
    if (not isinstance(size, tuple) or len(size) != 2
            or any(type(value) is not int or value <= 0 for value in size)):
        raise ValueError("size must be a (width, height) tuple of positive integers")
    if not isinstance(crop, bool):
        raise TypeError("crop must be a boolean")
    image = _frame_to_image(frame)
    if crop:
        return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)
    return ImageOps.pad(image, size, method=Image.Resampling.LANCZOS, color=(0, 0, 0))


def save_thumbnail(
    thumbnail: Image.Image,
    image_format: str = "JPEG",
    quality: int = 90,
    compress_level: int | None = None,
) -> Path:
    """Encode an RGB thumbnail as JPEG or PNG and return its unique saved path.

    File Manager owns paths and file lifecycle. Existing files are never
    overwritten. Filesystem errors propagate; failed writes are removed.

    JPEG uses quality (1..95), preserves optimize=True, and rejects a supplied
    compress_level. PNG accepts only the unchanged quality=90 default (unused)
    and rejects non-default JPEG quality. PNG uses compress_level (0..9), or
    Pillow's default when it is None.
    """
    if not isinstance(thumbnail, Image.Image):
        raise TypeError("thumbnail must be a PIL.Image.Image")
    if thumbnail.mode != "RGB" or min(thumbnail.size) <= 0:
        raise ValueError("thumbnail must be a nonempty RGB image from create_thumbnail")
    if not isinstance(image_format, str) or image_format.upper() not in {"JPEG", "PNG"}:
        raise ValueError("image_format must be JPEG or PNG")
    image_format = image_format.upper()
    if image_format == "JPEG":
        if type(quality) is not int or not 1 <= quality <= 95:
            raise ValueError("JPEG quality must be an integer from 1 to 95")
        if compress_level is not None:
            raise ValueError("compress_level is only supported for PNG")
        options = {"quality": quality, "optimize": True}
    else:
        if type(quality) is not int or quality != 90:
            raise ValueError("quality is JPEG-only; use compress_level for PNG")
        if compress_level is not None and (
                type(compress_level) is not int or not 0 <= compress_level <= 9):
            raise ValueError("PNG compress_level must be an integer from 0 to 9 or None")
        options = {} if compress_level is None else {"compress_level": compress_level}
    path = file_manager.generate_thumbnail_path("jpg" if image_format == "JPEG" else "png")
    with file_manager.open_thumbnail_file(path) as stream:
        thumbnail.save(stream, format=image_format, **options)
    return path


def create_preview(frame: np.ndarray) -> bytes:
    """Return one 320x180 JPEG preview; no input array/image is retained."""
    with create_thumbnail(frame) as thumbnail, BytesIO() as buffer:
        thumbnail.save(buffer, format='JPEG', quality=75, optimize=True)
        return buffer.getvalue()
