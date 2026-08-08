from __future__ import annotations

import hashlib
import json
import logging
import re
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path

from .comparisons import (
    RESPONSIBILITY_SCORE,
    compare_equipment,
    compare_schedule,
    compare_scopes,
    overall_risk,
    project_overall_risk,
)
from .date_utils import calculate_end_date
from .pdf_io import LocalOcr, extract_pdf_pages, sha256_file
from .pipeline import load_config
from .project_io import scan_projects
from .revenue_plan import compare_income_collection_plan
from .rules import analyze_contract
from .storage import ReviewStore, finding_key
from .structured import analysis_to_structured, structured_from_dict, structured_to_dict
from .system_models import ContractStructured, ProjectFiles, ProjectReviewResult, TimePlan


LOGGER = logging.getLogger("contract_review")
PARSE_VERSION = "2026.08-ocr-v2-server-device-v4"
CORRECTABLE_FIELDS = {
    "contract_number", "contract_name", "party_a", "party_b", "amount_yuan", "sign_date", "effective_date", "contract_type",
    "procurement_involved", "procurement_note", "time_plan.duration_value", "time_plan.duration_unit",
    "time_plan.duration_conclusion", "time_plan.duration_raw", "time_plan.calculation_status",
    "time_plan.start_condition_type", "time_plan.start_condition_text", "time_plan.start_date",
    "time_plan.finish_date", "time_plan.completion_node", "time_plan.fixed_deadline",
    "key_clauses.服务内容", "key_clauses.乙方义务", "key_clauses.关键条款",
    *(f"time_plan.milestones.{node}" for node in ("到货", "初验", "终验")),
    *(f"time_plan.milestone_details.{node}.{field}" for node in ("到货", "初验", "终验")
      for field in ("原文", "相对期限", "计算日期", "计算状态")),
}


