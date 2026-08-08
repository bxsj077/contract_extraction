from __future__ import annotations

import re
from difflib import SequenceMatcher

from .engine import OcrResultLine


def _rect(line: OcrResultLine) -> tuple[float, float, float, float] | None:
    if not line.box:
        return None
    xs = [point[0] for point in line.box]
    ys = [point[1] for point in line.box]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(a: OcrResultLine, b: OcrResultLine) -> float:
    ra, rb = _rect(a), _rect(b)
    if not ra or not rb:
        return 0.0
    left, top = max(ra[0], rb[0]), max(ra[1], rb[1])
    right, bottom = min(ra[2], rb[2]), min(ra[3], rb[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    area_a = max(1.0, (ra[2] - ra[0]) * (ra[3] - ra[1]))
    area_b = max(1.0, (rb[2] - rb[0]) * (rb[3] - rb[1]))
    return intersection / min(area_a, area_b)


def _normalized(text: str) -> str:
    return re.sub(r"[\s：:，,。.;；]", "", text).lower()


def _same_detection(a: OcrResultLine, b: OcrResultLine) -> bool:
    similarity = SequenceMatcher(None, _normalized(a.text), _normalized(b.text)).ratio()
    if a.box and b.box:
        return _overlap(a, b) >= 0.45 and similarity >= 0.72
    return similarity >= 0.94


def _preferred(a: OcrResultLine, b: OcrResultLine) -> OcrResultLine:
    score_a = a.score + min(len(_normalized(a.text)), 40) * 0.002
    score_b = b.score + min(len(_normalized(b.text)), 40) * 0.002
    return b if score_b > score_a else a


def merge_ocr_results(*result_sets: list[OcrResultLine]) -> list[OcrResultLine]:
    merged: list[OcrResultLine] = []
    for line in (item for group in result_sets for item in group):
        duplicate_index = next((index for index, existing in enumerate(merged) if _same_detection(existing, line)), None)
        if duplicate_index is None:
            merged.append(line)
        else:
            merged[duplicate_index] = _preferred(merged[duplicate_index], line)
    return sorted(merged, key=lambda line: (
        min((point[1] for point in line.box), default=10**9),
        min((point[0] for point in line.box), default=10**9),
    ))


def merge_native_and_ocr_text(native_text: str, lines: list[OcrResultLine]) -> str:
    """Keep V1's native-first evidence order while removing OCR duplicates."""

    selected = [line.strip() for line in native_text.splitlines() if line.strip()]
    normalized_selected = [_normalized(line) for line in selected]
    for ocr_line in (line.text.strip() for line in lines if line.text.strip()):
        norm = _normalized(ocr_line)
        if not norm:
            continue
        if any(SequenceMatcher(None, norm, other).ratio() >= 0.92 for other in normalized_selected):
            continue
        selected.append(ocr_line)
        normalized_selected.append(norm)
    return "\n".join(selected)
