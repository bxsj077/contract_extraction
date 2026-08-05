from __future__ import annotations

import json
import re
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
    "evidence_id": "证据编号", "confidence": "提取置信度", "list_type": "清单性质",
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
        ws.row_dimensions[row_number].height = 42
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(i)].width = min(max(12, len(h) * 2), 35)


def _duration_display(plan: dict[str, Any]) -> str:
    """Return one business-facing duration value without losing textual clauses."""
    value = plan.get("duration_value")
    unit = str(plan.get("duration_unit") or "").strip()
    if value not in (None, ""):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return f"{value}{unit}"
    return str(plan.get("duration_conclusion") or plan.get("duration_raw") or "").strip()


def _safe_filename(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .") or "未命名项目"


def export_review(store: ReviewStore, path: Path, project_codes: set[str] | None = None) -> Path:
    projects = [item["payload"] for item in store.list_projects()
                if project_codes is None or item["project_code"] in project_codes]
    summary: list[dict[str, Any]] = []
    contracts: list[dict[str, Any]] = []
    time_reviews: list[dict[str, Any]] = []
    equipment_rows: list[dict[str, Any]] = []
    scopes: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    for p in projects:
        equipment_differences = p.get("equipment_differences", [])
        equipment_risk_count = sum(d.get("risk_level") not in {"", "无风险"} for d in equipment_differences)
        summary.append({"项目编码": p["project_code"], "处理状态": p["status"], "风险等级": p["risk_level"],
                        "设备风险数": equipment_risk_count,
                        "前后向完全覆盖项数": sum(d.get("status") == "完全覆盖" for d in equipment_differences),
                        "后向备件要求项数": sum(d.get("status") == "后向新增备件要求" for d in equipment_differences),
                        "工期风险数": len(p.get("schedule_differences", [])),
                        "收入收款计划差异数": len(p.get("plan_differences", [])),
                        "实施差异数": len(p.get("scope_differences", [])), "待复核数": len(p.get("review_issues", [])),
                        "处理时间": p.get("processed_at", "")})
        difference_by_evidence: dict[str, dict[str, Any]] = {}
        for difference in equipment_differences:
            for evidence_id in difference.get("evidence_ids", []):
                difference_by_evidence[evidence_id] = difference
        individual_contracts = ([p["forward"]] if p.get("forward") else []) + p.get("backward_contracts", [])
        for contract in individual_contracts:
            if contract:
                plan = contract.get("time_plan", {})
                details = plan.get("milestone_details", {})
                metadata = contract.get("parse_metadata", {})
                contracts.append({
                    "项目编码": p["project_code"], "合同方向": contract.get("direction", ""),
                    "合同编号": contract.get("contract_number", ""), "合同名称": contract.get("contract_name", ""),
                    "甲方": contract.get("party_a", ""), "乙方": contract.get("party_b", ""),
                    "签订日期": contract.get("sign_date", ""), "生效日期": contract.get("effective_date", ""),
                    "合同性质": contract.get("contract_type", ""), "货物采购说明": contract.get("procurement_note", ""),
                    "工期": _duration_display(plan),
                    "工期提取结论": plan.get("duration_conclusion", ""), "合同履约起始日期": plan.get("start_date", ""),
                    "合同履约截止日期": plan.get("finish_date", ""), "起算条件": plan.get("start_condition_type", ""),
                    "工期约定原文": plan.get("duration_raw", ""), "工期计算状态": plan.get("calculation_status", ""),
                    "到货节点": _cell_value(details.get("到货", {})), "初验节点": _cell_value(details.get("初验", {})),
                    "终验节点": _cell_value(details.get("终验", {})),
                    "服务内容": contract.get("key_clauses", {}).get("服务内容", ""),
                    "乙方义务": contract.get("key_clauses", {}).get("乙方义务", ""),
                    "其他关键条款": contract.get("key_clauses", {}).get("关键条款", ""),
                    "页数": metadata.get("page_count", 0), "OCR错误数": metadata.get("ocr_error_count", 0),
                    "解析状态": metadata.get("parse_status", "未知")})
                if contract.get("equipment"):
                    for item in contract["equipment"]:
                        difference = difference_by_evidence.get(item.get("evidence_id", ""), {})
                        equipment_rows.append({
                            "记录类型": "清单项", "项目编码": p["project_code"],
                            "合同方向": contract.get("direction", ""), "合同名称": contract.get("contract_name", ""),
                            "清单性质": item.get("list_type", "采购交付清单"), "类别": item.get("category", ""),
                            "标准名称": item.get("standard_name", ""), "合同原始名称": item.get("original_name", ""),
                            "品牌": item.get("brand", ""), "型号规格": item.get("model", ""),
                            "单位": item.get("unit", ""), "数量": item.get("quantity"),
                            "技术参数": item.get("technical_parameters", {}),
                            "判断结果": difference.get("status", "待匹配"),
                            "风险等级": difference.get("risk_level", "待确认"),
                            "风险说明": difference.get("description", "未形成可靠差异判断"),
                            "证据编号": item.get("evidence_id", ""), "提取置信度": item.get("confidence"),
                            "货物采购说明": contract.get("procurement_note", ""),
                        })
                else:
                    equipment_rows.append({"记录类型": "清单提取说明", "项目编码": p["project_code"], "合同方向": contract.get("direction", ""),
                        "合同名称": contract.get("contract_name", ""), "清单提取结果": "无清单项",
                        "货物采购说明": contract.get("procurement_note", "")})
        time_reviews.extend({"记录类型": "前后向工期/节点", "项目编码": p["project_code"], **d}
                            for d in p.get("schedule_differences", []))
        time_reviews.extend({"记录类型": "收入收款计划复核", "项目编码": p["project_code"], **d}
                            for d in p.get("plan_differences", []))
        scopes.extend({"项目编码": p["project_code"], **d} for d in p.get("scope_differences", []))
        review_rows.extend({"记录类型": "待人工复核", "项目编码": p["project_code"], **x}
                           for x in p.get("review_issues", []))
    review_rows.extend({"记录类型": "人工纠正", **x} for x in store.list_corrections()
                       if project_codes is None or x.get("project_code") in project_codes)
    wb = Workbook(); wb.remove(wb.active)
    sheet_rows = [("项目审查汇总", summary), ("合同解析结果", contracts),
                  ("时间及收入计划复核", time_reviews), ("设备清单及差异", equipment_rows)]
    if scopes:
        sheet_rows.append(("实施内容差异", scopes))
    sheet_rows.append(("待复核及人工纠正", review_rows))
    for title, rows in sheet_rows:
        _add_sheet(wb, title, rows)
    path.parent.mkdir(parents=True, exist_ok=True); wb.save(path)
    return path


def export_project_reviews(store: ReviewStore, output_dir: Path, timestamp: str,
                           project_codes: set[str] | None = None) -> list[Path]:
    """Create one complete review workbook for every selected project."""
    paths: list[Path] = []
    for item in store.list_projects():
        project_code = item["project_code"]
        if project_codes is not None and project_code not in project_codes:
            continue
        paths.append(export_project_review(store, output_dir, project_code, timestamp))
    return paths


def export_project_review(store: ReviewStore, output_dir: Path, project_code: str, timestamp: str) -> Path:
    path = output_dir / f"{_safe_filename(project_code)}_前后向合同履约风险审查_{timestamp}.xlsx"
    return export_review(store, path, {project_code})
