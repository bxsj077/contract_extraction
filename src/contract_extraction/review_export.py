from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .storage import ReviewStore


def _flatten(prefix: str, value: Any, row: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _flatten(f"{prefix}.{key}" if prefix else key, child, row)
    elif not isinstance(value, list):
        row[prefix] = value


def _add_sheet(wb: Workbook, title: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(title)
    headers = list(dict.fromkeys(key for row in rows for key in row)) or ["无数据"]
    ws.append(headers)
    for row in rows:
        ws.append([json.dumps(row.get(h), ensure_ascii=False) if isinstance(row.get(h), (dict, list)) else row.get(h, "") for h in headers])
    ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor="1F4E78")
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(i)].width = min(max(12, len(h) * 2), 42)


def export_review(store: ReviewStore, path: Path) -> Path:
    projects = [item["payload"] for item in store.list_projects()]
    summary = []
    contracts = []
    equipment = []; schedules = []; scopes = []; timeline = []
    for p in projects:
        summary.append({"项目编码": p["project_code"], "处理状态": p["status"], "风险等级": p["risk_level"],
                        "设备差异数": len(p.get("equipment_differences", [])), "工期风险数": len(p.get("schedule_differences", [])),
                        "实施差异数": len(p.get("scope_differences", [])), "待复核数": len(p.get("review_issues", [])), "处理时间": p.get("processed_at", "")})
        for direction in ("forward", "backward"):
            if p.get(direction):
                row = {"项目编码": p["project_code"], "合同方向": "前向" if direction == "forward" else "后向"}
                _flatten("", {k: v for k, v in p[direction].items() if k not in {"equipment", "scopes", "evidence"}}, row)
                contracts.append(row)
        for target, key in ((equipment, "equipment_differences"), (schedules, "schedule_differences"), (scopes, "scope_differences")):
            target.extend({"项目编码": p["project_code"], **d} for d in p.get(key, []))
        timeline.extend({"项目编码": p["project_code"], **x} for x in p.get("timeline", []))
    wb = Workbook(); wb.remove(wb.active)
    for title, rows in (("项目处理汇总", summary), ("前后向合同解析结果", contracts), ("设备材料差异", equipment),
                        ("工期衔接风险", schedules), ("实施内容差异", scopes), ("项目时间轴", timeline),
                        ("待人工复核", store.list_issues())):
        _add_sheet(wb, title, rows)
    path.parent.mkdir(parents=True, exist_ok=True); wb.save(path)
    return path
