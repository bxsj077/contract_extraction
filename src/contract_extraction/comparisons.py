from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date, timedelta
from difflib import SequenceMatcher

from .system_models import ContractStructured, Difference, ScopeItem


RESPONSIBILITY_SCORE = {"负责完成": 6, "组织实施": 5, "承担": 5, "提供": 4, "配合": 2, "协助": 1, "不明确": 0}
RISK_ORDER = {"无风险": 0, "待确认": 1, "中风险": 2, "高风险": 3}


def _norm(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", value.lower()).replace("设备", "").replace("系统", "")


def _model_score(forward, backward) -> float:
    left, right = _norm(forward.model), _norm(backward.model)
    if not left or not right or left == "/" or right == "/":
        return 0.0
    if left == right:
        return 1.0
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 5 and longer.startswith(shorter):
        return max(.82, len(shorter) / len(longer))
    return SequenceMatcher(None, left, right).ratio()


def _name_key(value: str) -> str:
    normalized = _norm(value)
    normalized = normalized.replace("国产优质项目配套", "").replace("项目配套", "")
    normalized = normalized.replace("服务费", "").replace("服务", "").replace("费用", "")
    return re.sub(r"180$", "", normalized)


def _semantic_equipment_class(value: str) -> str:
    normalized = _name_key(value)
    if "挂载" in normalized or "安装集成" in normalized:
        return "安装集成"
    if "中间件" in normalized or "中间模块" in normalized:
        return "中间模块"
    if "维护" in normalized:
        return "维护"
    return ""


def _name_score(forward, backward) -> float:
    left, right = _name_key(forward.standard_name), _name_key(backward.standard_name)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_class = _semantic_equipment_class(forward.standard_name)
    right_class = _semantic_equipment_class(backward.standard_name)
    if left_class and left_class == right_class:
        return .88
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 4 and shorter in longer:
        return .9
    backward_model = _norm(backward.model)
    if backward_model and backward_model != "/" and len(backward_model) >= 4 and backward_model in _norm(forward.standard_name):
        return .9
    return SequenceMatcher(None, left, right).ratio()


def _matched_candidates(forward, backward_items):
    model_matches = [item for item in backward_items if _model_score(forward, item) >= .78]
    if model_matches:
        return model_matches, "型号"
    name_matches = [item for item in backward_items if _name_score(forward, item) >= .62]
    return name_matches, "名称"


def compare_equipment(forward: ContractStructured, backward: ContractStructured) -> list[Difference]:
    """Return only forward items that cannot be found in any backward contract.

    Matching prioritizes model plus quantity across the aggregated backward
    contracts, then falls back to a reliable normalized-name plus quantity
    match. Backward-only items do not create a difference row.
    """
    results: list[Difference] = []
    if not forward.equipment:
        return results
    for f in forward.equipment:
        candidates, basis = _matched_candidates(f, backward.equipment)
        known_quantities = [item.quantity for item in candidates if item.quantity is not None]
        covered_quantity = sum(known_quantities) if known_quantities else None
        quantity_satisfied = (f.quantity is None or covered_quantity is None or covered_quantity >= f.quantity)
        if candidates and quantity_satisfied:
            continue
        if candidates:
            description = (f"已按{basis}找到后向清单项，但汇总数量{covered_quantity:g}"
                           f"{f.unit or ''}，低于前向要求{f.quantity:g}{f.unit or ''}。")
            backward_payload = {"匹配依据": basis, "汇总数量": covered_quantity,
                                "匹配项": [asdict(item) for item in candidates]}
            results.append(Difference("设备", "后向数量不足", "高风险", "EQ-002", f.standard_name,
                                      description, asdict(f), backward_payload,
                                      [f.evidence_id] + [item.evidence_id for item in candidates if item.evidence_id]))
        else:
            description = "该前向清单项在全部后向合同汇总清单中均未找到可靠的型号或名称匹配。"
            results.append(Difference("设备", "后向未找到", "高风险", "EQ-001", f.standard_name,
                                      description, asdict(f), {}, [f.evidence_id]))
    return results


def compare_schedule(forward: ContractStructured, backward: ContractStructured, buffer_days: int = 15) -> list[Difference]:
    f, b = forward.time_plan, backward.time_plan
    evidence = f.evidence_ids + b.evidence_ids
    if not all((f.start_date, f.duration_value, b.start_date, b.duration_value, f.finish_date, b.finish_date)):
        result = [Difference("工期", "缺少起算依据", "待确认", "TM-001", "工期衔接",
            f"前向：{f.calculation_status or '缺少可计算信息'}；后向：{b.calculation_status or '缺少可计算信息'}。",
            asdict(f), asdict(b), evidence, True)]
        for node in ("到货", "初验", "终验"):
            fd = f.milestone_details.get(node, {})
            bd = b.milestone_details.get(node, {})
            result.append(Difference("时间节点", "节点时间待确认", "待确认", f"TM-{node}", node,
                f"前向：{fd.get('计算状态', '未提取')}；后向：{bd.get('计算状态', '未提取')}。",
                fd, bd, evidence, True))
        return result
    f_finish, b_finish = date.fromisoformat(f.finish_date), date.fromisoformat(b.finish_date)
    control = f_finish - timedelta(days=buffer_days)
    if b_finish <= control:
        status, risk = "工期满足", "无风险"
    elif b_finish <= f_finish:
        status, risk = "工期紧张", "中风险"
    else:
        status, risk = "明确来不及", "高风险"
    gap = (b_finish - control).days
    result = [Difference("工期", status, risk, "TM-002", "工期衔接",
        f"前向最晚完成{f_finish}，内部控制日期{control}，后向预计完成{b_finish}，相对控制日期差{gap}天。",
        asdict(f), asdict(b), evidence)]
    if f.start_condition_type != b.start_condition_type:
        result.append(Difference("工期", "前后向时间条件不一致", "中风险", "TM-003", "起算条件",
            f"前向为“{f.start_condition_type}”，后向为“{b.start_condition_type}”。", asdict(f), asdict(b), evidence))
    return result


SCOPE_GROUPS = {
    "深化设计": {"方案设计", "深化设计"},
    "软件部署": {"软件部署", "环境搭建", "功能配置"},
    "实施调试": {"旧设备拆除", "上架", "安装", "通电", "设备调试", "系统调试", "系统联调", "系统测试",
                 "桥架施工", "管线施工", "线缆敷设", "光纤熔接", "配电施工", "防雷接地", "机房改造", "土建恢复"},
    "接口与数据": {"接口开发", "数据迁移", "数据治理"},
    "安全加固": {"安全加固"},
    "项目管理": {"项目管理"},
    "培训": {"培训"},
    "试运行": {"试运行"},
    "验收交付": {"初验配合", "终验配合", "竣工资料", "结算资料"},
    "售后质保": {"售后服务", "质保服务"},
}


def _group_scopes(contract: ContractStructured) -> dict[str, ScopeItem]:
    grouped: dict[str, ScopeItem] = {}
    term_to_group = {term: group for group, terms in SCOPE_GROUPS.items() for term in terms}
    for item in contract.scopes:
        group = term_to_group.get(item.scope_item)
        if not group:
            continue
        current = grouped.get(group)
        if not current:
            grouped[group] = ScopeItem(group, item.responsibility, item.object, item.scope_limit,
                                       item.acceptance_requirement, item.direction,
                                       f"{item.scope_item}：{item.original_text}", item.evidence_id, item.confidence)
            continue
        if RESPONSIBILITY_SCORE.get(item.responsibility, 0) > RESPONSIBILITY_SCORE.get(current.responsibility, 0):
            current.responsibility = item.responsibility
        if item.scope_limit in {"全部", "所有"}:
            current.scope_limit = item.scope_limit
        for attribute in ("object", "acceptance_requirement"):
            old, new = getattr(current, attribute), getattr(item, attribute)
            if new and new not in old:
                setattr(current, attribute, "；".join(filter(None, (old, new))))
        evidence_text = f"{item.scope_item}：{item.original_text}"
        if evidence_text not in current.original_text:
            current.original_text = f"{current.original_text}；{evidence_text}"
        if item.evidence_id and item.evidence_id not in current.evidence_id:
            current.evidence_id = "；".join(filter(None, (current.evidence_id, item.evidence_id)))
        current.confidence = max(current.confidence, item.confidence)
    return grouped


def compare_scopes(forward: ContractStructured, backward: ContractStructured) -> list[Difference]:
    results: list[Difference] = []
    forward_map = _group_scopes(forward)
    backward_map = _group_scopes(backward)
    for name in SCOPE_GROUPS:
        f, b = forward_map.get(name), backward_map.get(name)
        if not f and not b:
            continue
        if not f and b:
            results.append(Difference("实施内容", "后向新增内容", "无风险", "SC-005", name,
                                      "后向新增主要实施内容。", {}, asdict(b), [b.evidence_id]))
            continue
        if not b:
            results.append(Difference("实施内容", "实施内容缺失", "高风险", "SC-001", name,
                "前向承诺的实施内容在后向合同中未提取到。", asdict(f), {}, [f.evidence_id]))
        elif RESPONSIBILITY_SCORE.get(b.responsibility, 0) < RESPONSIBILITY_SCORE.get(f.responsibility, 0):
            results.append(Difference("实施内容", "责任程度弱化", "中风险", "SC-002", name,
                f"责任由前向“{f.responsibility}”弱化为后向“{b.responsibility}”。", asdict(f), asdict(b), [f.evidence_id, b.evidence_id]))
        elif f.scope_limit in {"全部", "所有"} and b.scope_limit not in {"全部", "所有"}:
            results.append(Difference("实施内容", "实施范围不足", "中风险", "SC-003", name,
                "前向要求全部范围，后向未明确完整覆盖。", asdict(f), asdict(b), [f.evidence_id, b.evidence_id]))
        else:
            results.append(Difference("实施内容", "完全覆盖", "无风险", "SC-004", name,
                                      "实施事项及责任强度满足规则。", asdict(f), asdict(b), [f.evidence_id, b.evidence_id]))
    return results


def overall_risk(differences: list[Difference]) -> str:
    return max((d.risk_level for d in differences), key=lambda x: RISK_ORDER.get(x, 1), default="待确认")
