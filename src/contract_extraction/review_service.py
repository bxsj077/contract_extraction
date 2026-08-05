from __future__ import annotations

import hashlib
import json
import logging
import re
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path

from .comparisons import RESPONSIBILITY_SCORE, compare_equipment, compare_schedule, compare_scopes, overall_risk
from .pdf_io import LocalOcr, extract_pdf_pages, sha256_file
from .pipeline import load_config
from .project_io import scan_projects
from .revenue_plan import compare_income_collection_plan
from .rules import analyze_contract
from .storage import ReviewStore
from .structured import analysis_to_structured, structured_from_dict, structured_to_dict
from .system_models import ContractStructured, ProjectFiles, ProjectReviewResult, TimePlan


LOGGER = logging.getLogger("contract_review")
PARSE_VERSION = "2026.08-v11-procurement-delivery-milestones"
CORRECTABLE_FIELDS = {
    "contract_number", "contract_name", "party_a", "party_b", "sign_date", "effective_date", "contract_type",
    "procurement_involved", "procurement_note", "time_plan.duration_value", "time_plan.duration_unit",
    "time_plan.duration_conclusion", "time_plan.duration_raw", "time_plan.calculation_status",
    "time_plan.start_condition_type", "time_plan.start_condition_text", "time_plan.start_date",
    "time_plan.finish_date", "time_plan.completion_node", "time_plan.fixed_deadline",
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
        self.store = ReviewStore(output_root / "contract_review.db")
        self.ocr = LocalOcr()
        log_dir = output_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = (log_dir / "contract_review.log").resolve()
        if not any(isinstance(h, logging.FileHandler) and Path(h.baseFilename).resolve() == log_path for h in LOGGER.handlers):
            handler = logging.FileHandler(log_path, encoding="utf-8", delay=True)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            LOGGER.addHandler(handler)
            LOGGER.setLevel(logging.INFO)

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
            elif field_path == "procurement_involved" and isinstance(value, str):
                value = value.strip().lower() in {"true", "1", "是", "涉及"}
            target: object = contract
            parts = field_path.split(".")
            for part in parts[:-1]:
                target = getattr(target, part)
            setattr(target, parts[-1], value)
            corrected_paths.add(field_path)
            applied.append({"id": correction["id"], "contract_key": contract_key, "field_path": field_path,
                            "corrected_value": value, "note": correction.get("note", ""),
                            "updated_at": correction.get("updated_at", "")})
        if not applied:
            return contract
        plan = contract.time_plan
        if "time_plan.duration_value" in corrected_paths or "time_plan.duration_unit" in corrected_paths:
            if plan.duration_value is not None:
                if "time_plan.duration_conclusion" not in corrected_paths:
                    plan.duration_conclusion = f"人工确认：{plan.duration_value}{plan.duration_unit}"
                if plan.start_date and plan.duration_unit in {"日", "天", "工作日", "日历天", "个工作日"}:
                    try:
                        plan.finish_date = (date.fromisoformat(plan.start_date) + timedelta(days=plan.duration_value)).isoformat()
                        plan.calculation_status = "已按人工确认的工期计算完成日期"
                    except ValueError:
                        plan.finish_date = None
                        plan.calculation_status = "人工确认的起算日期格式无效，应使用YYYY-MM-DD"
                else:
                    plan.finish_date = None
                    plan.calculation_status = "工期已人工确认，但缺少可计算的起算日期或单位"
            else:
                plan.finish_date = None
        contract.parse_metadata["applied_corrections"] = applied
        contract.parse_metadata["correction_count"] = len(applied)
        return contract

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
                self.output_root / "ocr_cache", self.ocr, int(self.config.get("ocr_dpi", 300)),
                int(self.config.get("signature_dpi", 600)), int(self.config.get("native_text_min_chars", 80)),
                int(self.config.get("native_text_min_cjk", 20)), force)
            pages.extend(file_pages); sources.append(source); errors.extend(file_errors)
            LOGGER.info("完成读取 %s：%s页，OCR错误%s项", pdf.name, source.get("页数", 0), len(file_errors))
        analysis = analyze_contract(f"{project.project_code}-{direction}", project.folder, pages, sources,
                                    fingerprint, self.config, errors)
        structured = analysis_to_structured(project.project_code, direction, analysis)
        structured.parse_metadata["bundle_files"] = paths
        structured.parse_metadata.update({"file_count": len(paths), "page_count": len(pages), "ocr_error_count": len(errors),
                                          "errors": errors, "parse_status": "完整" if not errors else "部分完成"})
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
                forward, project.revenue_plan_files, int(self.config.get("plan_date_tolerance_days", 31)))
            if forward and backward:
                equipment = compare_equipment(forward, backward)
                if "运维类" not in {forward.contract_type, backward.contract_type}:
                    schedule = compare_schedule(forward, backward, int(self.config.get("safety_buffer_days", 15)))
                    scopes = compare_scopes(forward, backward)
                else:
                    project.issues.append("存在运维类合同，仅执行设备/服务对象清单覆盖和收入收款计划复核，不执行建设工期及实施责任对比")
                parsed_contracts = ([forward] if forward else []) + backward_contracts
                status = "部分完成" if any(c.parse_metadata.get("parse_status") != "完整" for c in parsed_contracts) else "已完成"
            else:
                status = "仅完成单向解析" if forward or backward else "处理失败"
            all_diffs = equipment + schedule + scopes + plan_differences
            risk = overall_risk(all_diffs)
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
