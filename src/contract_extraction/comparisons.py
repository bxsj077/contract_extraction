from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date
from difflib import SequenceMatcher

from .system_models import ContractStructured, Difference, ScopeItem

NON_EQUIPMENT_CATEGORIES = {
    "软件/服务",
    "服务对象",
    "备件",
}

NON_EQUIPMENT_LIST_TYPES = {
    "维保/服务对象清单",
    "现场备件库清单",
}

def project_overall_risk(
    equipment_differences: list[Difference],
    schedule_differences: list[Difference],
    scope_differences: list[Difference],
    plan_differences: list[Difference],
    review_issue_count: int = 0,
) -> str:
    """
    计算项目级总体风险。

    项目级高/中风险仅由以下核心履约因素触发：
    1. 工期风险；
    2. 到货风险；
    3. 前向设备/材料清单未被后向完整覆盖。

    初验、终验、实施内容、收入收款计划以及普通待复核事项，
    仍保留各自明细风险等级，但最多只将项目总体风险提升到“低风险”。
    """

    # ---------------------------------------------------------
    # 1. 设备/材料覆盖风险
    # ---------------------------------------------------------
    # compare_equipment 当前只返回真正未覆盖、数量不足等差异，
    # 因此只要出现高风险设备差异，就认为项目存在核心履约风险。
    if any(
        item.risk_level == "高风险"
        for item in equipment_differences
    ):
        return "高风险"

    # 为以后设备规则支持“中风险”预留
    if any(
        item.risk_level == "中风险"
        for item in equipment_differences
    ):
        return "中风险"

    # ---------------------------------------------------------
    # 2. 工期 / 到货
    # ---------------------------------------------------------
    critical_schedule = []

    for item in schedule_differences:
        # 工期，包括：
        # - 工期本身
        # - 起算条件
        if item.category == "工期":
            critical_schedule.append(item)
            continue

        # 到货在 compare_schedule 中 category 是“时间节点”，
        # 因此需要通过 title 判断
        if (
            item.category == "时间节点"
            and str(item.title).startswith("到货")
        ):
            critical_schedule.append(item)

    if any(
        item.risk_level == "高风险"
        for item in critical_schedule
    ):
        return "高风险"

    if any(
        item.risk_level == "中风险"
        for item in critical_schedule
    ):
        return "中风险"

    # ---------------------------------------------------------
    # 3. 其他问题最多定义为低风险
    # ---------------------------------------------------------
    other_findings = (
        equipment_differences
        + schedule_differences
        + scope_differences
        + plan_differences
    )

    has_other_problem = any(
        item.risk_level in {
            "高风险",
            "中风险",
            "待确认",
        }
        for item in other_findings
    )

    if has_other_problem or review_issue_count > 0:
        return "低风险"

    return "无风险"

def _is_equipment_coverage_item(item) -> bool:
    """
    判断一个清单项是否应该进入“设备未覆盖风险”比较。

    软件实施、系统集成、运维服务等服务类项目，
    不属于设备，不参与设备覆盖判断。
    """
    if item.category in NON_EQUIPMENT_CATEGORIES:
        return False

    if item.list_type in NON_EQUIPMENT_LIST_TYPES:
        return False

    # 兼容旧缓存中尚未正确写入 category 的服务类清单项。这里只影响
    # “设备未覆盖”比较，不删除结构化结果，实施和服务信息仍会保留。
    name = str(item.standard_name or item.original_name or "")
    if re.search(r"软件|平台(?:开发|服务)|算法|中间件|部署实施|(?:系统|安装)集成|运维|运营费|维护服务|服务(?!器)|挂载费", name):
        return False
    if (item.unit in {"项", "路"} and not item.brand and not item.model
            and re.search(r"平台|费用|费$|其他.*项目配套", name)):
        return False

    return True
RESPONSIBILITY_SCORE = {"负责完成": 6, "组织实施": 5, "承担": 5, "提供": 4, "配合": 2, "协助": 1, "不明确": 0}
RISK_ORDER = {"无风险": 0, "低风险": 1, "待确认": 1, "中风险": 2, "高风险": 3}


def _norm(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(value or "").lower()).replace("设备", "").replace("系统", "")


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
    normalized = normalized.replace("接闪器", "避雷针")
    normalized = normalized.replace("国产优质项目配套", "").replace("项目配套", "")
    normalized = normalized.replace("服务费", "").replace("服务", "").replace("费用", "")
    return re.sub(r"180$", "", normalized)


def _semantic_equipment_class(value: str) -> str:
    normalized = _name_key(value)
    if "避雷针" in normalized:
        return "避雷针"
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
    forward_name = _norm(forward.standard_name)
    if backward_model and backward_model != "/":
        if ((len(backward_model) >= 4 or re.fullmatch(r"\d+(?:\.\d+)?[kmg](?:bps)?", backward_model))
                and backward_model in forward_name):
            return .9
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._/\-]{3,}", forward.standard_name):
            token = _norm(token)
            shorter, longer = sorted((token, backward_model), key=len)
            if len(shorter) >= 5 and longer.startswith(shorter):
                return .9
    return SequenceMatcher(None, left, right).ratio()


