from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .storage import ReviewStore


HEADER_CN = {
    "project_code": "结构化项目编码", "direction": "结构化合同方向", "contract_number": "合同编号",
    "contract_name": "合同名称", "party_a": "甲方", "party_b": "乙方", "amount_yuan": "合同金额（元）",
    "sign_date": "签订日期", "effective_date": "生效日期", "contract_type": "合同性质",
    "time_plan.duration_value": "工期数值", "time_plan.duration_unit": "工期单位",
    "time_plan.start_condition_type": "起算条件类型", "time_plan.start_condition_text": "起算条件原文",
    "time_plan.start_date": "实际起算日期", "time_plan.finish_date": "预计完成日期",
    "time_plan.completion_node": "完成节点", "time_plan.fixed_deadline": "固定截止日期",
    "time_plan.confidence": "工期置信度", "key_clauses.服务内容": "服务内容条款",
    "key_clauses.乙方义务": "乙方义务条款", "key_clauses.关键条款": "其他关键条款",
    "parse_metadata.file_hash": "文件哈希", "parse_metadata.parse_version": "解析版本",
    "parse_metadata.aggregation": "后向汇总方式", "parse_metadata.contract_count": "后向合同数量",
    "category": "差异类别", "status": "判断结果", "risk_level": "风险等级", "rule_id": "规则编号",
    "title": "审查事项", "description": "风险说明", "forward": "前向结构化内容",
    "backward": "后向结构化内容", "evidence_ids": "证据编号", "needs_review": "是否需人工复核",
    "node": "时间节点", "difference": "差异说明", "id": "复核编号", "resolution": "复核结论",
    "created_at": "创建时间", "resolved_at": "复核完成时间",
}


def _header_cn(value: str) -> str:
    if value in HEADER_CN:
        return HEADER_CN[value]
    prefix = "time_plan.milestones."
    return f"时间节点-{value[len(prefix):]}" if value.startswith(prefix) else value


def _flatten(prefix: str, value: Any, row: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _flatten(f"{prefix}.{key}" if prefix else key, child, row)
    elif not isinstance(value, list):
        row[prefix] = value


def _add_sheet(wb: Workbook, title: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(title)
    rows = [{_header_cn(key): value for key, value in row.items()} for row in rows]
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
