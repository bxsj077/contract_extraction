from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from .models import ContractOutput, PageText
from .system_models import ContractStructured, EquipmentItem, EvidenceRef, ScopeItem, TimePlan


UNITS = "公斤|千克|千米|立方米|立方|平方米|千块|工作日|日历天|台|套|个|块|只|批|系统|项|组|路|点|端|授权|根|副|张|条|米|吨|年|月"
TABLE_UNIT_PATTERN = "|".join(r"\s*".join(re.escape(char) for char in unit) for unit in UNITS.split("|"))
EQUIPMENT_RE = re.compile(rf"(?P<name>[\u4e00-\u9fffA-Za-z0-9\-（）()/.]{{2,45}}?)\s+(?:(?P<model>[A-Za-z][A-Za-z0-9._/\-]{{2,30}})\s+)?(?P<unit>{UNITS})\s*(?P<qty>\d+(?:\.\d+)?)")
EQUIPMENT_WORDS = re.compile(r"交换机|服务器|防火墙|路由器|存储|软件|平台|系统|模块|终端|摄像机|授权|数据库|线缆|光纤|机柜|配电|网关|钢筋|电缆托架|积水罐|井盖|机制砖|粗砂|碎石|PVC|水泥|混凝土|管材|材料")
TABLE_HINT_RE = re.compile(r"报价表|报价清单|设备清单|材料清单|工程量清单")
PROCUREMENT_TABLE_HEADER_RE = re.compile(
    r"序\s*号.{0,30}名\s*称.{0,30}品\s*牌.{0,60}(?:型\s*号|软件版本号).{0,60}数\s*量",
    re.S,
)
DESCRIPTIVE_PROCUREMENT_HEADER_RE = re.compile(
    r"序\s*号.{0,30}(?:采购\s*内容|名\s*称).{0,40}(?:技术\s*参数|技术\s*要求).{0,80}"
    r"(?:(?:数\s*量.{0,30}单\s*位)|(?:单\s*位.{0,30}数\s*量))",
    re.S,
)
PRICE_TABLE_HEADER_RE = re.compile(
    r"品\s*牌.{0,20}型\s*号.{0,30}不含税单价.{0,30}增值税税\s*率.{0,30}含税单价",
    re.S,
)
QUANTITY_FIRST_TABLE_HEADER_RE = re.compile(
    r"序\s*号.{0,30}名\s*称.{0,30}数\s*量.{0,20}单\s*位.{0,30}品\s*牌.{0,20}型\s*号",
    re.S,
)
SIMPLE_PROCUREMENT_HEADER_RE = re.compile(
    r"序\s*号.{0,30}采购\s*内容.{0,30}数\s*量.{0,20}单\s*位"
    r"(?P<brand_model>.{0,30}品\s*牌.{0,20}型\s*号)?",
    re.S,
)
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


