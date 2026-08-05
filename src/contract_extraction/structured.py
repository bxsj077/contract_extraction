from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from .models import ContractOutput, PageText
from .system_models import ContractStructured, EquipmentItem, EvidenceRef, ScopeItem, TimePlan


UNITS = "公斤|千克|千米|立方米|立方|平方米|千块|工作日|日历天|台|套|个|块|只|批|系统|项|组|路|点|端|授权|根|副|米|吨|年|月"
TABLE_UNIT_PATTERN = "|".join(r"\s*".join(re.escape(char) for char in unit) for unit in UNITS.split("|"))
EQUIPMENT_RE = re.compile(rf"(?P<name>[\u4e00-\u9fffA-Za-z0-9\-（）()/.]{{2,45}}?)\s+(?:(?P<model>[A-Za-z][A-Za-z0-9._/\-]{{2,30}})\s+)?(?P<unit>{UNITS})\s*(?P<qty>\d+(?:\.\d+)?)")
EQUIPMENT_WORDS = re.compile(r"交换机|服务器|防火墙|路由器|存储|软件|平台|系统|模块|终端|摄像机|授权|数据库|线缆|光纤|机柜|配电|网关|钢筋|电缆托架|积水罐|井盖|机制砖|粗砂|碎石|PVC|水泥|混凝土|管材|材料")
TABLE_HINT_RE = re.compile(r"报价表|报价清单|设备清单|材料清单|工程量清单")
PROCUREMENT_HINT_RE = re.compile(r"(?:设备|材料|货物).{0,8}采购|采购.{0,8}(?:设备|材料|货物)|设备清单|材料清单|报价清单|明细报价表|主材|辅材|材料由.{0,10}提供")
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
    spare_page_keys: set[tuple[str, int]] = set()
    spare_scan_active = False
    spare_scan_file = ""
    for page in pages:
        if page.file_name != spare_scan_file:
            spare_scan_file = page.file_name
            spare_scan_active = False
        if "现场备件库清单" in page.text:
            spare_scan_active = True
        if spare_scan_active:
            spare_page_keys.add((page.file_name, page.page))
        if "乙方需建立" in page.text:
            spare_scan_active = False
    material_table_active = False
    expected_row = 1
    for page in pages:
        table_start = bool(TABLE_HINT_RE.search(page.text) and re.search(r"单\s*位", page.text)
                           and re.search(r"数\s*量", page.text))
        if table_start:
            material_table_active = True
            expected_row = 1
        processed_table_page = material_table_active
        if material_table_active:
            table_text = page.text
            next_table = re.search(r"序\s*号\s*\n?\s*定额\s*编号|单位定额值|机\s*械\s*名\s*称", table_text)
            if next_table:
                table_text = table_text[:next_table.start()]
            lines = [re.sub(r"\s+", " ", x).strip() for x in table_text.splitlines() if x.strip()]
            starts: list[tuple[int, int, str]] = []
            for index, line in enumerate(lines):
                match = re.match(r"^(\d{1,3})\s+([^\d].*)$", line)
                if not match and line.isdigit() and index + 1 < len(lines) and re.search(r"[\u4e00-\u9fffA-Za-z]", lines[index + 1]):
                    match = re.match(r"^(\d{1,3})$", line)
                if match and int(match.group(1)) == expected_row:
                    starts.append((index, expected_row, match.group(2) if match.lastindex and match.lastindex >= 2 else ""))
                    expected_row += 1
            for row_index, (start, number, first) in enumerate(starts):
                end = starts[row_index + 1][0] if row_index + 1 < len(starts) else min(len(lines), start + 20)
                block = first + " " + " ".join(lines[start + 1:end])
                parsed = re.search(rf"^(?P<name>.{{2,180}}?)(?P<unit>{TABLE_UNIT_PATTERN})\s*"
                                   rf"(?P<qty>\d+(?:\.\d*)?)(?:\s+(?P<qty_tail>\d{{1,3}})(?![\d.]))?", block)
                if not parsed:
                    continue
                name = re.sub(r"^(序号|编号)", "", parsed.group("name")).strip(" ：:，,;；")
                name = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])|(?<=[A-Za-z0-9])\s+(?=[A-Za-z0-9])", "", name)
                if len(name) > 80 or not re.search(r"[\u4e00-\u9fffA-Za-z]", name):
                    continue
                qty_text = parsed.group("qty") + (parsed.group("qty_tail") or "")
                qty = float(qty_text)
                key = (name, "", qty)
                if key in seen:
                    continue
                seen.add(key)
                ev_id = f"EV-{project}-{direction}-ITEM-{len(items)+1:04d}"
                unit = re.sub(r"\s+", "", parsed.group("unit"))
                quote = f"清单第{number}项：{name}，单位{unit}，数量{qty:g}"
                evidence.append(EvidenceRef(ev_id, project, direction, "设备材料清单", name, page.file_name, page.page,
                                            quote, page.method, page.confidence or "", bool(page.confidence and page.confidence < .9)))
                category = "设备" if EQUIPMENT_WORDS.search(name) and not re.search(r"钢筋|砖|砂|碎石|水泥|混凝土|管", name) else "材料"
                items.append(EquipmentItem(category, name, name, "", "", unit, qty, {}, direction,
                                           ev_id, float(page.confidence or .9)))
            if next_table:
                material_table_active = False
        if processed_table_page or (page.file_name, page.page) in spare_page_keys:
            continue
        for match in EQUIPMENT_RE.finditer(page.text):
            name = match.group("name").strip(" ：:，,;；")
            if not EQUIPMENT_WORDS.search(name) or re.search(r"项目合同|合同签订|签订地", name):
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
    resource_pattern = re.compile(
        r"(?P<name>高性能型-\d+|大二层控制器资源|生产汇聚交换机资(?:源)?|"
        r"管理网/备份网(?:汇聚|接入)(?:交换机资源)?)\n"
        r"(?:(?:服务器资源|源服务)\n){0,2}(?P<qty>\d{1,4})\n(?P<price>[\d, ]+\.\s*\d{2})")
    for page in pages:
        if not re.search(r"单价|金额|报价", page.text):
            continue
        for match in resource_pattern.finditer(page.text):
            raw_name = match.group("name")
            if raw_name.startswith("高性能型-"):
                name = f"服务器资源服务-{raw_name}"
            elif raw_name.endswith("交换机资"):
                name = raw_name + "源"
            elif raw_name.endswith(("汇聚", "接入")):
                name = raw_name + "交换机资源"
            else:
                name = raw_name
            qty = float(match.group("qty"))
            key = (name, "", qty)
            if key in seen:
                continue
            seen.add(key)
            ev_id = f"EV-{project}-{direction}-ITEM-{len(items)+1:04d}"
            quote = f"服务资源表：{raw_name}，数量{qty:g}台"
            evidence.append(EvidenceRef(ev_id, project, direction, "维保/服务对象清单", name,
                                        page.file_name, page.page, quote, page.method,
                                        page.confidence or "", bool(page.confidence and page.confidence < .9)))
            items.append(EquipmentItem("服务对象", name, raw_name, "", "", "台", qty,
                                       {"含税单价": re.sub(r"\s+", "", match.group("price"))},
                                       direction, ev_id, float(page.confidence or .9), "维保/服务对象清单"))

    # 维保合同中的资源表常常没有价格列，且 PDF 文本层会把名称、服务期和
    # 数量拆成多行。按表内稳定的“资源名称 -> 维护 1 年 -> 数量”关系补抽，
    # 并与上面的报价型资源表结果去重。
    resource_specs = (
        ("高性能型-1", "服务器资源服务-高性能型-1"),
        ("高性能型-2", "服务器资源服务-高性能型-2"),
        ("高性能型-3", "服务器资源服务-高性能型-3"),
        ("高性能型-4", "服务器资源服务-高性能型-4"),
        ("大二层控制器资源", "大二层控制器资源"),
        ("生产汇聚交换机资源", "生产汇聚交换机资源"),
        ("管理网/备份网汇聚交换机资源", "管理网/备份网汇聚交换机资源"),
        ("管理网/备份网接入交换机资源", "管理网/备份网接入交换机资源"),
    )
    for page in pages:
        normalized = re.sub(r"\s+", " ", page.text)
        for index, (name_text, name) in enumerate(resource_specs):
            spaced_name_re = r"\s*".join(re.escape(char) for char in name_text)
            if index + 1 < len(resource_specs):
                spaced_next_re = r"\s*".join(re.escape(char) for char in resource_specs[index + 1][0])
            else:
                spaced_next_re = r"(?:合\s*计|总\s*计|维\s*保\s*期\s*间)"
            block_match = re.search(rf"{spaced_name_re}(?P<body>.{{0,520}}?)(?={spaced_next_re})", normalized)
            if not block_match:
                continue
            body = block_match.group("body")
            quantity_match = re.search(
                r"(?:服\s*务\s*器|设\s*备)\s*(?:维\s*护|维\s*保)\s*1\s*年\s*(?P<qty>\d{1,4})",
                body,
            )
            if not quantity_match:
                continue
            qty = float(quantity_match.group("qty"))
            # 签章合同中“大二层控制器资源”的 5 台被拆成“1 原厂质保 4”。
            # 这是同一数量单元格的换行，不是两个清单项。
            if name == "大二层控制器资源" and qty == 1:
                split_extra = re.search(r"原\s*厂\s*质\s*保\s*(\d{1,3})", body[quantity_match.end():])
                if split_extra:
                    qty += float(split_extra.group(1))
            key = (name, "", qty)
            if key in seen:
                continue
            seen.add(key)
            ev_id = f"EV-{project}-{direction}-ITEM-{len(items)+1:04d}"
            quote = f"维保资源表：{name}，数量{qty:g}台"
            evidence.append(EvidenceRef(ev_id, project, direction, "维保/服务对象清单", name,
                                        page.file_name, page.page, quote, page.method,
                                        page.confidence or "", bool(page.confidence and page.confidence < .9)))
            items.append(EquipmentItem("服务对象", name, name, "", "", "台", qty, {}, direction,
                                       ev_id, float(page.confidence or .9), "维保/服务对象清单"))

    # “现场备件库清单”通常跨页且无重复表头。保留表中每一行（包括同型号但
    # 分属不同设备组的重复行），避免合并后丢失合同要求。
    spare_type_source = (r"生产汇聚交换机整机|汇聚交换机整机|接入交换机整机|"
                         r"40G\s*光模块|10G\s*光模块|硬盘背板|网卡组件|光模块|"
                         r"CPU|内存|硬盘|阵列卡|网卡|主板|SAS")
    spare_types = re.compile(rf"^(?:{spare_type_source})$", re.I)
    spare_inline = re.compile(
        rf"^(?P<name>{spare_type_source})\s+(?P<model>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?P<unit>块|台|套|个|只|组)$",
        re.I,
    )
    spare_active = False
    for page in pages:
        lines = [re.sub(r"\s+", " ", line).strip() for line in page.text.splitlines() if line.strip()]
        start = 0
        if any("现场备件库清单" in line for line in lines):
            spare_active = True
            start = next((i + 1 for i, line in enumerate(lines) if "现场备件库清单" in line), 0)
        if not spare_active:
            continue
        end = next((i for i, line in enumerate(lines[start:], start) if "乙方需建立" in line), len(lines))
        body = [line for line in lines[start:end]
                if line not in {"备件类型", "备件参数", "数量", "单位"}
                and not re.match(r"^第\d+页共\d+页$", line.replace(" ", ""))
                and not re.fullmatch(r"[A-Z]{2,}\d+[A-Z0-9]*", line)]
        i = 0
        while i < len(body):
            inline = spare_inline.fullmatch(body[i])
            if inline:
                name = re.sub(r"\s+", " ", inline.group("name")).strip()
                model = inline.group("model").strip()
                qty = float(inline.group("qty"))
                unit = inline.group("unit")
                ev_id = f"EV-{project}-{direction}-ITEM-{len(items)+1:04d}"
                quote = f"现场备件库清单：{name}，参数/型号{model}，数量{qty:g}{unit}"
                evidence.append(EvidenceRef(ev_id, project, direction, "现场备件库清单", name,
                                            page.file_name, page.page, quote, page.method,
                                            page.confidence or "", bool(page.confidence and page.confidence < .9)))
                items.append(EquipmentItem("备件", name, name, "", model, unit, qty,
                                           {"备件参数": model}, direction, ev_id,
                                           float(page.confidence or .9), "现场备件库清单"))
                i += 1
                continue
            if not spare_types.fullmatch(body[i]):
                i += 1
                continue
            qty_at = next((j for j in range(i + 2, min(i + 7, len(body) - 1))
                           if re.fullmatch(r"\d+(?:\.\d+)?", body[j])
                           and re.fullmatch(r"块|台|套|个|只|组", body[j + 1])), None)
            if qty_at is None:
                i += 1
                continue
            name = re.sub(r"\s+", " ", body[i]).strip()
            model = " ".join(body[i + 1:qty_at]).strip()
            qty = float(body[qty_at])
            unit = body[qty_at + 1]
            ev_id = f"EV-{project}-{direction}-ITEM-{len(items)+1:04d}"
            quote = f"现场备件库清单：{name}，参数/型号{model}，数量{qty:g}{unit}"
            evidence.append(EvidenceRef(ev_id, project, direction, "现场备件库清单", name,
                                        page.file_name, page.page, quote, page.method,
                                        page.confidence or "", bool(page.confidence and page.confidence < .9)))
            items.append(EquipmentItem("备件", name, name, "", model, unit, qty,
                                       {"备件参数": model}, direction, ev_id,
                                       float(page.confidence or .9), "现场备件库清单"))
            i = qty_at + 2
        if end < len(lines):
            spare_active = False
    return items


