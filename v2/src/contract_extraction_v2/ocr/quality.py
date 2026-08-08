from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

from .engine import OcrResultLine


KEY_FIELDS = (
    "合同编号", "合同金额", "签订日期", "签署日期", "工期", "服务期", "履约期限",
    "开工", "开工令", "到货", "交货", "初验", "终验", "验收", "设备名称",
    "规格型号", "型号", "数量", "单位",
)


@dataclass(slots=True)
class TextQuality:
    total_chars: int
    cjk_chars: int
    cjk_ratio: float
    replacement_chars: int
    replacement_ratio: float
    line_count: int
    average_line_length: float
    short_line_ratio: float
    single_char_line_ratio: float
    digit_fragmentation_ratio: float
    model_fragmentation_ratio: float
    content_usable: bool
    structure_usable: bool
    score: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class OcrQuality:
    mean_confidence: float
    key_field_confidence: float | None
    key_fields_found: list[str]
    missing_key_values: list[str]
    abnormal_ratio: float
    text_length: int
    score: float
    retry_required: bool
    retry_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def analyze_text_quality(text: str, min_chars: int = 80, min_cjk: int = 20) -> TextQuality:
    raw_lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    compact = re.sub(r"\s+", "", text or "")
    total = len(compact)
    cjk = len(re.findall(r"[\u4e00-\u9fff]", compact))
    replacement = compact.count("�") + compact.count("\ufffd")
    lengths = [len(re.sub(r"\s+", "", line)) for line in raw_lines]
    short_ratio = (sum(length <= 3 for length in lengths) / len(lengths)) if lengths else 1.0
    single_ratio = (sum(length == 1 for length in lengths) / len(lengths)) if lengths else 1.0

    digit_lines = [line for line in raw_lines if re.fullmatch(r"[\d.,%]+", line)]
    fragmented_digits = [line for line in digit_lines if len(re.sub(r"\D", "", line)) <= 2]
    digit_fragmentation = len(fragmented_digits) / max(1, len(digit_lines))

    model_tokens = re.findall(r"[A-Za-z0-9][-A-Za-z0-9./]{1,}", "\n".join(raw_lines))
    short_model_tokens = [token for token in model_tokens if len(token) <= 3]
    model_fragmentation = len(short_model_tokens) / max(1, len(model_tokens)) if model_tokens else 0.0

    cjk_ratio = cjk / max(1, total)
    replacement_ratio = replacement / max(1, total)
    average = sum(lengths) / max(1, len(lengths))
    content_usable = total >= min_chars and cjk >= min_cjk and replacement_ratio <= 0.03
    fragmented_layout = (
        len(raw_lines) >= 6
        and (short_ratio >= 0.62 or single_ratio >= 0.32)
        and (digit_fragmentation >= 0.45 or average <= 5.5)
    )
    structure_usable = content_usable and not fragmented_layout and model_fragmentation < 0.78
    score = 1.0
    score -= min(0.45, max(0, min_chars - total) / max(1, min_chars) * 0.45)
    score -= min(0.25, replacement_ratio * 4)
    score -= 0.20 if fragmented_layout else 0.0
    score -= max(0.0, model_fragmentation - 0.55) * 0.25
    return TextQuality(
        total, cjk, cjk_ratio, replacement, replacement_ratio, len(raw_lines), average,
        short_ratio, single_ratio, digit_fragmentation, model_fragmentation,
        content_usable, structure_usable, round(max(0.0, min(1.0, score)), 4),
    )


def _field_value_missing(text: str, field: str) -> bool:
    match = re.search(re.escape(field) + r"\s*[：:]?\s*([^\n]{0,30})", text)
    if not match:
        return False
    value = match.group(1).strip(" ：:")
    return not value or value in {"无", "/", "-"}


def assess_ocr_quality(
    lines: list[OcrResultLine],
    *,
    page_type: str = "NORMAL",
    table_rows: list[dict[str, object]] | None = None,
    retry_mean_confidence: float = 0.90,
    retry_key_field_confidence: float = 0.88,
) -> OcrQuality:
    text = "\n".join(line.text for line in lines)
    scores = [line.score for line in lines if math.isfinite(line.score)]
    mean = sum(scores) / len(scores) if scores else 0.0
    fields = [field for field in KEY_FIELDS if field in text]
    field_scores = [line.score for line in lines if any(field in line.text for field in fields)]
    key_conf = sum(field_scores) / len(field_scores) if field_scores else None
    missing_values = [field for field in fields if _field_value_missing(text, field)]
    compact = re.sub(r"\s+", "", text)
    abnormal = len(re.findall(r"[^\u4e00-\u9fffA-Za-z0-9\s，。；：、（）()《》【】\[\].,;:%+\-_/￥¥]", text))
    abnormal_ratio = abnormal / max(1, len(compact))
    structure = analyze_text_quality(text, min_chars=20, min_cjk=2)
    reasons: list[str] = []
    if mean < retry_mean_confidence:
        reasons.append(f"平均置信度{mean:.3f}低于{retry_mean_confidence:.2f}")
    if key_conf is not None and key_conf < retry_key_field_confidence:
        reasons.append(f"关键字段置信度{key_conf:.3f}低于{retry_key_field_confidence:.2f}")
    if len(compact) < 30:
        reasons.append("OCR结果过短")
    if missing_values:
        reasons.append("识别到关键字段名称但字段值缺失")
    if abnormal_ratio > 0.04:
        reasons.append("异常字符比例过高")
    if structure.digit_fragmentation_ratio > 0.65 or structure.model_fragmentation_ratio > 0.78:
        reasons.append("型号或数字明显碎裂")
    if page_type in {"TABLE", "MIXED"} and not (table_rows or []):
        reasons.append("表格页未恢复有效行")
    completeness = min(1.0, len(fields) / 5.0)
    score = mean * 0.55 + structure.score * 0.25 + completeness * 0.20
    if table_rows:
        score += min(0.12, len(table_rows) * 0.015)
    return OcrQuality(
        round(mean, 4), round(key_conf, 4) if key_conf is not None else None,
        fields, missing_values, round(abnormal_ratio, 4), len(compact),
        round(min(1.0, score), 4), bool(reasons), reasons,
    )
