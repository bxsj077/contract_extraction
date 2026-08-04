from __future__ import annotations

import hashlib
import json
import logging
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from .comparisons import RESPONSIBILITY_SCORE, compare_equipment, compare_schedule, compare_scopes, overall_risk
from .pdf_io import LocalOcr, extract_pdf_pages, sha256_file
from .pipeline import load_config
from .project_io import scan_projects
from .rules import analyze_contract
from .storage import ReviewStore
from .structured import analysis_to_structured, structured_from_dict, structured_to_dict
from .system_models import ContractStructured, ProjectFiles, ProjectReviewResult, TimePlan


LOGGER = logging.getLogger("contract_review")
PARSE_VERSION = "2026.08-v2-multi-backward"


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
            file_pages, source, file_errors = extract_pdf_pages(pdf, f"{project.project_code}_{cache_key}",
                self.output_root / "ocr_cache", self.ocr, int(self.config.get("ocr_dpi", 300)),
                int(self.config.get("signature_dpi", 600)), int(self.config.get("native_text_min_chars", 80)),
                int(self.config.get("native_text_min_cjk", 20)), force)
            pages.extend(file_pages); sources.append(source); errors.extend(file_errors)
        analysis = analyze_contract(f"{project.project_code}-{direction}", project.folder, pages, sources,
                                    fingerprint, self.config, errors)
        structured = analysis_to_structured(project.project_code, direction, analysis)
        structured.parse_metadata["bundle_files"] = paths
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
                    current.quantity = (current.quantity or 0) + item.quantity
                    current.evidence_id = ";".join(x for x in (current.evidence_id, item.evidence_id) if x)
        aggregate.equipment = list(equipment.values())

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
        for plan in plans:
            for name, value in plan.milestones.items():
                if value and (name not in milestones or value > milestones[name]):
                    milestones[name] = value
        aggregate.time_plan = TimePlan(max(durations) if durations else None,
            next((p.duration_unit for p in plans if p.duration_unit), ""),
            conditions.pop() if len(conditions) == 1 else "多个后向合同起算条件不一致",
            "；".join(p.start_condition_text for p in plans if p.start_condition_text), min(starts) if starts else None,
            max(finishes) if finishes else None, "全部后向合同完成", None, milestones,
            [x for p in plans for x in p.evidence_ids], min((p.confidence for p in plans), default=0.0))
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
        try:
            forward = self._parse_bundle(project, "前向", project.forward_pdfs, "前向", "前向合同解析结果", force)
            backward_contracts = []
            for index, path in enumerate(project.backward_pdfs, 1):
                name = Path(path).stem
                parsed = self._parse_bundle(project, "后向", [path], f"后向:{index:03d}:{name}",
                                            f"后向合同_{index:03d}_{name}_解析结果", force)
                if parsed:
                    parsed.contract_name = parsed.contract_name or name
                    backward_contracts.append(parsed)
            backward = self._aggregate_backward(project.project_code, backward_contracts)
            equipment = []; schedule = []; scopes = []
            if forward and backward:
                if "运维类" not in {forward.contract_type, backward.contract_type}:
                    equipment = compare_equipment(forward, backward)
                    schedule = compare_schedule(forward, backward, int(self.config.get("safety_buffer_days", 15)))
                    scopes = compare_scopes(forward, backward)
                else:
                    project.issues.append("存在运维类合同，暂不执行设备、工期和实施内容三项核心对比")
                status = "已完成"
            else:
                status = "仅完成单向解析" if forward or backward else "处理失败"
            all_diffs = equipment + schedule + scopes
            risk = overall_risk(all_diffs)
            review_issues = [{"category": "项目完整性", "description": issue} for issue in project.issues]
            for contract in ([forward] if forward else []) + backward_contracts:
                review_issues.extend({"category": f"{contract.direction}合同解析", "description": issue} for issue in contract.review_issues if issue)
            review_issues.extend({"category": d.category, "description": f"{d.title}：{d.description}"} for d in all_diffs if d.needs_review)
            result = ProjectReviewResult(project.project_code, status, risk, forward, backward, equipment, schedule, scopes,
                                         self._timeline(forward, backward), review_issues, processed_at=now,
                                         backward_contracts=backward_contracts)
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
        return result

    def run(self, wanted: set[str] | None = None, force: bool = False) -> dict[str, object]:
        projects = scan_projects(self.contract_root, wanted)
        results = [self.process_project(project, force) for project in projects]
        summary = {"项目数量": len(results), "已完成": sum(r.status == "已完成" for r in results),
                   "处理失败": sum(r.status == "处理失败" for r in results),
                   "高风险": sum(r.risk_level == "高风险" for r in results), "输出目录": str(self.output_root)}
        (self.output_root / "运行摘要.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
