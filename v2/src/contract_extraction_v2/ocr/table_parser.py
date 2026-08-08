from __future__ import annotations

import re
from statistics import median
from typing import Any

from .engine import OcrResultLine


HEADER_ALIASES = {
    "序号": ("序号", "编号", "项号"),
    "名称": ("设备名称", "产品名称", "货物名称", "材料名称", "服务名称", "名称", "采购内容"),
    "品牌": ("品牌", "制造商"),
    "型号": ("规格型号", "型号", "规格", "技术规格"),
    "数量": ("数量", "工程量"),
    "单位": ("单位",),
    "单价": ("单价", "含税单价"),
    "总价": ("总价", "合价", "含税总价"),
}


def _bounds(line: OcrResultLine) -> tuple[float, float, float, float]:
    xs = [point[0] for point in line.box] or [0.0]
    ys = [point[1] for point in line.box] or [0.0]
    return min(xs), min(ys), max(xs), max(ys)


def group_lines_by_y(lines: list[OcrResultLine], tolerance_ratio: float = 0.65) -> list[list[OcrResultLine]]:
    positioned = [line for line in lines if line.box]
    heights = [max(1.0, _bounds(line)[3] - _bounds(line)[1]) for line in positioned]
    tolerance = max(4.0, median(heights) * tolerance_ratio) if heights else 8.0
    rows: list[list[OcrResultLine]] = []
    for line in sorted(positioned, key=lambda item: ((_bounds(item)[1] + _bounds(item)[3]) / 2, _bounds(item)[0])):
        center = (_bounds(line)[1] + _bounds(line)[3]) / 2
        best: list[OcrResultLine] | None = None
        best_distance = float("inf")
        for row in rows:
            row_center = sum((_bounds(item)[1] + _bounds(item)[3]) / 2 for item in row) / len(row)
            distance = abs(center - row_center)
            vertical_overlap = min(_bounds(line)[3], max(_bounds(item)[3] for item in row)) - max(
                _bounds(line)[1], min(_bounds(item)[1] for item in row))
            if distance <= tolerance or vertical_overlap > 0:
                if distance < best_distance:
                    best, best_distance = row, distance
        if best is None:
            rows.append([line])
        else:
            best.append(line)
    return [sort_cells_by_x(row) for row in rows]


def sort_cells_by_x(row: list[OcrResultLine]) -> list[OcrResultLine]:
    return sorted(row, key=lambda line: _bounds(line)[0])


def detect_columns(rows: list[list[OcrResultLine]]) -> list[float]:
    candidates: list[float] = []
    for row in rows:
        if len(row) >= 3:
            candidates.extend((_bounds(cell)[0] + _bounds(cell)[2]) / 2 for cell in row)
    if not candidates:
        return []
    candidates.sort()
    widths = [_bounds(cell)[2] - _bounds(cell)[0] for row in rows for cell in row]
    tolerance = max(15.0, median(widths) * 0.55) if widths else 25.0
    clusters: list[list[float]] = []
    for value in candidates:
        cluster = next((item for item in clusters if abs(sum(item) / len(item) - value) <= tolerance), None)
        if cluster is None:
            clusters.append([value])
        else:
            cluster.append(value)
    return [sum(cluster) / len(cluster) for cluster in clusters if len(cluster) >= 2]


def _header_name(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text)
    for canonical, aliases in HEADER_ALIASES.items():
        if any(alias in compact for alias in aliases):
            return canonical
    return None


def build_table_rows(lines: list[OcrResultLine], *, source_engine: str = "ocr_boxes") -> list[dict[str, Any]]:
    visual_rows = group_lines_by_y(lines)
    if not visual_rows:
        return []
    header_index = -1
    header_cells: list[tuple[float, str]] = []
    for index, row in enumerate(visual_rows):
        mapped = [((_bounds(cell)[0] + _bounds(cell)[2]) / 2, _header_name(cell.text)) for cell in row]
        recognized = [(x, name) for x, name in mapped if name]
        if len(recognized) >= 2:
            header_index, header_cells = index, [(x, str(name)) for x, name in recognized]
            break
    result: list[dict[str, Any]] = []
    data_rows = visual_rows[header_index + 1:] if header_index >= 0 else visual_rows
    for row in data_rows:
        cells = [cell.text.strip() for cell in row if cell.text.strip()]
        if not cells or len(cells) < 2:
            continue
        if any("合计" in cell or "总计" in cell for cell in cells) and len(cells) <= 3:
            continue
        item: dict[str, Any] = {
            "cells": cells,
            "confidence": round(sum(cell.score for cell in row) / len(row), 4),
            "source_engine": source_engine,
        }
        if header_cells:
            for cell in row:
                center = (_bounds(cell)[0] + _bounds(cell)[2]) / 2
                _, key = min(header_cells, key=lambda pair: abs(pair[0] - center))
                if key in item:
                    item[key] = f"{item[key]} {cell.text}".strip()
                else:
                    item[key] = cell.text.strip()
        result.append(item)
    return result


def table_rows_to_text(rows: list[dict[str, Any]]) -> str:
    return "\n".join(" | ".join(str(cell) for cell in row.get("cells", [])) for row in rows)