def _bundle_hash(paths: list[str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        pdf = Path(path)
        digest.update(pdf.name.encode("utf-8"))
        digest.update(sha256_file(pdf).encode("ascii"))
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", "_", value)[:100]


class ReviewService:
    def __init__(self, contract_root: Path, output_root: Path, config_path: Path | None = None):
        self.contract_root = contract_root
        self.output_root = output_root
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.config = load_config(config_path)
        self.store = ReviewStore(output_root / "contract_review_v2.db")
        v2_root = Path(__file__).resolve().parents[2]
        default_output = (v2_root / "data" / "review_output").resolve()
        self.ocr_cache_root = (
            v2_root / "data" / "ocr_cache"
            if output_root.resolve() == default_output
            else output_root / "ocr_cache"
        )
        self.ocr_cache_root.mkdir(parents=True, exist_ok=True)
        self.ocr = LocalOcr()
        log_dir = output_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = (log_dir / "contract_review.log").resolve()
        if not any(isinstance(h, logging.FileHandler) and Path(h.baseFilename).resolve() == log_path for h in LOGGER.handlers):
            handler = logging.FileHandler(log_path, encoding="utf-8", delay=True)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            LOGGER.addHandler(handler)
            LOGGER.setLevel(logging.INFO)

    @staticmethod
    def _iso_date(value: object) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _relative_deadline(value: object) -> tuple[int, str] | None:
        match = re.search(
            r"([0-9]{1,4}|[一二两三四五六七八九十]{1,3})\s*"
            r"(个?工作日|日历日|日历天|天|日|个月|月|年)",
            str(value or ""),
        )
        if not match:
            return None
        raw = match.group(1)
        if raw.isdigit():
            amount = int(raw)
        else:
            digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                      "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
            if raw == "十":
                amount = 10
            elif "十" in raw:
                left, right = raw.split("十", 1)
                amount = digits.get(left, 1) * 10 + digits.get(right, 0)
            elif raw in digits:
                amount = digits[raw]
            else:
                return None
        return amount, match.group(2)

    def _recalculate_timeline(self, contract: ContractStructured, corrected_paths: set[str]) -> None:
        """Cascade manual date/duration corrections through dependent contract nodes."""
        plan = contract.time_plan
        manual_start = "time_plan.start_date" in corrected_paths
        manual_finish = "time_plan.finish_date" in corrected_paths
        sign_date = self._iso_date(contract.sign_date)
        effective_date = self._iso_date(contract.effective_date)
        start_type = str(plan.start_condition_type or "")

        if not manual_start and start_type != "固定日期区间":
            if re.search(r"合同(?:签订|签署)|签约归档", start_type) and sign_date:
                plan.start_date = sign_date.isoformat()
            elif "合同生效" in start_type and effective_date:
                plan.start_date = effective_date.isoformat()
            elif re.search(r"甲方通知|开工令|进场通知|开工通知", start_type):
                # These clauses require the actual notice date; a signing date is not a substitute.
                plan.start_date = None
            elif "sign_date" in corrected_paths and sign_date:
                # The contract has no reliable start clause. Still provide a transparent planning estimate.
                plan.start_date = sign_date.isoformat()

        start_date = self._iso_date(plan.start_date)
        if plan.duration_value is not None and "time_plan.duration_conclusion" not in corrected_paths:
            plan.duration_conclusion = f"人工确认：{plan.duration_value}{plan.duration_unit}" if (
                "time_plan.duration_value" in corrected_paths or "time_plan.duration_unit" in corrected_paths
            ) else plan.duration_conclusion

        if not manual_finish and start_date and plan.duration_value is not None:
            finish, note = calculate_end_date(start_date, int(plan.duration_value), str(plan.duration_unit))
            if finish:
                plan.finish_date = finish.isoformat()
                if "sign_date" in corrected_paths and not re.search(r"甲方通知|开工令|进场通知|开工通知", start_type):
                    prefix = "已按人工补录签订日期联动计算"
                else:
                    prefix = "已按起算日期和工期联动计算"
                plan.calculation_status = prefix + (f"；{note}" if note else "")
            else:
                plan.finish_date = None
                plan.calculation_status = note or "工期单位不支持自动计算"
        elif not manual_finish and plan.duration_value is not None and not start_date:
            plan.finish_date = None
            if re.search(r"甲方通知|开工令|进场通知|开工通知", start_type):
                plan.calculation_status = "已识别工期，但需人工补录甲方通知或开工令日期后才能联动计算"
            else:
                plan.calculation_status = "已识别工期，但缺少可计算的起算日期"

        calculated_nodes: dict[str, date] = {}
        overall_finish = self._iso_date(plan.finish_date)
        for node in ("到货", "初验", "终验"):
            detail = plan.milestone_details.setdefault(
                node, {"原文": "", "相对期限": "", "计算日期": "", "计算状态": "合同未约定该节点"})
            manual_node = (f"time_plan.milestones.{node}" in corrected_paths
                           or f"time_plan.milestone_details.{node}.计算日期" in corrected_paths)
            if manual_node:
                manual_value = self._iso_date(detail.get("计算日期") or plan.milestones.get(node))
                if manual_value:
                    calculated_nodes[node] = manual_value
                continue
            if detail.get("计算状态") == "合同约定了明确日期":
                explicit = self._iso_date(detail.get("计算日期") or plan.milestones.get(node))
                if explicit:
                    invalid_before_start = bool(start_date and explicit < start_date)
                    invalid_final_before_finish = bool(node == "终验" and overall_finish and explicit < overall_finish)
                    if not invalid_before_start and not invalid_final_before_finish:
                        calculated_nodes[node] = explicit
                        continue
                    detail["计算日期"] = ""
                    plan.milestones.pop(node, None)
                    detail["计算状态"] = (
                        "原识别日期早于项目起算或整体完工日期，已判定为时间异常并重新联动计算"
                    )

            relative_text = str(detail.get("相对期限") or "")
            relative = self._relative_deadline(relative_text)
            calculated: date | None = None
            basis_name = ""
            if relative:
                prefix = relative_text[:relative_text.find(str(relative[0]))] if str(relative[0]) in relative_text else relative_text
                if re.search(r"合同(?:签订|签署)", prefix):
                    calculated, basis_name = sign_date, "合同签订日期"
                elif "合同生效" in prefix:
                    calculated, basis_name = effective_date, "合同生效日期"
                elif re.search(r"开工令|甲方通知|进场通知|开工通知|开工", prefix):
                    calculated, basis_name = start_date, "开工日期"
                elif re.search(r"到货|交货|供货(?:完成|结束)", prefix):
                    calculated, basis_name = calculated_nodes.get("到货"), "到货日期"
                elif re.search(r"初验|初步验收", prefix):
                    calculated, basis_name = calculated_nodes.get("初验"), "初验日期"
                elif re.search(r"项目完工|项目完成|整体完工|整体完成", prefix):
                    calculated, basis_name = overall_finish, "项目整体完成日期"
                else:
                    calculated, basis_name = None, "基准日期"
                if calculated:
                    calculated, note = calculate_end_date(calculated, relative[0], relative[1])
                    if calculated:
                        detail["计算日期"] = calculated.isoformat()
                        detail["计算状态"] = f"已按{basis_name}及相对期限联动计算" + (f"；{note}" if note else "")
                        plan.milestones[node] = calculated.isoformat()
                        calculated_nodes[node] = calculated
                        continue
                detail["计算日期"] = ""
                plan.milestones.pop(node, None)
                detail["计算状态"] = f"有明确相对期限，但缺少可确定的{basis_name or '基准日期'}"

        # Overall duration normally represents completion/final acceptance. Use it as a derived
        # terminal-acceptance date only when the contract has no independent explicit/manual date.
        final_path = "time_plan.milestone_details.终验.计算日期"
        final_manual = final_path in corrected_paths or "time_plan.milestones.终验" in corrected_paths
        final_detail = plan.milestone_details.setdefault(
            "终验", {"原文": "", "相对期限": "", "计算日期": "", "计算状态": "合同未约定该节点"})
        final_explicit = final_detail.get("计算状态") == "合同约定了明确日期"
        finish_date = self._iso_date(plan.finish_date)
        final_calculated = self._iso_date(final_detail.get("计算日期") or plan.milestones.get("终验"))
        if finish_date and not final_manual and not final_explicit and not final_calculated:
            plan.milestones["终验"] = finish_date.isoformat()
            final_detail["计算日期"] = finish_date.isoformat()
            final_detail["计算状态"] = "按项目整体工期完成日推算终验日期"

    def _apply_corrections(self, contract: ContractStructured | None, contract_key: str) -> ContractStructured | None:
        if contract is None:
            return None
        applied = []
        corrected_paths: set[str] = set()
        for correction in self.store.list_corrections(contract.project_code):
            field_path = correction["field_path"]
            if correction["contract_key"] != contract_key or field_path not in CORRECTABLE_FIELDS:
                continue
            value = correction["corrected_value"]
            if field_path == "time_plan.duration_value" and value not in (None, ""):
                value = int(value)
            elif field_path == "amount_yuan" and value not in (None, ""):
                value = float(value)
            elif field_path == "procurement_involved" and isinstance(value, str):
                value = value.strip().lower() in {"true", "1", "是", "涉及"}
            target: object = contract
            parts = field_path.split(".")
            for part in parts[:-1]:
                if isinstance(target, dict):
                    target = target.setdefault(part, {})
                else:
                    target = getattr(target, part)
            if isinstance(target, dict):
                target[parts[-1]] = value
            else:
                setattr(target, parts[-1], value)
            corrected_paths.add(field_path)
            applied.append({"id": correction["id"], "contract_key": contract_key, "field_path": field_path,
                            "corrected_value": value, "note": correction.get("note", ""),
                            "updated_at": correction.get("updated_at", "")})
        if "time_plan.duration_raw" not in corrected_paths and contract.time_plan.duration_value is not None:
            duration_evidence = next((item for item in contract.evidence if item.field_name == "工期原文"), None)
            if duration_evidence and duration_evidence.value:
                evidence_text = str(duration_evidence.value)
                evidence_number = re.search(r"工期.{0,30}?([0-9]{1,4})\s*(?:个?工作日|日历日|日历天|天|日|个月|月|年)", evidence_text)
                if evidence_number and int(evidence_number.group(1)) == int(contract.time_plan.duration_value):
                    contract.time_plan.duration_raw = evidence_text
        self._recalculate_timeline(contract, corrected_paths)
        if applied:
            contract.parse_metadata["applied_corrections"] = applied
            contract.parse_metadata["correction_count"] = len(applied)
        return contract

    def _apply_finding_overrides(self, project_code: str, category: str, findings: list) -> list:
        overrides = self.store.finding_overrides(project_code, category)
        for finding in findings:
            override = overrides.get(finding_key(category, finding))
            if not override:
                continue
            finding.status = override["status"]
            finding.risk_level = override["risk_level"]
            finding.description = override["description"]
        return findings

    def _parse_bundle(self, project: ProjectFiles, direction: str, paths: list[str], cache_key: str,
                      output_name: str, force: bool) -> ContractStructured | None:
        if not paths:
            return None
        fingerprint = _bundle_hash(paths)
        cached = None if force else self.store.load_contract(project.project_code, cache_key, fingerprint, PARSE_VERSION)
        if cached:
            return structured_from_dict(cached)
        pages = []; sources = []; errors = []
        for path in paths:
            pdf = Path(path)
            LOGGER.info("开始解析 %s合同：%s", direction, pdf)
            safe_cache_id = _safe_name(f"{project.project_code}_{cache_key}")
            file_pages, source, file_errors = extract_pdf_pages(pdf, safe_cache_id,
                self.ocr_cache_root, self.ocr, int(self.config.get("ocr_dpi", 300)),
                int(self.config.get("signature_dpi", 600)), int(self.config.get("native_text_min_chars", 80)),
                int(self.config.get("native_text_min_cjk", 20)), force,
                dict(self.config.get("ocr_v2", {})))
            pages.extend(file_pages); sources.append(source); errors.extend(file_errors)
            LOGGER.info("完成读取 %s：%s页，OCR错误%s项", pdf.name, source.get("页数", 0), len(file_errors))
        analysis = analyze_contract(f"{project.project_code}-{direction}", project.folder, pages, sources,
                                    fingerprint, self.config, errors)
        structured = analysis_to_structured(project.project_code, direction, analysis)
        structured.parse_metadata["bundle_files"] = paths
        structured.parse_metadata.update({"file_count": len(paths), "page_count": len(pages), "ocr_error_count": len(errors),
                                          "errors": errors, "parse_status": "完整" if not errors else "部分完成",
                                          "ocr_version": "V2", "ocr_engine": "RapidOCR / PP-OCRv6",
                                          "ocr_page_diagnostics": [
                                              {"file_name": page.file_name, "page": page.page,
                                               "page_type": page.page_type, "method": page.method,
                                               "mean_confidence": page.confidence,
                                               "table_rows": page.table_rows,
                                               "diagnostics": page.diagnostics}
                                              for page in pages
                                          ]})
        if errors:
            structured.review_issues.append(f"存在{len(errors)}项页面读取或OCR错误，解析结果需复核")
        payload = structured_to_dict(structured)
        self.store.save_contract(project.project_code, cache_key, fingerprint, PARSE_VERSION, payload)
        detail = self.output_root / "项目明细" / project.project_code
        detail.mkdir(parents=True, exist_ok=True)
        (detail / f"{_safe_name(output_name)}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return structured

    @staticmethod
    def _aggregate_backward(project_code: str, contracts: list[ContractStructured]) -> ContractStructured | None:
        if not contracts:
            return None
        if len(contracts) == 1:
            return contracts[0]
        aggregate = ContractStructured(project_code, "后向汇总", contract_name=f"{len(contracts)}份后向合同汇总")
        aggregate.evidence = [e for contract in contracts for e in contract.evidence]
        aggregate.review_issues = [i for contract in contracts for i in contract.review_issues]
        types = {c.contract_type for c in contracts}
        aggregate.contract_type = types.pop() if len(types) == 1 else "无法确定"
        if aggregate.contract_type == "无法确定":
            aggregate.review_issues.append("多份后向合同性质不一致，汇总比较结果需人工确认")

        equipment: dict[tuple[str, str, str], object] = {}
        for contract in contracts:
            for item in contract.equipment:
                key = ((item.model or item.standard_name).lower().replace(" ", ""), item.standard_name, item.unit)
                if key not in equipment:
                    equipment[key] = deepcopy(item)
                elif item.quantity is not None:
                    current = equipment[key]
                    current.quantity = round((current.quantity or 0) + item.quantity, 6)
                    current.evidence_id = ";".join(x for x in (current.evidence_id, item.evidence_id) if x)
        aggregate.equipment = list(equipment.values())
        involved = [c.procurement_involved for c in contracts]
        aggregate.procurement_involved = True if True in involved else (False if involved and all(x is False for x in involved) else None)
        aggregate.procurement_note = "；".join(f"{c.contract_name or i + 1}：{c.procurement_note}" for i, c in enumerate(contracts))

        scopes = {}
        for contract in contracts:
            for item in contract.scopes:
                old = scopes.get(item.scope_item)
                if old is None or RESPONSIBILITY_SCORE.get(item.responsibility, 0) > RESPONSIBILITY_SCORE.get(old.responsibility, 0):
                    scopes[item.scope_item] = deepcopy(item)
        aggregate.scopes = list(scopes.values())

        plans = [c.time_plan for c in contracts]
        starts = [p.start_date for p in plans if p.start_date]
        finishes = [p.finish_date for p in plans if p.finish_date]
        durations = [p.duration_value for p in plans if p.duration_value is not None]
        conditions = {p.start_condition_type for p in plans}
        milestones: dict[str, str] = {}
        milestone_details: dict[str, dict[str, object]] = {}
        for plan in plans:
            for name, value in plan.milestones.items():
                if value and (name not in milestones or value > milestones[name]):
                    milestones[name] = value
        for node in ("到货", "初验", "终验"):
            node_values = [p.milestone_details.get(node, {}) for p in plans]
            statuses = list(dict.fromkeys(str(x.get("计算状态", "未提取")) for x in node_values))
            milestone_details[node] = {"原文": "；".join(str(x.get("原文", "")) for x in node_values if x.get("原文")),
                "相对期限": "；".join(str(x.get("相对期限", "")) for x in node_values if x.get("相对期限")),
                "计算日期": max((str(x.get("计算日期")) for x in node_values if x.get("计算日期")), default=""),
                "计算状态": statuses[0] if len(statuses) == 1 else "多份后向合同节点约定不一致：" + "；".join(statuses)}
        calc_statuses = list(dict.fromkeys(p.calculation_status for p in plans if p.calculation_status))
        conclusions = list(dict.fromkeys(p.duration_conclusion for p in plans if p.duration_conclusion))
        aggregate.time_plan = TimePlan(duration_value=max(durations) if durations else None,
            duration_unit=next((p.duration_unit for p in plans if p.duration_unit), ""),
            start_condition_type=conditions.pop() if len(conditions) == 1 else "多个后向合同起算条件不一致",
            start_condition_text="；".join(p.start_condition_text for p in plans if p.start_condition_text),
            start_date=min(starts) if starts else None, finish_date=max(finishes) if finishes else None,
            completion_node="全部后向合同完成", milestones=milestones,
            evidence_ids=[x for p in plans for x in p.evidence_ids],
            confidence=min((p.confidence for p in plans), default=0.0),
            duration_raw="；".join(dict.fromkeys(p.duration_raw for p in plans if p.duration_raw)),
            calculation_status=calc_statuses[0] if len(calc_statuses) == 1 else "；".join(calc_statuses),
            milestone_details=milestone_details,
            duration_conclusion=conclusions[0] if len(conclusions) == 1 else "；".join(conclusions))
        aggregate.sign_date = max((c.sign_date for c in contracts if c.sign_date), default=None)
        aggregate.parse_metadata = {"aggregation": "multiple_backward_contracts", "contract_count": len(contracts),
                                    "source_contracts": [c.parse_metadata for c in contracts]}
        return aggregate

    @staticmethod
    def _timeline(forward: ContractStructured | None, backward: ContractStructured | None) -> list[dict[str, object]]:
        names = ["合同签订", "合同生效", "开工", "设备到货", "安装完成", "系统调试", "试运行", "初验", "终验", "质保"]
        rows = []
        for name in names:
            f_value = (forward.sign_date if name == "合同签订" and forward else None) or (forward.time_plan.milestones.get(name, "") if forward else "")
            b_value = (backward.sign_date if name == "合同签订" and backward else None) or (backward.time_plan.milestones.get(name, "") if backward else "")
            rows.append({"node": name, "forward": f_value or "", "backward": b_value or "",
                         "difference": "节点缺失" if bool(f_value) != bool(b_value) else ("时间不一致" if f_value and b_value and f_value != b_value else "")})
        return rows

    def process_project(self, project: ProjectFiles, force: bool = False) -> ProjectReviewResult:
        now = datetime.now().isoformat(timespec="seconds")
        LOGGER.info("项目%s开始处理：前向%s份，后向%s份", project.project_code, len(project.forward_pdfs), len(project.backward_pdfs))
        try:
            detail = self.output_root / "项目明细" / project.project_code
            detail.mkdir(parents=True, exist_ok=True)
            for stale in detail.glob("后向合同_*_解析结果.json"):
                stale.unlink(missing_ok=True)
            forward = self._parse_bundle(project, "前向", project.forward_pdfs, "前向", "前向合同解析结果", force)
            forward = self._apply_corrections(forward, "前向")
            backward_contracts = []
            for index, path in enumerate(project.backward_pdfs, 1):
                name = Path(path).stem
                parsed = self._parse_bundle(project, "后向", [path], f"后向:{index:03d}:{name}",
                                            f"后向合同_{index:03d}_{name}_解析结果", force)
                if parsed:
                    parsed.contract_name = parsed.contract_name or name
                    parsed = self._apply_corrections(parsed, f"后向:{index:03d}")
                    backward_contracts.append(parsed)
            backward = self._aggregate_backward(project.project_code, backward_contracts)
            equipment = []; schedule = []; scopes = []
            plan_differences = compare_income_collection_plan(
                forward, project.revenue_plan_files, int(self.config.get("plan_date_tolerance_days", 31)),
                backward_contracts=backward_contracts)
            if forward and backward:
                equipment = compare_equipment(forward, backward)
                dismissed_equipment = self.store.dismissed_finding_keys(project.project_code, "equipment")
                equipment = [item for item in equipment if finding_key("equipment", item) not in dismissed_equipment]
                if "运维类" not in {forward.contract_type, backward.contract_type}:
                    for index, backward_contract in enumerate(backward_contracts, 1):
                        contract_name = backward_contract.contract_name or backward_contract.contract_number or f"后向合同{index}"
                        schedule.extend(compare_schedule(
                            forward, backward_contract, int(self.config.get("safety_buffer_days", 15)),
                            f"后向合同{index}：{contract_name}",
                        ))
                    scopes = compare_scopes(forward, backward)
                    schedule = self._apply_finding_overrides(project.project_code, "schedule", schedule)
                    scopes = self._apply_finding_overrides(project.project_code, "scope", scopes)
                else:
                    project.issues.append("存在运维类合同，仅执行设备/服务对象清单覆盖和收入收款计划复核，不执行建设工期及实施责任对比")
                parsed_contracts = ([forward] if forward else []) + backward_contracts
                status = "部分完成" if any(c.parse_metadata.get("parse_status") != "完整" for c in parsed_contracts) else "已完成"
            else:
                status = "仅完成单向解析" if forward or backward else "处理失败"
            all_diffs = equipment + schedule + scopes + plan_differences

            risk = project_overall_risk(
                equipment_differences=equipment,
                schedule_differences=schedule,
                scope_differences=scopes,
                plan_differences=plan_differences,
                review_issue_count=len(project.issues),
            )
            review_issues = [{"category": "项目完整性", "description": issue} for issue in project.issues]
            for contract in ([forward] if forward else []) + backward_contracts:
                review_issues.extend({"category": f"{contract.direction}合同解析", "description": issue} for issue in contract.review_issues if issue)
            review_issues.extend({"category": d.category, "description": f"{d.title}：{d.description}"} for d in all_diffs if d.needs_review)
            parse_statuses = []
            for contract in ([forward] if forward else []) + backward_contracts:
                meta = contract.parse_metadata
                parse_statuses.append({"合同方向": contract.direction, "合同名称": contract.contract_name,
                    "源文件": "；".join(Path(x).name for x in meta.get("bundle_files", [])),
                    "文件数": meta.get("file_count", 0), "页数": meta.get("page_count", 0),
                    "OCR错误数": meta.get("ocr_error_count", 0), "解析状态": meta.get("parse_status", "未知"),
                    "设备材料清单项数": len(contract.equipment), "采购备注": contract.procurement_note})
            result = ProjectReviewResult(project.project_code, status, risk, forward, backward, equipment, schedule, scopes,
                                         self._timeline(forward, backward), review_issues, processed_at=now,
                                         backward_contracts=backward_contracts, contract_parse_statuses=parse_statuses,
                                         plan_differences=plan_differences)
        except Exception as exc:
            LOGGER.exception("项目%s处理失败", project.project_code)
            result = ProjectReviewResult(project.project_code, "处理失败", "待确认", None, None,
                review_issues=[{"category": "系统异常", "description": str(exc)}], processed_at=now)
        payload = result.to_dict()
        self.store.upsert_project(project.project_code, project.folder, result.status, result.risk_level, payload)
        self.store.replace_issues(project.project_code, result.review_issues)
        detail = self.output_root / "项目明细" / project.project_code
        detail.mkdir(parents=True, exist_ok=True)
        (detail / "前后向审查结果.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("项目%s处理结束：%s，风险%s", project.project_code, result.status, result.risk_level)
        return result

    def run(self, wanted: set[str] | None = None, force: bool = False) -> dict[str, object]:
        projects = scan_projects(self.contract_root, wanted)
        results = [self.process_project(project, force) for project in projects]
        summary = {"项目数量": len(results), "已完成": sum(r.status == "已完成" for r in results),
                   "处理失败": sum(r.status == "处理失败" for r in results),
                   "高风险": sum(r.risk_level == "高风险" for r in results), "输出目录": str(self.output_root)}
        (self.output_root / "运行摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
