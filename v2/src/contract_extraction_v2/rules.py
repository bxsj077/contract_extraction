from __future__ import annotations

import re
from datetime import date

from .date_utils import calculate_end_date, choose_contract_sign_date, choose_party_dates, extract_signing_dates, parse_dates
from .models import ContractOutput, Evidence, PageText
from .schema import DEEP_FIELDS, empty_result


MAINTENANCE = {"运行维护": 5, "运维服务": 5, "维护服务": 4, "维保服务": 4, "驻场运维": 4, "故障处理": 2, "巡检": 2}
INTEGRATION = {"系统集成": 5, "集成实施": 5, "项目实施": 4, "设备采购": 3, "安装调试": 3, "部署上线": 3, "竣工验收": 2, "到货验收": 2}

DURATION_RE = re.compile(r"(?:工期|建设周期|实施周期|交付周期|合同工期)[^。；\n]{0,35}?(?P<num>\d{1,4}|[一二三四五六七八九十百]+)\s*(?P<unit>个?工作日|日历日|个?月|年|天|日)")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

START_RULES = [
    ("收到开工令开始", re.compile(r"(?:收到|接到)[^。；\n]{0,12}(?:开工令|开工通知)[^。；\n]{0,12}(?:之日|日起|开始)")),
    ("签约归档开始", re.compile(r"(?:合同(?:签订|签署|生效|归档)|签字盖章)[^。；\n]{0,18}(?:之日|日起|开始)")),
    ("其他", re.compile(r"(?:进场|甲方通知|具备开工条件|项目启动)[^。；\n]{0,18}(?:之日|日起|开始)")),
]

MILESTONES = {
    "开工时间点": ("开工",), "设备到货时间点": ("到货", "设备交货"), "设备安装完成时间点": ("安装完成",),
    "软件部署完成时间点": ("部署完成",), "系统调试完成时间点": ("调试完成",), "上线时间点": ("上线",),
    "试运行开始时间点": ("试运行开始",), "试运行期限": ("试运行期", "试运行时间"), "初验时间点": ("初验", "初步验收"),
    "终验时间点": ("终验", "最终验收"), "竣工验收时间点": ("竣工验收",), "整体交付时间点": ("整体交付", "项目交付"),
    "质保期": ("质保期", "质量保证期"),
}


def _compact(text: str) -> str:
    return re.sub(r"[ \t\r]+", "", text)


def _snippet(text: str, start: int, end: int, radius: int = 90) -> str:
    return re.sub(r"\s+", " ", text[max(0, start - radius):min(len(text), end + radius)]).strip()


def _page_for(pages: list[PageText], matcher) -> tuple[PageText, re.Match[str]] | None:
    for page in pages:
        match = matcher(page.text)
        if match:
            return page, match
    return None


