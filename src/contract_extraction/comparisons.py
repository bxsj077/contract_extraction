from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date, timedelta
from difflib import SequenceMatcher

from .system_models import ContractStructured, Difference


RESPONSIBILITY_SCORE = {"负责完成": 6, "组织实施": 5, "承担": 5, "提供": 4, "配合": 2, "协助": 1, "不明确": 0}
RISK_ORDER = {"无风险": 0, "待确认": 1, "中风险": 2, "高风险": 3}


def _norm(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", value.lower()).replace("设备", "").replace("系统", "")


def _item_score(forward, backward) -> float:
    if forward.model and backward.model and _norm(forward.model) == _norm(backward.model):
        return 1.0
    return SequenceMatcher(None, _norm(forward.standard_name), _norm(backward.standard_name)).ratio()


def compare_equipment(forward: ContractStructured, backward: ContractStructured) -> list[Difference]:
    results: list[Difference] = []
    if not forward.equipment and not backward.equipment:
        if forward.procurement_involved is False and backward.procurement_involved is False:
            return [Difference("设备材料", "双方均不涉及货物采购", "无风险", "EQ-000", "货物采购适用性",
                f"前向：{forward.procurement_note}；后向：{backward.procurement_note}")]
        return [Difference("设备材料", "未提取到清单", "待确认", "EQ-000", "货物采购适用性",
            f"前向：{forward.procurement_note}；后向：{backward.procurement_note}", needs_review=True)]
    matched: set[int] = set()
    for f in forward.equipment:
        choices = sorted(((i, _item_score(f, b)) for i, b in enumerate(backward.equipment)), key=lambda x: x[1], reverse=True)
        if not choices or choices[0][1] < .62:
            results.append(Difference("设备", "后向未采购", "高风险", "EQ-001", f.standard_name,
                "前向交付项在后向采购清单中未找到可靠匹配。", asdict(f), {}, [f.evidence_id]))
            continue
        index, score = choices[0]
        b = backward.equipment[index]
        matched.add(index)
        if score < .82:
            status, risk, desc, review = "疑似匹配", "待确认", f"名称相似度{score:.0%}，需要人工确认。", True
        elif f.unit != b.unit:
            status, risk, desc, review = "单位无法换算", "待确认", f"前向单位{f.unit}、后向单位{b.unit}。", True
        elif f.quantity is not None and b.quantity is not None and b.quantity < f.quantity:
            status, risk, desc, review = "数量不足", "中风险", f"后向数量比前向少{f.quantity-b.quantity:g}{f.unit}。", False
        else:
            status, risk, desc, review = "完全覆盖", "无风险", "名称/型号及数量满足当前确定性规则。", False
        results.append(Difference("设备", status, risk, "EQ-002", f.standard_name, desc, asdict(f), asdict(b),
                                  [f.evidence_id, b.evidence_id], review))
    for i, item in enumerate(backward.equipment):
        if i not in matched:
            results.append(Difference("设备", "后向新增", "无风险", "EQ-003", item.standard_name,
                                      "后向存在前向未列出的新增采购项。", {}, asdict(item), [item.evidence_id]))
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


def compare_scopes(forward: ContractStructured, backward: ContractStructured) -> list[Difference]:
    results: list[Difference] = []
    backward_map = {item.scope_item: item for item in backward.scopes}
    forward_names = {item.scope_item for item in forward.scopes}
    for f in forward.scopes:
        b = backward_map.get(f.scope_item)
        if not b:
            results.append(Difference("实施内容", "实施内容缺失", "高风险", "SC-001", f.scope_item,
                "前向承诺的实施内容在后向合同中未提取到。", asdict(f), {}, [f.evidence_id]))
        elif RESPONSIBILITY_SCORE.get(b.responsibility, 0) < RESPONSIBILITY_SCORE.get(f.responsibility, 0):
            results.append(Difference("实施内容", "责任程度弱化", "中风险", "SC-002", f.scope_item,
                f"责任由前向“{f.responsibility}”弱化为后向“{b.responsibility}”。", asdict(f), asdict(b), [f.evidence_id, b.evidence_id]))
        elif f.scope_limit in {"全部", "所有"} and b.scope_limit not in {"全部", "所有"}:
            results.append(Difference("实施内容", "实施范围不足", "中风险", "SC-003", f.scope_item,
                "前向要求全部范围，后向未明确完整覆盖。", asdict(f), asdict(b), [f.evidence_id, b.evidence_id]))
        else:
            results.append(Difference("实施内容", "完全覆盖", "无风险", "SC-004", f.scope_item,
                                      "实施事项及责任强度满足规则。", asdict(f), asdict(b), [f.evidence_id, b.evidence_id]))
    for b in backward.scopes:
        if b.scope_item not in forward_names:
            results.append(Difference("实施内容", "后向新增内容", "无风险", "SC-005", b.scope_item,
                                      "后向新增实施内容。", {}, asdict(b), [b.evidence_id]))
    return results


def overall_risk(differences: list[Difference]) -> str:
    return max((d.risk_level for d in differences), key=lambda x: RISK_ORDER.get(x, 1), default="待确认")
