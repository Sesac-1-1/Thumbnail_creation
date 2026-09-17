"""AI-assisted thumbnail candidate selection and copy suggestions."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


def _load_api_key() -> None:
    """Load the API key from this project's root .env file."""
    project_root = Path(__file__).resolve().parents[1]
    env_path = project_root / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)


def has_api_key() -> bool:
    _load_api_key()
    return bool(os.getenv("OPENAI_API_KEY"))


def _to_data_url(frame: Any, image_format: str = "jpeg") -> str:
    """Encode an OpenCV BGR frame as a compact data URL."""
    import cv2

    success, encoded = cv2.imencode(f".{image_format}", frame)
    if not success:
        raise ValueError("프레임 이미지를 인코딩할 수 없습니다")
    data = base64.b64encode(encoded.tobytes()).decode("ascii")
    return f"data:image/{image_format};base64,{data}"


def _parse_json_response(raw: str) -> dict[str, Any]:
    """Parse plain JSON even when the model wraps it in Markdown or prose."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise json.JSONDecodeError("AI response must be an object", text, 0)
    return value


def analyze_candidates(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the best frame and suggest Korean thumbnail copy in one request."""
    if not frames:
        raise ValueError("분석할 프레임이 없습니다")
    if not has_api_key():
        raise RuntimeError("OPENAI_API_KEY를 찾을 수 없습니다")

    model = os.getenv("OPENAI_MODEL") or "gpt-5.6-luna"
    content: list[dict[str, Any]] = [{
        "type": "input_text",
        "text": (
            "아래 영상 프레임 후보를 유튜브 썸네일 관점에서 분석해줘. "
            "가장 좋은 프레임 3개를 순위대로 고르고, 각 프레임마다 "
            "짧고 클릭을 유도하는 한국어 썸네일 문구 3개씩 제안해줘. "
            "반드시 후보 index에 있는 프레임만 선택해. "
            '반드시 JSON만 반환: {"candidates": [{"index": 숫자, "suggestions": ["문구1", "문구2", "문구3"]}]}'
        ),
    }]
    for item in frames:
        content.append({
            "type": "input_text",
            "text": f"후보 index: {item['index']}, 시점: {item['timestamp_seconds']}초",
        })
        content.append({
            "type": "input_image",
            "image_url": _to_data_url(item["frame"]),
            "detail": "low",
        })

    response = OpenAI().responses.create(
        model=model,
        input=[{"role": "user", "content": content}],
        # Three frames plus three Korean suggestions per frame need more room
        # than the previous single-frame response limit.
        max_output_tokens=800,
    )
    raw = response.output_text.strip()
    try:
        parsed = _parse_json_response(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("AI 응답을 JSON으로 해석할 수 없습니다") from error

    valid_indexes = {item["index"] for item in frames}
    candidates = []
    for candidate in parsed.get("candidates", []):
        index = candidate.get("index")
        if index not in valid_indexes:
            continue
        suggestions = [str(value).strip() for value in candidate.get("suggestions", [])
                       if str(value).strip()]
        if suggestions:
            candidates.append({"index": index, "suggestions": suggestions[:3]})
    if not candidates:
        raise RuntimeError("AI가 유효한 프레임 추천 결과를 반환하지 않았습니다")
    return {
        "best_index": candidates[0]["index"],
        "candidates": candidates[:3],
        "model": model,
        "usage": getattr(response, "usage", None),
    }