def _known_text(value: str) -> bool:
    return _norm(value) not in {"", "无", "未知", "未提供", "不详", "/", "-"}


def _brand_score(forward, backward) -> float:
    left, right = _norm(forward.brand), _norm(backward.brand)
    if not left and not right:
        return .5
    if not left or not right:
        return .6
    if left == right:
        return 1.0
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 2 and shorter in longer:
        return .9
    return SequenceMatcher(None, left, right).ratio()


def _description_text(item) -> str:
    """Return normalized product description, excluding price and grouping metadata."""
    parts = [item.original_name or ""]
    ignored = {"清单分组", "含税单价", "含税总价", "增值税税率", "单价", "总价", "税率"}
    for key, value in sorted((item.technical_parameters or {}).items()):
        if key in ignored or value in (None, "", [], {}):
            continue
        if isinstance(value, (dict, list, tuple, set)):
            value = str(value)
        parts.append(f"{key}{value}")
    return _norm(" ".join(str(part) for part in parts))


def _description_score(forward, backward) -> float:
    left, right = _description_text(forward), _description_text(backward)
    if not left and not right:
        return .5
    if not left or not right:
        return .5
    if left == right:
        return 1.0
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 8 and shorter in longer:
        return max(.82, len(shorter) / len(longer))
    return SequenceMatcher(None, left, right).ratio()


def _quantity_score(forward, backward) -> float:
    left, right = forward.quantity, backward.quantity
    if left is None or right is None:
        return .5
    if left == right:
        return 1.0
    if left <= 0 or right <= 0:
        return 0.0
    ratio = min(left, right) / max(left, right)
    return max(ratio, .8 if right >= left else 0.0)


def _unit_score(forward, backward) -> float:
    left, right = _norm(forward.unit), _norm(backward.unit)
    if not left or not right:
        return .5
    return 1.0 if left == right else 0.0


def _equipment_pair_score(forward, backward) -> tuple[float, dict[str, float]] | None:
    """Score one forward/backward pair using model, brand, name, description and quantity.

    Model remains the primary identity. Brand conflicts reject weak/fuzzy model
    matches, while description and quantity are used to disambiguate OCR variants
    and generic product names.
    """
    model = _model_score(forward, backward)
    name = _name_score(forward, backward)
    brand = _brand_score(forward, backward)
    description = _description_score(forward, backward)
    quantity = _quantity_score(forward, backward)
    unit = _unit_score(forward, backward)
    forward_model_known = _known_text(forward.model)
    backward_model_known = _known_text(backward.model)
    brands_known = _known_text(forward.brand) and _known_text(backward.brand)
    brand_conflict = brands_known and brand < .55

    if brand_conflict:
        return None

    # Known but clearly different models must not be rescued by a generic name.
    if forward_model_known and backward_model_known and model < .65:
        return None

    if model >= .92:
        pass
    elif model >= .78:
        if max(name, description) < .48 and brand < .82:
            return None
    else:
        if name < .72 and not (name >= .62 and description >= .65):
            return None

    score = (.45 * model + .18 * name + .14 * brand
             + .13 * description + .07 * quantity + .03 * unit)
    if model < .78:
        score = (.37 * name + .22 * description + .17 * brand
                 + .14 * quantity + .10 * unit)
    details = {"型号": model, "品牌": brand, "名称": name,
               "描述": description, "数量": quantity, "单位": unit}
    return score, details


def _matched_candidates(forward, backward_items):
    ranked = []
    for item in backward_items:
        scored = _equipment_pair_score(forward, item)
        if scored:
            score, details = scored
            ranked.append((score, item, details))
    if not ranked:
        return [], "品牌、型号、数量及描述综合匹配"

    ranked.sort(key=lambda row: row[0], reverse=True)
    best_score = ranked[0][0]
    best_has_model = ranked[0][2]["型号"] >= .78
    tolerance = .12 if best_has_model else .08
    selected = [row for row in ranked
                if row[0] >= max(.58, best_score - tolerance)
                and (not best_has_model or row[2]["型号"] >= .78)]
    fields = [field for field in ("品牌", "型号", "数量", "描述")
              if any(row[2][field] >= .78 for row in selected)]
    basis = "+".join(fields) + "综合匹配" if fields else "名称与描述综合匹配"
    return [row[1] for row in selected], basis


