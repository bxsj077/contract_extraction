from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .engine import OcrResultLine
from .quality import TextQuality, analyze_text_quality


class PageType(str, Enum):
    NORMAL = "NORMAL"
    TABLE = "TABLE"
    SIGNATURE = "SIGNATURE"
    MIXED = "MIXED"


TABLE_KEYWORDS = (
    "序号", "设备名称", "产品名称", "货物名称", "材料名称", "服务名称", "规格",
    "型号", "品牌", "单位", "数量", "单价", "总价", "合计", "采购清单", "设备清单",
    "报价表", "工程量清单", "材料清单", "服务清单", "软硬件清单",
)
SIGNATURE_KEYWORDS = (
    "签订日期", "签署日期", "法定代表人", "授权代表", "甲方（盖章）", "乙方（盖章）",
    "甲方盖章", "乙方盖章", "签字盖章", "签章", "公章",
)


@dataclass(slots=True)
class Classification:
    page_type: PageType
    table_score: float
    signature_score: float
    reasons: list[str]
    native_quality: TextQuality


def _coordinate_table_score(lines: list[OcrResultLine]) -> float:
    boxes = [line.box for line in lines if len(line.box) >= 4]
    if len(boxes) < 6:
        return 0.0
    centers = [sum(point[1] for point in box) / len(box) for box in boxes]
    heights = [max(point[1] for point in box) - min(point[1] for point in box) for box in boxes]
    tolerance = max(5.0, (sum(heights) / len(heights)) * 0.65)
    groups: list[list[float]] = []
    for center in sorted(centers):
        target = next((group for group in groups if abs(sum(group) / len(group) - center) <= tolerance), None)
        if target is None:
            groups.append([center])
        else:
            target.append(center)
    multi = sum(len(group) >= 3 for group in groups)
    return min(1.0, multi / 3.0)


def classify_page(
    native_text: str,
    ocr_lines: list[OcrResultLine] | None = None,
    native_quality: TextQuality | None = None,
) -> Classification:
    lines = ocr_lines or []
    text = "\n".join(filter(None, [native_text, "\n".join(line.text for line in lines)]))
    compact = re.sub(r"\s+", "", text)
    quality = native_quality or analyze_text_quality(native_text)
    keyword_hits = [word for word in TABLE_KEYWORDS if word in compact]
    header_hits = [word for word in keyword_hits if word in {"序号", "设备名称", "产品名称", "货物名称", "规格", "型号", "品牌", "单位", "数量", "单价", "总价"}]
    digit_density = len(re.findall(r"\d", compact)) / max(1, len(compact))
    coord_score = _coordinate_table_score(lines)
    table_score = min(1.0, len(header_hits) * 0.13 + len(keyword_hits) * 0.035 + min(0.22, digit_density) + coord_score * 0.25)
    if not quality.structure_usable and quality.content_usable:
        table_score += 0.12
    table_score = min(1.0, table_score)

    signature_hits = [word for word in SIGNATURE_KEYWORDS if word in compact]
    date_hits = len(re.findall(r"20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*日?", compact))
    signature_score = min(1.0, len(signature_hits) * 0.18 + min(0.30, date_hits * 0.15))
    reasons: list[str] = []
    is_table = len(header_hits) >= 3 and table_score >= 0.50
    is_signature = len(signature_hits) >= 2 and signature_score >= 0.38
    if is_table:
        reasons.append(f"命中{len(header_hits)}个表头关键词")
    if coord_score >= 0.5:
        reasons.append("OCR坐标呈多列同行分布")
    if is_signature:
        reasons.append(f"命中{len(signature_hits)}个签章关键词")
    if is_table and is_signature:
        page_type = PageType.MIXED
    elif is_table:
        page_type = PageType.TABLE
    elif is_signature:
        page_type = PageType.SIGNATURE
    else:
        page_type = PageType.NORMAL
    return Classification(page_type, round(table_score, 4), round(signature_score, 4), reasons, quality)
