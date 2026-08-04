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
    "time_plan.duration_raw": "工期约定原文", "time_plan.calculation_status": "工期计算状态",
    "time_plan.duration_conclusion": "工期提取结论",
    "time_plan.confidence": "工期置信度", "key_clauses.服务内容": "服务内容条款",
    "key_clauses.乙方义务": "乙方义务条款", "key_clauses.关键条款": "其他关键条款",
    "parse_metadata.file_hash": "文件哈希", "parse_metadata.parse_version": "解析版本",
    "parse_metadata.aggregation": "后向汇总方式", "parse_metadata.contract_count": "后向合同数量",
    "procurement_involved": "是否涉及货物采购", "procurement_note": "货物采购说明",
    "standard_name": "标准名称", "original_name": "合同原始名称", "brand": "品牌",
    "model": "型号规格", "unit": "单位", "quantity": "数量", "technical_parameters": "技术参数",
    "evidence_id": "证据编号", "confidence": "提取置信度",
    "category": "差异类别", "status": "判断结果", "risk_level": "风险等级", "rule_id": "规则编号",
    "title": "审查事项", "description": "风险说明", "forward": "前向结构化内容",
    "backward": "后向结构化内容", "evidence_ids": "证据编号", "needs_review": "是否需人工复核",
    "node": "时间节点", "difference": "差异说明", "id": "复核编号", "resolution": "复核结论",
    "created_at": "创建时间", "resolved_at": "复核完成时间",
    "contract_key": "合同标识", "field_path": "纠正字段", "corrected_value": "人工确认值",
    "note": "纠正说明", "updated_at": "更新时间",
}


def _header_cn(value: str) -> str:
    if value in HEADER_CN:
        return HEADER_CN[value]
    prefix = "time_plan.milestones."
    if value.startswith(prefix):
        return f"时间节点-{value[len(prefix):]}"
    detail_prefix = "time_plan.milestone_details."
    return f"时间节点明细-{value[len(detail_prefix):]}" if value.startswith(detail_prefix) else value


def _flatten(prefix: str, value: Any, row: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _flatten(f"{prefix}.{key}" if prefix else key, child, row)
    elif not isinstance(value, list):
        row[prefix] = value


def _cell_value(value: Any) -> Any:
    if isinstance(value, dict):
        if "standard_name" in value:
            parts = [f"名称：{value.get('standard_name', '')}"]
            if value.get("model"): parts.append(f"型号：{value['model']}")
            if value.get("quantity") is not None: parts.append(f"数量：{value['quantity']}{value.get('unit', '')}")
            return "；".join(parts)
        if "calculation_status" in value:
            return "；".join(x for x in (value.get("duration_raw", ""), value.get("calculation_status", ""),
                f"起算日期：{value.get('start_date')}" if value.get("start_date") else "",
                f"预计完成：{value.get('finish_date')}" if value.get("finish_date") else "") if x)
        if "计算状态" in value:
            return "；".join(x for x in (str(value.get("计算状态", "")), str(value.get("原文", "")),
                                          str(value.get("计算日期", ""))) if x)
        if "scope_item" in value:
            return "；".join(x for x in (str(value.get("scope_item", "")), str(value.get("responsibility", "")),
                                          str(value.get("original_text", ""))) if x)
        text = json.dumps(value, ensure_ascii=False)
        return text if len(text) <= 220 else text[:217] + "..."
    if isinstance(value, list):
        text = "；".join(str(x) for x in value)
        return text if len(text) <= 220 else text[:217] + "..."
    if isinstance(value, str) and len(value) > 220:
        return value[:217] + "..."
    return value


def _add_sheet(wb: Workbook, title: str, rows: list[dict[str, Any]]) -> None:
    ws = wb.create_sheet(title)
    rows = [{_header_cn(key): value for key, value in row.items()} for row in rows]
    headers = list(dict.fromkeys(key for row in rows for key in row)) or ["无数据"]
    ws.append(headers)
    for row in rows:
        ws.append([_cell_value(row.get(h, "")) for h in headers])
    ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor="1F4E78")
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for row_number in range(2, ws.max_row + 1):
        ws.row_dimensions[row_number].height = 90
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(i)].width = min(max(12, len(h) * 2), 42)


def export_review(store: ReviewStore, path: Path) -> Path:
    projects = [item["payload"] for item in store.list_projects()]
    summary = []
    contracts = []
    equipment = []; equipment_items = []; schedules = []; scopes = []; timeline = []; parse_statuses = []
    for p in projects:
        summary.append({"项目编码": p["project_code"], "处理状态": p["status"], "风险等级": p["risk_level"],
                        "设备差异数": len(p.get("equipment_differences", [])), "工期风险数": len(p.get("schedule_differences", [])),
                        "实施差异数": len(p.get("scope_differences", [])), "待复核数": len(p.get("review_issues", [])), "处理时间": p.get("processed_at", "")})
        individual_contracts = ([p["forward"]] if p.get("forward") else []) + p.get("backward_contracts", [])
        for contract in individual_contracts:
            if contract:
                row = {"项目编码": p["project_code"], "合同方向": contract.get("direction", "")}
                _flatten("", {k: v for k, v in contract.items() if k not in {"equipment", "scopes", "evidence"}}, row)
                if "time_plan.duration_conclusion" in row:
                    conclusion = row.pop("time_plan.duration_conclusion")
                    reordered = {}
                    for key, value in row.items():
                        reordered[key] = value
                        if key == "time_plan.duration_unit":
                            reordered["time_plan.duration_conclusion"] = conclusion
                    row = reordered
                contracts.append(row)
                if contract.get("equipment"):
                    for item in contract["equipment"]:
                        equipment_items.append({"项目编码": p["project_code"], "合同方向": contract.get("direction", ""),
                            "合同名称": contract.get("contract_name", ""), **item, "货物采购说明": contract.get("procurement_note", "")})
                else:
                    equipment_items.append({"项目编码": p["project_code"], "合同方向": contract.get("direction", ""),
                        "合同名称": contract.get("contract_name", ""), "清单提取结果": "无清单项",
                        "货物采购说明": contract.get("procurement_note", "")})
        for target, key in ((equipment, "equipment_differences"), (schedules, "schedule_differences"), (scopes, "scope_differences")):
            target.extend({"项目编码": p["project_code"], **d} for d in p.get(key, []))
        timeline.extend({"项目编码": p["project_code"], **x} for x in p.get("timeline", []))
        parse_statuses.extend({"项目编码": p["project_code"], **x} for x in p.get("contract_parse_statuses", []))
    wb = Workbook(); wb.remove(wb.active)
    for title, rows in (("项目处理汇总", summary), ("合同解析状态", parse_statuses), ("前后向合同解析结果", contracts),
                        ("设备材料清单", equipment_items), ("设备材料差异", equipment),
                        ("工期衔接风险", schedules), ("实施内容差异", scopes), ("项目时间轴", timeline),
                        ("人工纠正记录", store.list_corrections()), ("待人工复核", store.list_issues())):
        _add_sheet(wb, title, rows)
    path.parent.mkdir(parents=True, exist_ok=True); wb.save(path)
    return path