def _cn_int(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value in CN_NUM:
        return CN_NUM[value]
    if "十" in value:
        left, right = value.split("十", 1)
        return (CN_NUM.get(left, 1) * 10) + CN_NUM.get(right, 0)
    return None


def _add_evidence(evidence: list[Evidence], contract_id: str, field: str, value: object,
                  page: PageText, quote: str, confidence: str = "高", review: bool = False,
                  conflict: str = "") -> None:
    evidence.append(Evidence(contract_id, field, value, page.file_name, page.page, quote,
                             method=page.method, ocr_confidence=page.confidence or "",
                             field_confidence=confidence, conflict=conflict,
                             needs_review="是" if review else "否"))


def classify(pages: list[PageText], margin: int = 2) -> tuple[str, str, int, int]:
    text = _compact("\n".join(page.text for page in pages))
    maintenance = sum(text.count(word) * weight for word, weight in MAINTENANCE.items())
    integration = sum(text.count(word) * weight for word, weight in INTEGRATION.items())
    if maintenance >= integration + margin:
        kind = "运维类"
    elif integration >= maintenance + margin:
        kind = "集成实施类"
    else:
        kind = ""
    return kind, f"运维关键词得分={maintenance}；集成实施关键词得分={integration}", maintenance, integration


def _extract_header(pages: list[PageText], field: str, labels: tuple[str, ...]) -> tuple[str, PageText | None, str]:
    pattern = re.compile(rf"(?:{'|'.join(map(re.escape, labels))})\s*[：:]\s*([^\n。；]{{2,100}})")
    found = _page_for(pages[:8], pattern.search)
    if not found:
        return "", None, ""
    page, match = found
    value = match.group(1).strip(" ：:，,")
    return value, page, _snippet(page.text, match.start(), match.end(), 35)


def _find_milestone(pages: list[PageText], words: tuple[str, ...]) -> tuple[str, PageText | None, str]:
    pattern = re.compile(rf"(?:{'|'.join(map(re.escape, words))})[^。；\n]{{0,80}}")
    found = _page_for(pages, pattern.search)
    if not found:
        return "", None, ""
    page, match = found
    quote = _snippet(page.text, match.start(), match.end(), 40)
    dates = parse_dates(quote)
    if dates:
        return dates[0][0].isoformat(), page, quote
    relative = re.search(r"(?:\d{1,4}|[一二三四五六七八九十]+)\s*(?:个?工作日|日历日|个?月|年|天|日)(?:内|后|前|完成)?", quote)
    return (relative.group(0) if relative else quote[:180]), page, quote


def analyze_contract(contract_id: str, folder: str, pages: list[PageText], source_files: list[dict[str, object]],
                     fingerprint: str, config: dict[str, object], errors: list[str]) -> ContractOutput:
    result = empty_result()
    evidence: list[Evidence] = []
    result.update({"合同号": contract_id, "合同文件夹": folder, "关联PDF数量": len(source_files),
                   "主合同文件": source_files[0]["文件名"] if source_files else ""})

    for field, labels in (("合同名称", ("合同名称", "项目名称")), ("甲方", ("甲方", "买方")), ("乙方", ("乙方", "卖方"))):
        value, page, quote = _extract_header(pages, field, labels)
        result[field] = value
        if value and page:
            _add_evidence(evidence, contract_id, field, value, page, quote)

    kind, reason, _, _ = classify(pages, int(config.get("classification_margin", 2)))
    result["合同性质"] = kind
    result["合同性质判定依据"] = reason
    if pages:
        _add_evidence(evidence, contract_id, "合同性质", kind or "无法判定", pages[0], reason,
                      "高" if kind else "低", not bool(kind))

    candidates = extract_signing_dates(pages, float(config.get("ocr_review_threshold", 0.90)))
    party_dates = choose_party_dates(candidates)
    result["甲方签约日期"] = party_dates["甲方"].isoformat() if party_dates["甲方"] else ""
    result["乙方签约日期"] = party_dates["乙方"].isoformat() if party_dates["乙方"] else ""
    result["其他方签约日期"] = party_dates["其他方"].isoformat() if party_dates["其他方"] else ""
    contract_date = choose_contract_sign_date(candidates, str(config.get("signing_date_policy", "latest_recognized")))
    result["合同签约日期"] = contract_date.isoformat() if contract_date else ""
    partial = bool(candidates) and len({c.party for c in candidates}) < 2
    obscured = any(c.needs_review for c in candidates)
    result["签约日期需人工确认"] = "是" if obscured else "否"
    result["签约日期识别说明"] = ("仅识别到部分签约方日期，按配置允许作为合同签约日期；不等同于三方全部盖章生效日。" if partial else "") + (" 日期位于签章区域并经局部增强OCR，需人工确认。" if obscured else "")
    for candidate in candidates:
        target = candidate.party + "签约日期" if candidate.party in {"甲方", "乙方"} else "其他方签约日期"
        page = next((p for p in pages if p.file_name == candidate.file_name and p.page == candidate.page), pages[0])
        _add_evidence(evidence, contract_id, target, candidate.value.isoformat(), page, candidate.context,
                      "中" if candidate.needs_review else "高", candidate.needs_review,
                      "仅部分签约方有日期" if partial else "")

    methods = sorted({p.method for p in pages})
    confidences = [p.confidence for p in pages if p.confidence is not None]
    result["OCR方式"] = "；".join(methods)
    result["OCR质量"] = round(sum(confidences) / len(confidences), 4) if confidences else "原生文本"

    if kind == "运维类":
        for field in DEEP_FIELDS:
            result[field] = ""
        result["处理说明"] = "运维类按规则停止深度提取。"
    elif kind == "集成实施类":
        duration = _page_for(pages, DURATION_RE.search)
        if duration:
            page, match = duration
            amount = _cn_int(match.group("num"))
            unit = match.group("unit")
            result.update({"工期原文": match.group(0), "工期标准化": f"{amount}{unit}" if amount is not None else match.group(0),
                           "工期数值": amount or "", "工期单位": unit})
            _add_evidence(evidence, contract_id, "工期原文", match.group(0), page, _snippet(page.text, match.start(), match.end()))

        start_found = None
        for label, pattern in START_RULES:
            start_found = _page_for(pages, pattern.search)
            if start_found:
                page, match = start_found
                quote = _snippet(page.text, match.start(), match.end())
                result["工期起算方式"] = label
                result["工期起算条件原文"] = quote
                dates = parse_dates(quote)
                if dates:
                    result["工期起算具体日期"] = dates[0][0].isoformat()
                elif label == "签约归档开始" and contract_date and bool(config.get("allow_partial_signing_date", True)):
                    result["工期起算具体日期"] = contract_date.isoformat()
                    result["工期起算其他说明"] = "根据签约起算条款及已识别签约日期推算；部分签约方缺日期时不代表全部盖章生效日。"
                _add_evidence(evidence, contract_id, "工期起算方式", label, page, quote)
                break
        if not start_found:
            result["工期起算方式"] = "没有明确"

        if result["工期起算具体日期"] and result["工期数值"]:
            end, note = calculate_end_date(date.fromisoformat(str(result["工期起算具体日期"])), int(result["工期数值"]), str(result["工期单位"]))
            result["预计结束日期"] = end.isoformat() if end else ""
            if note:
                result["工期起算其他说明"] = (str(result["工期起算其他说明"]) + " " + note).strip()

        for field, words in MILESTONES.items():
            value, page, quote = _find_milestone(pages, words)
            result[field] = value
            if value and page:
                _add_evidence(evidence, contract_id, field, value, page, quote, "中")

        for field, words in (("服务内容", ("服务内容", "建设内容", "工作内容")), ("乙方义务", ("乙方义务", "乙方责任", "卖方义务")),
                             ("关键条款", ("违约责任", "付款条件", "知识产权", "保密", "合同生效"))):
            value, page, quote = _find_milestone(pages, words)
            result[field] = quote[:int(config.get("max_summary_chars", 800))] if quote else ""
            if result[field] and page:
                _add_evidence(evidence, contract_id, field, result[field], page, quote, "中")
    else:
        result["处理说明"] = "合同性质无法可靠判定，未自动执行深度提取。"

    reasons = list(errors)
    if not kind:
        reasons.append("合同性质无法可靠判定")
    if obscured:
        reasons.append("签约日期被印章遮挡或OCR置信度不足，需局部增强后人工确认")
    result["待人工复核"] = "是" if reasons else "否"
    result["复核原因"] = "；".join(dict.fromkeys(reasons))
    result["处理时间"] = date.today().isoformat()
    return ContractOutput(result, evidence, pages, source_files, fingerprint, errors)