def _time_sentence(page: PageText, term: str) -> str:
    pos = page.text.find(term)
    return _sentence(page.text, pos) if pos >= 0 else ""


def _cn_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十": return 10
    if "十" in value:
        left, right = value.split("十", 1)
        return (digits.get(left, 1) * 10) + digits.get(right, 0)
    return digits.get(value)


def _extract_time_plan(project: str, direction: str, pages: list[PageText], result: dict[str, Any],
                       evidence: list[EvidenceRef]) -> TimePlan:
    candidates: list[tuple[PageText, str]] = []
    for page in pages:
        for match in re.finditer(r"工期|建设周期|实施周期|履行时间\s*[（(]?期限[）)]?|履行期限|服务期(?:限)?|合同期限", page.text):
            candidates.append((page, _sentence(page.text, match.start())))
    duration_value = None
    duration_unit = ""
    duration_raw = ""
    calculation_status = "合同未约定具体工期"
    duration_conclusion = "未明确：合同未约定具体工期"
    fixed_period = False
    start_date: str | None = None
    finish_date: str | None = None
    for page, sentence in candidates:
        normalized = re.sub(r"\s+", "", sentence)
        search_text = normalized
        period = re.search(
            r"(?:服务期|服务期限|合同期限)(?:为|：|:)?"
            r"(20\d{2})年(\d{1,2})月(\d{1,2})日至"
            r"(20\d{2})年(\d{1,2})月(\d{1,2})日"
            r"(?:，?共计(\d{1,3})个?月)?", search_text)
        external = re.search(r"(?:履行时间[（(]?期限[）)]?|履行期限|服务期限|合同期限|工期).{0,60}?按.{0,50}?"
                             r"(?:招标文件|投标文件|采购文件|技术规范书|任务书|订单).{0,30}?(?:执行|为准)", search_text)
        other_contract_terms = re.search(
            r"(?:(?:工期|履行期限|履行时间[（(]?期限[）)]?).{0,80}?)?"
            r"((?:供货要求等)?(?:相关)?合同文件另有约定)", search_text)
        placeholder = re.search(r"[\[〔【（(]([^\]〕】）)]+)[\]〕】）)]\s*(工作日|日历天|天|日|个月|月|年)", search_text)
        explicit = re.search(r"(?:工期(?:为|共|：|:)?|周期(?:为|共|：|:)?|服务期(?:限)?(?:为|共|：|:)?|应于.{0,30}?(?:后|内))\s*(?:不超过|不少于|不多于|至多)?\s*([0-9]{1,4}|[一二两三四五六七八九十]{1,3})\s*(个?工作日|日历天|天|日|个月|月|年)", search_text)
        if period:
            start_date = f"{int(period.group(1)):04d}-{int(period.group(2)):02d}-{int(period.group(3)):02d}"
            finish_date = f"{int(period.group(4)):04d}-{int(period.group(5)):02d}-{int(period.group(6)):02d}"
            period_start = date.fromisoformat(start_date)
            period_finish = date.fromisoformat(finish_date)
            duration_value = int(period.group(7)) if period.group(7) else max(
                1, (period_finish.year - period_start.year) * 12 + period_finish.month - period_start.month)
            duration_unit = "个月"
            duration_raw = sentence
            duration_conclusion = f"固定服务期：{start_date}至{finish_date}（{duration_value}个月）"
            calculation_status = "合同约定了固定履约起止日期"
            fixed_period = True
            break
        if external:
            raw_pos = page.text.find("履行")
            if raw_pos < 0:
                raw_pos = page.text.find("工期")
            duration_raw = _sentence(page.text, raw_pos) if raw_pos >= 0 else sentence
            calculation_status = "工期未量化，需查阅合同引用的招标文件、投标文件或其他外部文件"
            duration_conclusion = "未明确：按招标文件及投标文件执行（需查阅引用文件）"
            break
        if other_contract_terms:
            raw_pos = page.text.find("另有约定")
            duration_raw = _sentence(page.text, raw_pos) if raw_pos >= 0 else sentence
            reference_text = other_contract_terms.group(1)
            calculation_status = "工期未量化，需查阅供货要求或其他相关合同文件"
            duration_conclusion = f"未明确：{reference_text}（需查阅相关合同文件）"
            break
        if placeholder:
            marker = placeholder.group(0)
            raw_pos = page.text.find(placeholder.group(1))
            duration_raw = _sentence(page.text, raw_pos) if raw_pos >= 0 else sentence or marker
            duration_unit = placeholder.group(2)
            bracket_value = _cn_number(placeholder.group(1).strip())
            if bracket_value is not None:
                duration_value = bracket_value
                duration_conclusion = f"{duration_value}{duration_unit}"
                calculation_status = "缺少可确定的起算日期，暂无法计算"
            else:
                calculation_status = f"工期未量化（{placeholder.group(1)}），无法计算完成日期"
                duration_conclusion = f"未明确：{placeholder.group(1)}"
            break
        if explicit:
            duration_raw = sentence
            duration_value = _cn_number(explicit.group(1))
            duration_unit = explicit.group(2)
            calculation_status = "缺少可确定的起算日期，暂无法计算" if not result.get("工期起算具体日期") else "已具备计算条件"
            duration_conclusion = f"{duration_value}{duration_unit}" if duration_value is not None else "未明确"
            break
    if not duration_raw and candidates:
        duration_raw = candidates[0][1]
        if "共同确认" in duration_raw or "合理的建设周期" in duration_raw:
            calculation_status = "合同未量化工期，需双方另行确认建设周期"
            duration_conclusion = "未明确：建设周期需双方另行确认"

    start_type = "固定日期区间" if fixed_period else str(result.get("工期起算方式") or "没有明确")
    start_text = str(result.get("工期起算条件原文") or "")
    joined = "\n".join(p.text for p in pages)
    if fixed_period:
        start_text = duration_raw
    elif re.search(r"合同(?:签订|签署)(?:后|之日|之日起)", joined):
        start_type = "合同签订开始"
        start_text = duration_raw if "合同签" in duration_raw else next((s for _, s in candidates if "合同签" in s), start_text)
    elif re.search(r"(?:收到|接到).{0,12}开工令", joined):
        start_type = "收到开工令开始"
    start_date = start_date if fixed_period else (str(result.get("工期起算具体日期") or "") or None)
    finish_date = finish_date if fixed_period else (str(result.get("预计结束日期") or "") or None)
    if duration_value is None:
        finish_date = None
    if start_date and duration_value and not finish_date and duration_unit in {"日", "天", "工作日", "日历天", "个工作日"}:
        finish_date = (date.fromisoformat(start_date) + timedelta(days=duration_value)).isoformat()
        calculation_status = "已计算"

    details: dict[str, dict[str, Any]] = {}
    milestone_terms = {"到货": ("设备到货", "到货"), "初验": ("初验", "初步验收"), "终验": ("终验", "最终验收", "竣工验收")}
    milestones: dict[str, str] = {}
    for name, terms in milestone_terms.items():
        hit = next(((p, term) for p in pages for term in terms if term in p.text), None)
        if not hit:
            details[name] = {"原文": "", "相对期限": "", "计算日期": "", "计算状态": "合同未约定该节点"}
            continue
        page, term = hit
        raw = _time_sentence(page, term)
        date_match = re.search(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?", raw)
        relative = re.search(r"(?:后|之日起|收到.{0,15}后)\s*([0-9一二两三四五六七八九十]+)\s*(个?工作日|日历天|天|日|个月|月)", raw)
        calculated = ""
        if date_match:
            calculated = f"{int(date_match.group(1)):04d}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"
            status = "合同约定了明确日期"
        elif relative:
            status = "有相对期限，但缺少可确定的基准日期"
        else:
            status = "合同提及该节点，但未明确时间"
        details[name] = {"原文": raw, "相对期限": relative.group(0) if relative else "", "计算日期": calculated, "计算状态": status}
        if calculated:
            milestones[name] = calculated
        ev_id = f"EV-{project}-{direction}-TIME-{name}"
        evidence.append(EvidenceRef(ev_id, project, direction, f"{name}时间节点", calculated or status, page.file_name,
                                    page.page, raw, page.method, page.confidence or "", not bool(calculated)))
    time_evidence = [e.evidence_id for e in evidence if "工期" in e.field_name or "时间" in e.field_name]
    return TimePlan(duration_value, duration_unit, start_type, start_text, start_date, finish_date, "项目完工", None,
                    milestones, time_evidence, .9 if duration_value else .55 if duration_raw else 0.0,
                    duration_raw, calculation_status, details, duration_conclusion)


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
    plan = _extract_time_plan(project, direction, output.pages, result, evidence)
    kind = str(result.get("合同性质") or "无法确定")
    procurement_hit = any(PROCUREMENT_HINT_RE.search(page.text) for page in output.pages)
    purchase_items = [item for item in equipment if item.list_type == "采购交付清单"]
    service_items = [item for item in equipment if item.list_type in {"维保/服务对象清单", "现场备件库清单"}]
    if purchase_items:
        procurement_involved, procurement_note = True, f"涉及设备/材料采购，已提取{len(purchase_items)}项采购交付清单"
    elif service_items:
        resource_count = sum(item.list_type == "维保/服务对象清单" for item in service_items)
        spare_count = sum(item.list_type == "现场备件库清单" for item in service_items)
        procurement_involved, procurement_note = False, (
            f"不涉及货物采购；已提取{resource_count}项维保/服务对象资源和{spare_count}项现场备件要求，"
            "用于前后向覆盖分析"
        )
    elif procurement_hit:
        procurement_involved, procurement_note = True, "合同提及设备/材料采购或报价清单，但未可靠提取到明细，需人工复核"
    else:
        procurement_involved, procurement_note = False, "合同正文未发现货物采购或设备材料清单，按不涉及货物采购标注"
    contract = ContractStructured(project, direction, str(result.get("合同号", "")), str(result.get("合同名称", "")),
        str(result.get("甲方", "")), str(result.get("乙方", "")), None,
        str(result.get("合同签约日期") or "") or None, None, kind, equipment, plan, scopes,
        {"服务内容": str(result.get("服务内容", "")), "乙方义务": str(result.get("乙方义务", "")),
         "关键条款": str(result.get("关键条款", ""))}, evidence,
        {"file_hash": output.fingerprint, "source_files": output.source_files, "parse_version": "2026.08-v1"},
        [str(result.get("复核原因"))] if result.get("待人工复核") == "是" else [], procurement_involved, procurement_note)
    return contract


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
        review_issues=data.get("review_issues", []), procurement_involved=data.get("procurement_involved"),
        procurement_note=data.get("procurement_note", ""))