def _clean_table_text(value: str) -> str:
    value = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", value)
    value = re.sub(r"\s*([/\-])\s*", r"\1", value)
    value = re.sub(r"(?<=\d)\s+(?=\d)", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _split_procurement_columns(body: str) -> tuple[str, str, str]:
    tokens = body.split()
    if len(tokens) >= 3 and tokens[-2:] == ["/", "/"]:
        return _clean_table_text(" ".join(tokens[:-2])), "/", "/"
    if len(tokens) >= 3 and tokens[-1] in {"定制", "/"}:
        brand_start = len(tokens) - 2
        if len(tokens) >= 4 and tokens[-3:-1] == ["中电", "鸿信"]:
            brand_start -= 1
        return (_clean_table_text(" ".join(tokens[:brand_start])),
                _clean_table_text("".join(tokens[brand_start:-1])),
                tokens[-1])
    model_index = next((index for index, token in enumerate(tokens[1:], 1)
                        if len(token) >= 4 and (re.search(r"[-_/]", token)
                                                or re.search(r"[A-Za-z]", token) and re.search(r"\d", token)
                                                or re.fullmatch(r"[A-Za-z]{6,}", token))), None)
    if model_index is None or model_index < 2:
        return _clean_table_text(body), "", ""
    return (_clean_table_text(" ".join(tokens[:model_index - 1])),
            _clean_table_text(tokens[model_index - 1]),
            _clean_table_text(" ".join(tokens[model_index:])))


def _procurement_rows(text: str, initial_section: str = "") -> tuple[list[dict[str, Any]], str]:
    """Parse repeated/cross-page product tables whose quantity is written as '73 台'."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    current_section = initial_section
    starts: list[tuple[int, int, str, str]] = []
    product_units = "台|套|个|块|只|批|系统|项|组|路|点|端|授权|根|副"
    for index, line in enumerate(lines):
        if re.fullmatch(r".{2,50}(?:卷烟厂|有限责任公司|有限公司)", line):
            current_section = line
        match = re.match(r"^(\d{1,3})\s+(.+)$", line)
        if match and not re.match(rf"^(?:{product_units})(?:\s|$|[（(])", match.group(2)):
            starts.append((index, int(match.group(1)), match.group(2), current_section))
        elif (line.isdigit() and index + 1 < len(lines)
              and re.search(r"[\u4e00-\u9fffA-Za-z]", lines[index + 1])
              and not re.match(r"序\s*号", lines[index + 1])
              and not re.fullmatch(r".{2,50}(?:卷烟厂|有限责任公司|有限公司)", lines[index + 1])):
            starts.append((index, int(line), "", current_section))
    rows: list[dict[str, Any]] = []
    for row_index, (start, number, first, section) in enumerate(starts):
        end = starts[row_index + 1][0] if row_index + 1 < len(starts) else min(len(lines), start + 18)
        block = re.sub(r"\s+", " ", (first + " " + " ".join(lines[start + 1:end])).strip())
        quantity = re.search(rf"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>{product_units})(?:\s*[（(][^）)]{{1,30}}[）)])?", block)
        if not quantity:
            continue
        body = block[:quantity.start()].strip(" ：:，,;；")
        if not re.search(r"[\u4e00-\u9fffA-Za-z]", body):
            continue
        name, brand, model = _split_procurement_columns(body)
        if not name or len(name) > 100:
            continue
        rows.append({"row_number": number, "section": section, "name": name, "brand": brand, "model": model,
                     "quantity": float(quantity.group("qty")), "unit": quantity.group("unit")})
    return rows, current_section


def _cross_page_product_rows(
        pages: list[PageText]) -> tuple[list[dict[str, Any]], set[tuple[str, int]]]:
    """Extend 产品名称/品牌/规格型号/数量/单位 tables onto pages without a repeated header."""
    rows: list[dict[str, Any]] = []
    consumed: set[tuple[str, int]] = set()
    active = False
    current_file = ""
    expected_row = 1
    section = ""
    product_header_re = re.compile(
        r"序\s*号.{0,30}产品\s*名称.{0,30}品\s*牌.{0,40}规格\s*型号.{0,40}数\s*量.{0,20}单\s*位",
        re.S,
    )
    for page in pages:
        if page.file_name != current_file:
            current_file = page.file_name
            active = False
            expected_row = 1
            section = ""
        has_header = bool(product_header_re.search(page.text))
        if has_header:
            active = True
            expected_row = 1
        if not active:
            continue
        page_rows, section = _procurement_rows(page.text, section)
        if not page_rows:
            if re.search(r"以上合计|设备部分合计|总\s*计", page.text):
                active = False
            continue
        if not has_header and page_rows[0]["row_number"] != expected_row:
            active = False
            continue
        consumed.add((page.file_name, page.page))
        for row in page_rows:
            rows.append({**row, "page": page})
        expected_row = page_rows[-1]["row_number"] + 1
        if re.search(r"以上合计|设备部分合计|总\s*计", page.text):
            active = False
    return rows, consumed


def _simple_procurement_rows(
        pages: list[PageText]) -> tuple[list[dict[str, Any]], set[tuple[str, int]]]:
    """Parse compact 清单 tables ordered as 采购内容/数量/单位[/品牌/型号]."""
    rows: list[dict[str, Any]] = []
    consumed: set[tuple[str, int]] = set()
    current_file = ""
    active = False
    expected_row = 1
    has_brand_model = False
    units = "台|套|个|块|只|批|系统|项|组|路|点|端|授权|根|副|张|条"

    for page in pages:
        if page.file_name != current_file:
            current_file = page.file_name
            active = False
            expected_row = 1
            has_brand_model = False
        header = SIMPLE_PROCUREMENT_HEADER_RE.search(page.text)
        if header and DESCRIPTIVE_PROCUREMENT_HEADER_RE.search(page.text):
            header = None
        if header:
            active = True
            expected_row = 1
            has_brand_model = bool(re.search(r"品\s*牌.{0,30}型\s*号", page.text, re.S))
        if not active:
            continue
        lines = [re.sub(r"\s+", " ", line).strip() for line in page.text.splitlines() if line.strip()]
        starts: list[tuple[int, int, str]] = []
        for index, line in enumerate(lines):
            match = re.match(r"^(\d{1,3})\s+(.+)$", line)
            if match and int(match.group(1)) == expected_row:
                starts.append((index, expected_row, match.group(2)))
                expected_row += 1
        for row_index, (start, number, first) in enumerate(starts):
            end = starts[row_index + 1][0] if row_index + 1 < len(starts) else min(len(lines), start + 8)
            block = re.sub(r"\s+", " ", " ".join([first] + lines[start + 1:end])).split("]", 1)[0].strip()
            parsed = re.match(
                rf"^(?P<name>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>{units})"
                r"(?:\s*/\s*\d*\s*年)?(?:\s+(?P<tail>.*))?$",
                block,
            )
            if not parsed:
                continue
            name = _clean_table_text(parsed.group("name"))
            tail = str(parsed.group("tail") or "").strip()
            brand = model = ""
            if has_brand_model and tail:
                tokens = tail.split()
                if len(tokens) >= 2 and tokens[0] == tokens[1] == "/":
                    brand = model = "/"
                elif tokens:
                    brand = _clean_table_text(tokens[0])
                    model = _clean_table_text(" ".join(tokens[1:])) if len(tokens) > 1 else ""
            rows.append({
                "row_number": number,
                "name": name,
                "brand": brand,
                "model": model,
                "quantity": float(parsed.group("qty")),
                "unit": parsed.group("unit"),
                "page": page,
            })
        if starts:
            consumed.add((page.file_name, page.page))
        if "]" in page.text or re.search(r"甲方\s*[：:]", page.text):
            active = False
    return rows, consumed


def _descriptive_name_and_parameters(first: str, following: list[str]) -> tuple[str, str]:
    """Split a descriptive procurement row into its short name and long specification."""
    first = re.sub(r"\s+", " ", first).strip()
    inline_parameter = ""
    short_parameter = re.match(
        r"^(?P<name>.+?)\s+(?P<parameter>\d+(?:\.\d+)?\s*[KMG](?:bps)?)$",
        first,
        re.I,
    )
    if short_parameter:
        first = short_parameter.group("name")
        inline_parameter = short_parameter.group("parameter")
    enumerated_parameter = re.match(
        r"^(?P<name>.{2,60}?)(?P<parameter>[（(]\d+[）)].*(?:内存|存储|数据|支持|接口|网口).*)$",
        first,
    )
    if enumerated_parameter:
        first = enumerated_parameter.group("name")
        inline_parameter = enumerated_parameter.group("parameter")
    threshold_parameter = re.match(
        r"^(?P<name>.{2,60}?)\s+(?P<parameter>(?:不低于|不少于|不小于|≥|≤).+)$",
        first,
    )
    if threshold_parameter:
        first = threshold_parameter.group("name")
        inline_parameter = threshold_parameter.group("parameter")
    inline = re.match(r"^(?P<name>[^\s，,；;：:]{2,40})\s+(?P<parameter>.+[，,；;。])$", first)
    if inline:
        first = inline.group("name")
        inline_parameter = inline.group("parameter")
    name_parts = [first]
    used = 0
    for line in following[:2]:
        candidate = re.sub(r"\s+", " ", line).strip()
        if (not candidate or len(candidate) > 14 or re.match(r"^[（(【\[]?\d+[.、)]", candidate)
                or re.search(r"[：:；;，,。]", candidate) or "详见" in candidate):
            break
        name_parts.append(candidate)
        used += 1
        if re.search(r"设备\d*$|装置$|套装$|模块$|授权$|服务$|传感器(?:（[^）]+）)?$|监测仪$", candidate):
            break
    parameters = ([inline_parameter] if inline_parameter else []) + following[used:]
    parameters = [line for line in parameters if line and "详见技术规范书" not in line]
    return _clean_table_text("".join(name_parts)), re.sub(r"\s+", " ", " ".join(parameters)).strip()


def _descriptive_procurement_rows(
        pages: list[PageText]) -> tuple[list[dict[str, Any]], set[tuple[str, int]]]:
    """Parse cross-page descriptive quote tables in either unit/quantity order."""
    rows: list[dict[str, Any]] = []
    consumed: set[tuple[str, int]] = set()
    active = False
    current_file = ""
    section = "设备部分"
    expected_row = 1
    product_units = "|".join(sorted(set((UNITS + "|座").split("|")), key=len, reverse=True))
    compound_unit = r"(?:\s*/\s*(?:\d*\s*年|百米|公里))?"
    quantity_first_re = re.compile(
        rf"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>{product_units}){compound_unit}(?=\s|$|[，,；;。])"
    )
    unit_first_re = re.compile(
        rf"(?P<unit>{product_units}){compound_unit}\s*(?P<qty>\d+(?:\.\d+)?)(?=\s|$|[，,；;。])"
    )
    price_tail = (r"\s+(?:(?P<brand>[\u4e00-\u9fffA-Za-z0-9/.-]{1,20})\s+(?P<model>.+?)\s+)?"
                  r"(?P<net_unit>\d+(?:\.\d+)?)\s+(?P<tax>\d{1,2}%)")
    priced_quantity_first_re = re.compile(
        rf"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>{product_units}){compound_unit}{price_tail}"
    )
    priced_unit_first_re = re.compile(
        rf"(?P<unit>{product_units}){compound_unit}\s*(?P<qty>\d+(?:\.\d+)?){price_tail}"
    )

    def parse_segment(lines: list[str], page: PageText, row_start: int, group: str) -> int:
        starts: list[tuple[int, int, str]] = []
        expected = row_start
        for index, line in enumerate(lines):
            match = re.match(r"^(\d{1,3})\s+(.+)$", line)
            if (match and int(match.group(1)) == expected
                    and not re.match(rf"^(?:{product_units})(?:\s|$|[（(])", match.group(2))):
                starts.append((index, expected, match.group(2)))
                expected += 1
        for row_index, (start, number, first) in enumerate(starts):
            end = starts[row_index + 1][0] if row_index + 1 < len(starts) else len(lines)
            block_lines = [first] + lines[start + 1:end]
            block_text = "\n".join(block_lines)
            priced_matches = list(priced_quantity_first_re.finditer(block_text))
            priced_matches.extend(priced_unit_first_re.finditer(block_text))
            quantity_matches = priced_matches or list(quantity_first_re.finditer(block_text))
            if not priced_matches:
                quantity_matches.extend(unit_first_re.finditer(block_text))
            if not quantity_matches:
                continue
            # Technical requirements often contain measurements. The contractual
            # row quantity is the final unit/quantity pair before brand and price.
            quantity = max(quantity_matches, key=lambda match: match.start())
            before_quantity = block_text[:quantity.start()]
            body_lines = [re.sub(r"\s+", " ", line).strip()
                          for line in before_quantity.splitlines() if line.strip()]
            if not body_lines:
                continue
            name, parameters = _descriptive_name_and_parameters(body_lines[0], body_lines[1:])
            if not name or len(name) > 80:
                continue
            brand = _clean_table_text(quantity.groupdict().get("brand") or "")
            model = _clean_table_text(quantity.groupdict().get("model") or "")
            if not model and re.fullmatch(r"\d+(?:\.\d+)?\s*[KMG](?:bps)?", parameters, re.I):
                model = _clean_table_text(parameters)
            rows.append({
                "row_number": number,
                "section": group,
                "name": name,
                "technical_parameters": parameters,
                "quantity": float(quantity.group("qty")),
                "unit": _clean_table_text(quantity.group("unit")),
                "brand": brand,
                "model": model,
                "file_name": page.file_name,
                "page": page,
            })
        return expected

    for page in pages:
        if page.file_name != current_file:
            current_file = page.file_name
            active = False
            section = "设备部分"
            expected_row = 1
        has_header = bool(DESCRIPTIVE_PROCUREMENT_HEADER_RE.search(page.text))
        if has_header and re.search(r"(?:明细报价表|报价明细表)", page.text):
            active = True
            section = "设备部分"
            expected_row = 1
        if not active:
            continue
        consumed.add((page.file_name, page.page))
        lines = [re.sub(r"\s+", " ", line).strip() for line in page.text.splitlines() if line.strip()]
        lines = [line for line in lines if not re.search(r"序\s*号.*采购\s*内容.*技术\s*参数.*数\s*量.*单\s*位", line)]
        equipment_total = next((i for i, line in enumerate(lines) if "设备部分合计" in line), None)
        service_total = next((i for i, line in enumerate(lines) if "服务部分合计" in line), None)
        if section == "设备部分":
            equipment_lines = lines[:equipment_total] if equipment_total is not None else lines
            expected_row = parse_segment(equipment_lines, page, expected_row, section)
            if equipment_total is not None:
                section = "服务部分"
                expected_row = 1
                service_lines = lines[equipment_total + 1:service_total]
                expected_row = parse_segment(service_lines, page, expected_row, section)
        else:
            service_lines = lines[:service_total] if service_total is not None else lines
            expected_row = parse_segment(service_lines, page, expected_row, section)
        if service_total is not None:
            active = False
    return rows, consumed


def _price_quote_rows(
        pages: list[PageText]) -> tuple[dict[str, list[dict[str, str]]], set[tuple[str, int]]]:
    """Read the separate brand/model/price table that follows a descriptive list."""
    by_file: dict[str, list[dict[str, str]]] = {}
    consumed: set[tuple[str, int]] = set()
    active = False
    current_file = ""
    row_re = re.compile(
        r"(?P<prefix>.*?)\s+(?P<net_unit>\d+(?:\.\d+)?)\s+"
        r"(?P<tax>\d{1,2}%)\s+(?P<gross_unit>\d+(?:\.\d+)?)\s+"
        r"(?P<net_total>\d+(?:\.\d+)?)\s+(?P<gross_total>\d+(?:\.\d+)?)(?=\s|$)",
        re.S,
    )
    for page in pages:
        if page.file_name != current_file:
            current_file = page.file_name
            active = False
        if PRICE_TABLE_HEADER_RE.search(page.text):
            active = True
        if not active:
            continue
        consumed.add((page.file_name, page.page))
        text = re.sub(r"南京市（?\d+）?信息化项目明细报价表", " ", page.text)
        text = re.sub(r"品牌\s*型号\s*不含税单价\s*（元）\s*增值税税\s*率\s*含税单价\s*（元）\s*不含税总价\s*（元）\s*含税总价\s*（元）\s*备注", " ", text)
        for match in row_re.finditer(text):
            prefix = re.sub(r"提供安全证\s*书或检测报\s*告", " ", match.group("prefix"))
            if re.search(r"/\s*/\s*$", prefix):
                brand, model = "/", "/"
            elif slash_model := re.search(r"(?:^|\s)/\s+(?P<model>[^/\s].*?)\s*$", prefix, re.S):
                brand = "/"
                model = _clean_table_text(slash_model.group("model"))
            else:
                prefix = re.sub(r"设备部分合计.*", " ", prefix, flags=re.S)
                prefix = re.sub(r"\s+", " ", prefix).strip(" /，,；;")
                brand_model = re.search(r"(?P<brand>[\u4e00-\u9fff]{2,12}|/)\s+(?P<model>.+)$", prefix)
                if not brand_model:
                    continue
                brand = brand_model.group("brand")
                model = _clean_table_text(brand_model.group("model"))
                model = re.sub(r"(?<=[A-Za-z])\s+(?=[A-Za-z])", "", model)
            by_file.setdefault(page.file_name, []).append({
                "brand": brand,
                "model": model,
                "不含税单价": match.group("net_unit"),
                "增值税税率": match.group("tax"),
                "含税单价": match.group("gross_unit"),
                "不含税总价": match.group("net_total"),
                "含税总价": match.group("gross_total"),
            })
        if "总价（含税总限价" in page.text:
            active = False
    return by_file, consumed


def _quantity_first_procurement_rows(
        pages: list[PageText]) -> tuple[list[dict[str, Any]], set[tuple[str, int]]]:
    """Parse tables ordered as 名称/数量/单位/品牌/型号/税率/价格."""
    rows: list[dict[str, Any]] = []
    consumed: set[tuple[str, int]] = set()
    current_file = ""
    active = False
    section = "设备部分"
    expected_row = 1
    units = "台|套|个|块|只|批|系统|项|组|路|点|端|授权|根|副"
    row_value_re = re.compile(
        rf"^(?P<name>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>{units})\s+"
        r"(?P<brand>\S+)\s+(?P<model>.+?)\s+(?P<tax>\d{1,2}%)\s+"
        r"(?P<gross_unit>\d+(?:\.\d+)?)\s+(?P<gross_total>\d+(?:\.\d+)?)"
        r"(?:\s+[A-Z]{2,}[A-Z0-9]+)?$",
        re.S,
    )

    def parse_segment(lines: list[str], page: PageText, row_start: int, group: str) -> int:
        starts: list[tuple[int, int, str]] = []
        expected = row_start
        for index, line in enumerate(lines):
            match = re.match(r"^(\d{1,3})\s+(.+)$", line)
            if (match and int(match.group(1)) == expected
                    and not re.match(rf"^(?:{units})(?:\s|$|[（(])", match.group(2))):
                starts.append((index, expected, match.group(2)))
                expected += 1
        for row_index, (start, number, first) in enumerate(starts):
            end = starts[row_index + 1][0] if row_index + 1 < len(starts) else len(lines)
            block = re.sub(r"\s+", " ", " ".join([first] + lines[start + 1:end])).strip()
            parsed = row_value_re.match(block)
            if not parsed:
                continue
            name = _clean_table_text(parsed.group("name"))
            name = re.sub(
                r"(?<=[A-Za-z0-9.])\s+(?=[\u4e00-\u9fff])|(?<=[\u4e00-\u9fff])\s+(?=[A-Za-z0-9])",
                "", name,
            )
            model = _clean_table_text(parsed.group("model"))
            if not name or len(name) > 100:
                continue
            rows.append({
                "row_number": number,
                "section": group,
                "name": name,
                "brand": _clean_table_text(parsed.group("brand")),
                "model": model,
                "quantity": float(parsed.group("qty")),
                "unit": parsed.group("unit"),
                "增值税税率": parsed.group("tax"),
                "含税单价": parsed.group("gross_unit"),
                "含税总价": parsed.group("gross_total"),
                "page": page,
            })
        return expected

    for page in pages:
        if page.file_name != current_file:
            current_file = page.file_name
            active = False
            section = "设备部分"
            expected_row = 1
        has_header = bool(QUANTITY_FIRST_TABLE_HEADER_RE.search(page.text))
        if has_header and not active:
            active = True
            section = "设备部分"
            expected_row = 1
        if not active:
            continue
        consumed.add((page.file_name, page.page))
        lines = [re.sub(r"\s+", " ", line).strip() for line in page.text.splitlines() if line.strip()]
        equipment_total = next((i for i, line in enumerate(lines) if "设备部分合计" in line), None)
        service_total = next((i for i, line in enumerate(lines) if "服务部分合计" in line), None)

        # Some PDF tables split the final character of the previous row across a
        # page boundary (for example “交换” / next page “机”). Recover that short
        # suffix before scanning the continuation rows.
        if rows and section == "设备部分":
            first_row_at = next((i for i, line in enumerate(lines)
                                 if re.match(rf"^{expected_row}\s+", line)), None)
            if first_row_at:
                suffix = lines[first_row_at - 1].strip()
                if (1 <= len(suffix) <= 4 and re.search(r"[\u4e00-\u9fffA-Za-z]", suffix)
                        and not re.search(r"序|号|名称|数量|单位|品牌|型号|税|价|元|率", suffix)):
                    rows[-1]["name"] = _clean_table_text(rows[-1]["name"] + suffix)

        if section == "设备部分":
            equipment_lines = lines[:equipment_total] if equipment_total is not None else lines
            expected_row = parse_segment(equipment_lines, page, expected_row, section)
            if equipment_total is not None:
                section = "服务部分"
                expected_row = 1
                service_lines = lines[equipment_total + 1:service_total]
                expected_row = parse_segment(service_lines, page, expected_row, section)
        else:
            service_lines = lines[:service_total] if service_total is not None else lines
            expected_row = parse_segment(service_lines, page, expected_row, section)
        if service_total is not None or re.search(r"\n\s*总\s*计\b", page.text):
            active = False
    return rows, consumed


def _extract_equipment(project: str, direction: str, pages: list[PageText], evidence: list[EvidenceRef]) -> list[EquipmentItem]:
    items: list[EquipmentItem] = []
    seen: set[tuple[str, str, float | None]] = set()
    simple_rows, simple_pages = _simple_procurement_rows(pages)
    for row in simple_rows:
        page = row["page"]
        name = row["name"]
        qty = row["quantity"]
        unit = row["unit"]
        key = (name, row["model"], qty)
        if key in seen:
            continue
        seen.add(key)
        ev_id = f"EV-{project}-{direction}-ITEM-{len(items)+1:04d}"
        quote = (f"采购清单第{row['row_number']}项：{name}，数量{qty:g}{unit}，"
                 f"品牌{row['brand'] or '未列明'}，型号{row['model'] or '未列明'}")
        evidence.append(EvidenceRef(ev_id, project, direction, "设备材料清单", name, page.file_name, page.page,
                                    quote, page.method, page.confidence or "",
                                    bool(page.confidence and page.confidence < .9)))
        category = "软件/服务" if re.search(r"软件|平台|模块|算法|服务(?!器)|实施|集成|运维|运营", name) else "设备"
        items.append(EquipmentItem(category, name, name, row["brand"], row["model"], unit, qty,
                                   {}, direction, ev_id, float(page.confidence or .9),
                                   "采购交付清单"))
    product_rows, product_pages = _cross_page_product_rows(pages)
    for row in product_rows:
        page = row["page"]
        name = row["name"]
        qty = row["quantity"]
        unit = row["unit"]
        key = (name, row["model"], qty)
        if key in seen:
            continue
        seen.add(key)
        ev_id = f"EV-{project}-{direction}-ITEM-{len(items)+1:04d}"
        quote = (f"清单第{row['row_number']}项：{name}，品牌{row['brand'] or '未列明'}，"
                 f"型号{row['model'] or '未列明'}，数量{qty:g}{unit}")
        evidence.append(EvidenceRef(ev_id, project, direction, "设备材料清单", name, page.file_name, page.page,
                                    quote, page.method, page.confidence or "",
                                    bool(page.confidence and page.confidence < .9)))
        category = "软件/服务" if re.search(r"软件|平台|模块|授权|服务(?!器)|实施|集成|运维", name) else "设备"
        items.append(EquipmentItem(category, name, name, row["brand"], row["model"], unit, qty,
                                   {}, direction, ev_id, float(page.confidence or .9),
                                   "采购交付清单"))
    quantity_first_rows, quantity_first_pages = _quantity_first_procurement_rows(pages)
    for row in quantity_first_rows:
        page = row["page"]
        name = row["name"]
        qty = row["quantity"]
        unit = row["unit"]
        key = (name, row["model"], qty)
        if key in seen:
            continue
        seen.add(key)
        ev_id = f"EV-{project}-{direction}-ITEM-{len(items)+1:04d}"
        quote = (f"{row['section']}清单第{row['row_number']}项：{name}，数量{qty:g}{unit}，"
                 f"品牌{row['brand']}，型号{row['model']}")
        evidence.append(EvidenceRef(ev_id, project, direction, "设备材料清单", name, page.file_name, page.page,
                                    quote, page.method, page.confidence or "",
                                    bool(page.confidence and page.confidence < .9)))
        parameters = {"清单分组": row["section"], "增值税税率": row["增值税税率"],
                      "含税单价": row["含税单价"], "含税总价": row["含税总价"]}
        category = "软件/服务" if row["section"] == "服务部分" or re.search(
            r"软件|平台|模块|授权|服务(?!器)|实施|集成|运维", name) else "设备"
        items.append(EquipmentItem(category, name, name, row["brand"], row["model"], unit, qty,
                                   parameters, direction, ev_id, float(page.confidence or .9),
                                   "采购交付清单"))
    descriptive_rows, descriptive_pages = _descriptive_procurement_rows(pages)
    price_rows_by_file, price_pages = _price_quote_rows(pages)
    price_indexes: dict[str, int] = {}
    for row in descriptive_rows:
        file_name = row["file_name"]
        price_index = price_indexes.get(file_name, 0)
        file_prices = price_rows_by_file.get(file_name, [])
        price = file_prices[price_index] if price_index < len(file_prices) else {}
        if row.get("brand") or row.get("model"):
            price = {**price, "brand": row.get("brand", ""), "model": row.get("model", "")}
        price_indexes[file_name] = price_index + 1
        page = row["page"]
        name = row["name"]
        qty = row["quantity"]
        unit = row["unit"]
        key = (name, price.get("model", ""), qty)
        if key in seen:
            continue
        seen.add(key)
        ev_id = f"EV-{project}-{direction}-ITEM-{len(items)+1:04d}"
        quote = (f"{row['section']}清单第{row['row_number']}项：{name}，数量{qty:g}{unit}"
                 f"，品牌{price.get('brand') or '未列明'}，型号{price.get('model') or '未列明'}")
        evidence.append(EvidenceRef(ev_id, project, direction, "设备材料清单", name, page.file_name, page.page,
                                    quote, page.method, page.confidence or "",
                                    bool(page.confidence and page.confidence < .9)))
        parameters = {"清单分组": row["section"]}
        if row["technical_parameters"]:
            parameters["技术参数"] = row["technical_parameters"]
        parameters.update({key: value for key, value in price.items() if key not in {"brand", "model"}})
        category = "软件/服务" if row["section"] == "服务部分" or re.search(
            r"软件|平台|模块|授权|服务(?!器)|实施|集成|运维", name) else "设备"
        items.append(EquipmentItem(category, name, name, price.get("brand", ""), price.get("model", ""),
                                   unit, qty, parameters, direction, ev_id,
                                   float(page.confidence or .9), "采购交付清单"))
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
    procurement_section = ""
    for page in pages:
        if ((page.file_name, page.page) in product_pages
                or (page.file_name, page.page) in simple_pages
                or (page.file_name, page.page) in quantity_first_pages
                or (page.file_name, page.page) in descriptive_pages
                or (page.file_name, page.page) in price_pages):
            continue
        if PROCUREMENT_TABLE_HEADER_RE.search(page.text):
            rows, procurement_section = _procurement_rows(page.text, procurement_section)
            for row in rows:
                ev_id = f"EV-{project}-{direction}-ITEM-{len(items)+1:04d}"
                name = row["name"]
                quote = (f"{row['section'] + '，' if row['section'] else ''}清单第{row['row_number']}项：{name}，"
                         f"品牌{row['brand'] or '未列明'}，型号{row['model'] or '未列明'}，"
                         f"数量{row['quantity']:g}{row['unit']}")
                evidence.append(EvidenceRef(ev_id, project, direction, "设备材料清单", name, page.file_name, page.page,
                                            quote, page.method, page.confidence or "",
                                            bool(page.confidence and page.confidence < .9)))
                category = "软件/服务" if re.search(r"软件|平台开发|部署实施|系统集成|授权|费用", name) else "设备"
                parameters = {"清单分组": row["section"]} if row["section"] else {}
                items.append(EquipmentItem(category, name, name, row["brand"], row["model"], row["unit"],
                                           row["quantity"], parameters, direction, ev_id,
                                           float(page.confidence or .9), "采购交付清单"))
            continue
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


def _milestone_candidate(pages: list[PageText], terms: tuple[str, ...]) -> tuple[PageText, str, re.Match[str] | None] | None:
    relative_re = re.compile(
        r"(?:(?:子)?合同(?:签订|签署|生效)|(?:收到|接到).{0,20}?)?(?:后|之日起)\s*"
        r"([0-9一二两三四五六七八九十]+)\s*(个?工作日|日历日|日历天|天|日|个月|月)(?:内)?"
    )
    candidates: list[tuple[int, PageText, str, re.Match[str] | None]] = []
    for page in pages:
        normalized = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", page.text)
        normalized = re.sub(r"[\r\n]+", " ", normalized)
        for term in terms:
            for match in re.finditer(re.escape(term), normalized):
                raw = _sentence(normalized, match.start())
                relative = relative_re.search(raw)
                explicit_date = re.search(r"20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?", raw)
                score = (120 if explicit_date else 0) + (100 if relative else 0)
                score += 20 if re.search(r"完成|应在|应于|不迟于|期限", raw) else 0
                score += min(len(term), 6)
                candidates.append((score, page, raw, relative))
    if not candidates:
        return None
    _, page, raw, relative = max(candidates, key=lambda value: value[0])
    return page, raw, relative


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
    milestone_terms = {
        "到货": ("设备到货", "硬件供货", "完成供货", "供货", "交货", "到货"),
        "初验": ("初验", "初步验收", "进场验收"),
        "终验": ("终验", "最终验收", "竣工验收"),
    }
    milestones: dict[str, str] = {}
    for name, terms in milestone_terms.items():
        hit = _milestone_candidate(pages, terms)
        if not hit:
            details[name] = {"原文": "", "相对期限": "", "计算日期": "", "计算状态": "合同未约定该节点"}
            continue
        page, raw, relative = hit
        date_match = re.search(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?", raw)
        calculated = ""
        if date_match:
            calculated = f"{int(date_match.group(1)):04d}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"
            status = "合同约定了明确日期"
        elif relative:
            relative_value = _cn_number(relative.group(1))
            relative_unit = relative.group(2)
            contract_date = str(result.get("合同签约日期") or "")
            if contract_date and relative_value is not None and relative_unit in {"日", "天", "日历日", "日历天"} and "合同" in relative.group(0):
                calculated = (date.fromisoformat(contract_date) + timedelta(days=relative_value)).isoformat()
                status = "已按合同签约日期计算"
            else:
                status = "有明确相对期限，但缺少可确定的基准日期"
        else:
            status = "合同提及该节点，但未明确时间"
        details[name] = {"原文": raw, "相对期限": relative.group(0) if relative else "", "计算日期": calculated,
                         "计算状态": status}
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
        {"file_hash": output.fingerprint, "source_files": output.source_files,
         "parse_version": "2026.08-v11-procurement-delivery-milestones"},
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
