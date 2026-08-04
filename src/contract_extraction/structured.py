from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from .models import ContractOutput, PageText
from .system_models import ContractStructured, EquipmentItem, EvidenceRef, ScopeItem, TimePlan


UNITS = "台|套|个|块|只|批|系统|项|组|路|点|端|授权|年|月"
EQUIPMENT_RE = re.compile(rf"(?P<name>[\u4e00-\u9fffA-Za-z0-9\-（）()/.]{{2,45}}?)\s+(?:(?P<model>[A-Za-z][A-Za-z0-9._/\-]{{2,30}})\s+)?(?P<unit>{UNITS})\s*(?P<qty>\d+(?:\.\d+)?)")
EQUIPMENT_WORDS = re.compile(r"交换机|服务器|防火墙|路由器|存储|软件|平台|系统|模块|终端|摄像机|授权|数据库|线缆|光纤|机柜|配电|网关")
SCOPE_TERMS = ["供货", "运输", "卸货", "保管", "上架", "安装", "通电", "设备调试", "系统联调", "旧设备拆除",
               "桥架施工", "管线施工", "线缆敷设", "光纤熔接", "配电施工", "防雷接地", "机房改造", "土建恢复", "标识标签",
               "软件部署", "环境搭建", "功能配置", "接口开发", "系统集成", "数据迁移", "数据治理", "系统测试", "安全加固",
               "方案设计", "深化设计", "项目管理", "培训", "试运行", "初验配合", "终验配合", "竣工资料", "结算资料", "售后服务", "质保服务"]
RESPONSIBILITY = [("负责完成", "负责完成"), ("组织实施", "组织实施"), ("承担", "承担"), ("负责", "负责完成"),
                  ("提供", "提供"), ("配合", "配合"), ("协助", "协助")]
MILESTONE_MAP = {"开工": "开工时间点", "设备到货": "设备到货时间点", "安装完成": "设备安装完成时间点",
                 "软件部署": "软件部署完成时间点", "系统调试": "系统调试完成时间点", "上线": "上线时间点",
                 "试运行": "试运行开始时间点", "初验": "初验时间点", "终验": "终验时间点",
                 "竣工验收": "竣工验收时间点", "整体交付": "整体交付时间点", "质保": "质保期"}


def _sentence(text: str, start: int) -> str:
    left = max(text.rfind("。", 0, start), text.rfind("\n", 0, start)) + 1
    endings = [x for x in (text.find("。", start), text.find("\n", start)) if x >= 0]
    right = min(endings) + 1 if endings else min(len(text), start + 240)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def _page_evidence(pages: list[PageText], needle: str) -> tuple[str, int, str, float]:
    for page in pages:
        pos = page.text.find(needle)
        if pos >= 0:
            return page.file_name, page.page, _sentence(page.text, pos), float(page.confidence or 1.0)
    return "", "", "", 0.0


def _extract_equipment(project: str, direction: str, pages: list[PageText], evidence: list[EvidenceRef]) -> list[EquipmentItem]:
    items: list[EquipmentItem] = []
    seen: set[tuple[str, str, float | None]] = set()
    for page in pages:
        for match in EQUIPMENT_RE.finditer(page.text):
            name = match.group("name").strip(" ：:，,;；")
            if not EQUIPMENT_WORDS.search(name):
                continue
            qty = float(match.group("qty"))
            key = (name, match.group("model") or "", qty)
            if key in seen:
                continue
            seen.add(key)
            ev_id = f"EV-{project}-{direction}-ITEM-{len(items)+1:04d}"
            quote = _sentence(page.text, match.start())
            evidence.append(EvidenceRef(ev_id, project, direction, "设备材料清单", name, page.file_name, page.page,
                                        quote, page.method, page.confidence or "", bool(page.confidence and page.confidence < .9)))
            items.append(EquipmentItem("其他", name, name, "", match.group("model") or "", match.group("unit"), qty,
                                       {}, direction, ev_id, float(page.confidence or .9)))
    return items


