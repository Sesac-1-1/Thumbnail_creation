"""Transform extracted uint8 BGR frames; no video or resource dependencies."""
from pathlib import Path

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
) -> Path:
    """Encode an RGB thumbnail as JPEG or PNG and return its unique saved path.

    File Manager owns paths and file lifecycle. Existing files are never
    overwritten. Filesystem errors propagate; failed writes are removed.
    """
    if not isinstance(thumbnail, Image.Image):
        raise TypeError("thumbnail must be a PIL.Image.Image")
    if thumbnail.mode != "RGB" or min(thumbnail.size) <= 0:
        raise ValueError("thumbnail must be a nonempty RGB image from create_thumbnail")
    if not isinstance(image_format, str) or image_format.upper() not in {"JPEG", "PNG"}:
        raise ValueError("image_format must be JPEG or PNG")
    if type(quality) is not int or not 1 <= quality <= 95:
        raise ValueError("quality must be an integer from 1 to 95")
    image_format = image_format.upper()
    path = file_manager.generate_thumbnail_path("jpg" if image_format == "JPEG" else "png")
    options = {"quality": quality, "optimize": True} if image_format == "JPEG" else {}
    with file_manager._open_thumbnail_file(path) as stream:
        thumbnail.save(stream, format=image_format, **options)
    return path


def add_text_overlay(thumbnail: Image.Image, text: str, font_size: int = 32) -> Image.Image:
    """Return a copy with a fitted, wrapped Korean caption overlaid."""
    from PIL import ImageDraw, ImageFont

    if not isinstance(thumbnail, Image.Image) or thumbnail.mode != "RGB":
        raise TypeError("thumbnail must be an RGB PIL.Image.Image")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a nonempty string")
    if type(font_size) is not int or font_size <= 0:
        raise ValueError("font_size must be a positive integer")
    font_paths = [Path(r"C:\Windows\Fonts\malgunbd.ttf"), Path(r"C:\Windows\Fonts\malgun.ttf"),
                  Path(r"C:\Windows\Fonts\gulim.ttc"),
                  Path(r"/usr/share/fonts/truetype/nanum/NanumGothic.ttf")]
    result = thumbnail.copy()
    draw = ImageDraw.Draw(result, "RGBA")
    font_path = next((path for path in font_paths if path.is_file()), None)
    max_width = int(result.width * 0.90)
    selected_lines: list[str] = [text.strip()]
    selected_font = None

    # Fit long titles by wrapping at word/character boundaries and shrinking.
    for size in range(font_size, 11, -1):
        font = ImageFont.truetype(str(font_path), size) if font_path else ImageFont.load_default()
        lines: list[str] = []
        for paragraph in text.strip().splitlines():
            current = ""
            for char in paragraph:
                candidate = current + char
                if draw.textbbox((0, 0), candidate, font=font, stroke_width=2)[2] <= max_width:
                    current = candidate
                else:
                    if current:
                        lines.append(current)
                    current = char
            if current:
                lines.append(current)
        if len(lines) <= 2:
            selected_lines, selected_font = lines, font
            break
    if selected_font is None:
        selected_font = ImageFont.truetype(str(font_path), 12) if font_path else ImageFont.load_default()

    spacing = max(2, selected_font.size // 6)
    boxes = [draw.textbbox((0, 0), line, font=selected_font, stroke_width=2)
             for line in selected_lines]
    text_width = max(box[2] - box[0] for box in boxes)
    text_height = sum(box[3] - box[1] for box in boxes) + spacing * (len(boxes) - 1)
    pad_x, pad_y = 12, 8
    x = (result.width - text_width) // 2
    y = result.height - text_height - pad_y * 2 - 4
    draw.rounded_rectangle((x - pad_x, y - pad_y, x + text_width + pad_x, y + text_height + pad_y),
                           radius=7, fill=(0, 0, 0, 185))
    cursor_y = y
    for line, box in zip(selected_lines, boxes):
        line_width = box[2] - box[0]
        cursor_x = (result.width - line_width) // 2
        draw.text((cursor_x, cursor_y), line, font=selected_font, fill=(255, 255, 255, 255),
                  stroke_width=2, stroke_fill=(0, 0, 0, 255))
        cursor_y += box[3] - box[1] + spacing
    return result
