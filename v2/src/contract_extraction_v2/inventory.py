from __future__ import annotations

import re
from typing import Any, Iterable

from .system_models import EquipmentItem


SERVICE_CONTENT_RE = re.compile(
    r"软件部署实施|硬件部署实施(?:及系统集成)?|系统集成|安装调试|系统迁移|数据迁移|"
    r"培训|技术服务|驻场服务|实施服务|维保服务|运维服务|咨询服务|集成服务|部署实施"
)


def normalize_inventory_text(value: object) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("（", "(").replace("）", ")").replace("－", "-").replace("—", "-")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def normalize_quantity(value: object) -> float | None:
    if value in (None, ""):
        return None
    match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def is_service_content(name: object) -> bool:
    return bool(SERVICE_CONTENT_RE.search(str(name or "")))


def inventory_row_key(row: dict[str, Any]) -> tuple[object, ...]:
    return (
        normalize_inventory_text(row.get("清单类型") or row.get("list_type") or "采购交付清单"),
        normalize_inventory_text(row.get("名称") or row.get("name") or row.get("standard_name")),
        normalize_inventory_text(row.get("型号") or row.get("model")),
        normalize_inventory_text(row.get("单位") or row.get("unit")),
        normalize_quantity(row.get("数量") or row.get("quantity")),
        normalize_inventory_text(row.get("来源文件") or row.get("source_file")),
        str(row.get("页码") or row.get("page") or ""),
    )


def deduplicate_inventory_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate multi-route OCR rows only within the same PDF and page."""

    selected: dict[tuple[object, ...], dict[str, Any]] = {}
    order: list[tuple[object, ...]] = []
    priority = {"ppstructure": 4, "ocr_boxes": 3, "native_table": 2, "text_regex": 1}
    for row in rows:
        key = inventory_row_key(row)
        if key not in selected:
            selected[key] = row
            order.append(key)
            continue
        old = selected[key]
        old_score = float(old.get("confidence") or 0) + priority.get(str(old.get("source_engine")), 0) * 0.05
        new_score = float(row.get("confidence") or 0) + priority.get(str(row.get("source_engine")), 0) * 0.05
        if new_score > old_score:
            selected[key] = row
    return [selected[key] for key in order]


def equipment_item_key(item: EquipmentItem, evidence_source: tuple[str, object] = ("", "")) -> tuple[object, ...]:
    source_file, page = evidence_source
    return (
        normalize_inventory_text(item.list_type),
        normalize_inventory_text(item.standard_name),
        normalize_inventory_text(item.model),
        normalize_inventory_text(item.unit),
        item.quantity,
        normalize_inventory_text(source_file),
        str(page),
    )


def deduplicate_equipment_items(
    items: Iterable[EquipmentItem],
    evidence_locations: dict[str, tuple[str, object]] | None = None,
) -> list[EquipmentItem]:
    locations = evidence_locations or {}
    selected: dict[tuple[object, ...], EquipmentItem] = {}
    order: list[tuple[object, ...]] = []
    for item in items:
        key = equipment_item_key(item, locations.get(item.evidence_id, ("", "")))
        if key not in selected:
            selected[key] = item
            order.append(key)
        elif item.confidence > selected[key].confidence:
            selected[key] = item
    return [selected[key] for key in order]