def _extract_scopes(project: str, direction: str, pages: list[PageText], evidence: list[EvidenceRef]) -> list[ScopeItem]:
    scopes: list[ScopeItem] = []
    full_text = "\n".join(p.text for p in pages)
    for term in SCOPE_TERMS:
        pos = full_text.find(term)
        if pos < 0:
            continue
        quote = _sentence(full_text, pos)
        responsibility = next((standard for word, standard in RESPONSIBILITY if word in quote), "不明确")
        scope_limit = next((x for x in ("全部", "所有", "部分", "指定", "不少于", "至少") if x in quote), "")
        obj = quote.replace(term, "", 1)[:100]
        file_name, page, _, confidence = _page_evidence(pages, term)
        ev_id = f"EV-{project}-{direction}-SCOPE-{len(scopes)+1:04d}"
        evidence.append(EvidenceRef(ev_id, project, direction, "实施内容", term, file_name, page, quote, "规则抽取", confidence))
        scopes.append(ScopeItem(term, responsibility, obj, scope_limit, "", direction, quote, ev_id, confidence or .85))
    return scopes


def analysis_to_structured(project: str, direction: str, output: ContractOutput) -> ContractStructured:
    result = output.result
    evidence: list[EvidenceRef] = []
    for index, item in enumerate(output.evidence, 1):
        evidence.append(EvidenceRef(f"EV-{project}-{direction}-FIELD-{index:04d}", project, direction, item.field_name,
            item.value, item.source_file, item.page, item.quote, item.method, item.ocr_confidence, item.needs_review == "是"))
    equipment = _extract_equipment(project, direction, output.pages, evidence)
    scopes = _extract_scopes(project, direction, output.pages, evidence)
    milestones = {name: str(result.get(field, "")) for name, field in MILESTONE_MAP.items() if result.get(field)}
    time_evidence = [e.evidence_id for e in evidence if "工期" in e.field_name or "时间" in e.field_name]
    plan = TimePlan(int(result["工期数值"]) if result.get("工期数值") else None, str(result.get("工期单位", "")),
                    str(result.get("工期起算方式", "没有明确")), str(result.get("工期起算条件原文", "")),
                    str(result.get("工期起算具体日期") or "") or None, str(result.get("预计结束日期") or "") or None,
                    "项目完工", None, milestones, time_evidence, .9 if result.get("工期数值") else 0.0)
    kind = str(result.get("合同性质") or "无法确定")
    return ContractStructured(project, direction, str(result.get("合同号", "")), str(result.get("合同名称", "")),
        str(result.get("甲方", "")), str(result.get("乙方", "")), None,
        str(result.get("合同签约日期") or "") or None, None, kind, equipment, plan, scopes,
        {"服务内容": str(result.get("服务内容", "")), "乙方义务": str(result.get("乙方义务", "")),
         "关键条款": str(result.get("关键条款", ""))}, evidence,
        {"file_hash": output.fingerprint, "source_files": output.source_files, "parse_version": "2026.08-v1"},
        [str(result.get("复核原因"))] if result.get("待人工复核") == "是" else [])


def structured_to_dict(value: ContractStructured) -> dict[str, Any]:
    return asdict(value)


def structured_from_dict(data: dict[str, Any]) -> ContractStructured:
    return ContractStructured(project_code=data["project_code"], direction=data["direction"],
        contract_number=data.get("contract_number", ""), contract_name=data.get("contract_name", ""),
        party_a=data.get("party_a", ""), party_b=data.get("party_b", ""), amount_yuan=data.get("amount_yuan"),
        sign_date=data.get("sign_date"), effective_date=data.get("effective_date"), contract_type=data.get("contract_type", "无法确定"),
        equipment=[EquipmentItem(**x) for x in data.get("equipment", [])], time_plan=TimePlan(**data.get("time_plan", {})),
        scopes=[ScopeItem(**x) for x in data.get("scopes", [])], key_clauses=data.get("key_clauses", {}),
        evidence=[EvidenceRef(**x) for x in data.get("evidence", [])], parse_metadata=data.get("parse_metadata", {}),
        review_issues=data.get("review_issues", []))
