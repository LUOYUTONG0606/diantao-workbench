"""Pure conversion logic for the self-hosted PaddleOCR compatibility service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, TypeAlias


JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class OcrCenter:
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class OcrBlock:
    text: str
    score: float
    center: OcrCenter


def _mapping(value: JsonValue) -> Mapping[str, JsonValue] | None:
    if isinstance(value, dict):
        return value
    return None


def _numbers(value: JsonValue) -> list[float] | None:
    if not isinstance(value, list):
        return None
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        numbers.append(float(item))
    return numbers


def _center_from_box(value: JsonValue) -> OcrCenter:
    if not isinstance(value, list):
        return OcrCenter(x=0, y=0)

    flat_box = _numbers(value)
    if flat_box is not None and len(flat_box) >= 4:
        x1, y1, x2, y2 = flat_box[:4]
        return OcrCenter(x=round((x1 + x2) / 2), y=round((y1 + y2) / 2))

    points: list[tuple[float, float]] = []
    for point in value:
        if not isinstance(point, list) or len(point) < 2:
            continue
        coordinates = _numbers(point)
        if coordinates is not None and len(coordinates) >= 2:
            points.append((coordinates[0], coordinates[1]))

    if not points:
        return OcrCenter(x=0, y=0)

    return OcrCenter(
        x=round(sum(point[0] for point in points) / len(points)),
        y=round(sum(point[1] for point in points) / len(points)),
    )


def _texts(value: JsonValue) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _rows(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        return []
    return value


def normalize_paddle_result(result: Mapping[str, JsonValue]) -> list[OcrBlock]:
    """Convert one PaddleOCR JSON result to the legacy OCR block contract."""

    nested = _mapping(result.get("res"))
    payload = nested if nested is not None else result

    texts = _texts(payload.get("rec_texts"))
    scores = _numbers(payload.get("rec_scores", [])) or []
    boxes = _rows(payload.get("rec_boxes", []))
    if not boxes:
        boxes = _rows(payload.get("dt_polys", []))
    if not boxes:
        boxes = _rows(payload.get("rec_polys", []))

    blocks: list[OcrBlock] = []
    for index, text in enumerate(texts):
        cleaned_text = text.strip()
        if not cleaned_text:
            continue
        score = scores[index] if index < len(scores) else 0.0
        box = boxes[index] if index < len(boxes) else []
        blocks.append(
            OcrBlock(
                text=cleaned_text,
                score=score,
                center=_center_from_box(box),
            )
        )
    return blocks