def compare_equipment(forward: ContractStructured,backward: ContractStructured) -> list[Difference]:
    """
    仅比较前后向合同中的实物设备/材料覆盖情况。

    软件部署、系统集成、实施服务、运维服务等
    非实物设备项目不进入“设备未覆盖风险”。
    """

    results: list[Difference] = []

    forward_items = [
        item
        for item in forward.equipment
        if _is_equipment_coverage_item(item)
    ]

    backward_items = [
        item
        for item in backward.equipment
        if _is_equipment_coverage_item(item)
    ]

    if not forward_items:
        return results

    for f in forward_items:
        candidates, basis = _matched_candidates(
            f,
            backward_items
        )

        known_quantities = [
            item.quantity
            for item in candidates
            if item.quantity is not None
        ]

        covered_quantity = (
            sum(known_quantities)
            if known_quantities
            else None
        )

        quantity_satisfied = (
            f.quantity is None
            or covered_quantity is None
            or covered_quantity >= f.quantity
        )

        if candidates and quantity_satisfied:
            continue

        if candidates:
            description = (
                f"已按{basis}找到后向清单项，但汇总数量"
                f"{covered_quantity:g}{f.unit or ''}，"
                f"低于前向要求{f.quantity:g}{f.unit or ''}。"
            )

            backward_payload = {
                "匹配依据": basis,
                "汇总数量": covered_quantity,
                "匹配项": [
                    asdict(item)
                    for item in candidates
                ],
            }

            results.append(
                Difference(
                    "设备",
                    "后向数量不足",
                    "高风险",
                    "EQ-002",
                    f.standard_name,
                    description,
                    asdict(f),
                    backward_payload,
                    [f.evidence_id]
                    + [
                        item.evidence_id
                        for item in candidates
                        if item.evidence_id
                    ],
                )
            )

        else:
            description = (
                "该前向设备在全部后向合同汇总清单中"
                "均未找到可靠的型号或名称匹配。"
            )

            results.append(
                Difference(
                    "设备",
                    "后向未找到",
                    "高风险",
                    "EQ-001",
                    f.standard_name,
                    description,
                    asdict(f),
                    {},
                    [f.evidence_id],
                )
            )

    return results


def _schedule_date(plan, node: str) -> date | None:
    if node == "工期":
        raw = plan.finish_date
    else:
        detail = plan.milestone_details.get(node, {}) or {}
        if re.search(r"时间异常|日期异常", str(detail.get("计算状态") or "")):
            return None
        raw = detail.get("计算日期") or plan.milestones.get(node)
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def _schedule_requirement(plan, node: str) -> bool:
    if node == "工期":
        return bool(plan.duration_value is not None or plan.duration_raw or plan.duration_conclusion)
    detail = plan.milestone_details.get(node, {}) or {}
    return bool(detail.get("原文") or detail.get("相对期限") or detail.get("计算日期") or plan.milestones.get(node))


def compare_schedule(forward: ContractStructured, backward: ContractStructured, buffer_days: int = 15,
                     backward_label: str = "后向合同") -> list[Difference]:
    f, b = forward.time_plan, backward.time_plan
    evidence = f.evidence_ids + b.evidence_ids
    _ = buffer_days  # Kept for configuration/API compatibility; business rule now compares the contractual dates directly.
    result: list[Difference] = []
    for index, node in enumerate(("工期", "到货", "初验", "终验"), 1):
        forward_date = _schedule_date(f, node)
        backward_date = _schedule_date(b, node)
        category = "工期" if node == "工期" else "时间节点"
        title = f"{node}（{backward_label}）"
        forward_payload = asdict(f) if node == "工期" else (f.milestone_details.get(node, {}) or {})
        backward_payload = asdict(b) if node == "工期" else (b.milestone_details.get(node, {}) or {})
        if forward_date and backward_date:
            gap = (backward_date - forward_date).days
            if gap > 0:
                status, risk = f"后向{node}晚于前向", "高风险"
                description = (f"前向{node}日期为{forward_date.isoformat()}，{backward_label}{node}日期为"
                               f"{backward_date.isoformat()}，后向晚{gap}天，存在明确履约风险。")
            else:
                status, risk = f"后向{node}不晚于前向", "无风险"
                lead = abs(gap)
                description = (f"前向{node}日期为{forward_date.isoformat()}，{backward_label}{node}日期为"
                               f"{backward_date.isoformat()}，后向{'提前' + str(lead) + '天' if lead else '与前向同日'}。")
            result.append(Difference(category, status, risk, f"TM-{index:03d}", title, description,
                                     forward_payload, backward_payload, evidence))
            continue
        forward_has = _schedule_requirement(f, node)
        backward_has = _schedule_requirement(b, node)
        if node != "工期" and not (forward_date or backward_date or forward_has or backward_has):
            continue
        missing_side = "前向和后向" if not forward_date and not backward_date else ("前向" if not forward_date else "后向")
        description = (f"{missing_side}{node}缺少可靠的具体日期，暂不能判断{backward_label}是否晚于前向。"
                       f"前向：{forward_date.isoformat() if forward_date else '无可计算日期'}；"
                       f"后向：{backward_date.isoformat() if backward_date else '无可计算日期'}。")
        result.append(Difference(category, f"{node}日期待确认", "待确认", f"TM-{index:03d}", title,
                                 description, forward_payload, backward_payload, evidence, True))
    if f.start_condition_type != b.start_condition_type:
        result.append(Difference("工期", "前后向时间条件不一致", "中风险", "TM-005",
            f"起算条件（{backward_label}）",
            f"前向为“{f.start_condition_type}”，{backward_label}为“{b.start_condition_type}”。",
            asdict(f), asdict(b), evidence))
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
