from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from .comparisons import compare_equipment, compare_schedule, compare_scopes, overall_risk
from .pdf_io import LocalOcr, extract_pdf_pages, sha256_file
from .pipeline import load_config
from .project_io import scan_projects
from .rules import analyze_contract
from .storage import ReviewStore
from .structured import analysis_to_structured, structured_from_dict, structured_to_dict
from .system_models import ContractStructured, ProjectFiles, ProjectReviewResult


LOGGER = logging.getLogger("contract_review")
PARSE_VERSION = "2026.08-v1"


class ReviewService:
    def __init__(self, contract_root: Path, output_root: Path, config_path: Path | None = None):
        self.contract_root = contract_root
        self.output_root = output_root
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.config = load_config(config_path)
        self.store = ReviewStore(output_root / "contract_review.db")
        self.ocr = LocalOcr()

    def _parse_contract(self, project: ProjectFiles, direction: str, path: str | None, force: bool) -> ContractStructured | None:
        if not path:
            return None
        pdf = Path(path)
        file_hash = sha256_file(pdf)
        cached = None if force else self.store.load_contract(project.project_code, direction, file_hash, PARSE_VERSION)
        if cached:
            return structured_from_dict(cached)
        pages, source, errors = extract_pdf_pages(pdf, f"{project.project_code}_{direction}", self.output_root / "ocr_cache",
            self.ocr, int(self.config.get("ocr_dpi", 300)), int(self.config.get("signature_dpi", 600)),
            int(self.config.get("native_text_min_chars", 80)), int(self.config.get("native_text_min_cjk", 20)), force)
        analysis = analyze_contract(f"{project.project_code}-{direction}", project.folder, pages, [source], file_hash, self.config, errors)
        structured = analysis_to_structured(project.project_code, direction, analysis)
        payload = structured_to_dict(structured)
        self.store.save_contract(project.project_code, direction, file_hash, PARSE_VERSION, payload)
        detail = self.output_root / "项目明细" / project.project_code
        detail.mkdir(parents=True, exist_ok=True)
        (detail / f"{direction}合同解析结果.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return structured

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
            forward = self._parse_contract(project, "前向", project.forward_pdf, force)
            backward = self._parse_contract(project, "后向", project.backward_pdf, force)
            equipment = []; schedule = []; scopes = []
            if forward and backward:
                if "运维类" not in {forward.contract_type, backward.contract_type}:
                    equipment = compare_equipment(forward, backward)
                    schedule = compare_schedule(forward, backward, int(self.config.get("safety_buffer_days", 15)))
                    scopes = compare_scopes(forward, backward)
                else:
                    project.issues.append("存在运维类合同，按规则暂不执行设备、工期和实施内容三项核心对比")
                status = "已完成"
            else:
                status = "仅完成单份解析" if forward or backward else "处理失败"
            all_diffs = equipment + schedule + scopes
            risk = overall_risk(all_diffs)
            review_issues = [{"category": "项目完整性", "description": issue} for issue in project.issues]
            for contract in (forward, backward):
                if contract:
                    review_issues.extend({"category": f"{contract.direction}合同解析", "description": issue} for issue in contract.review_issues if issue)
            review_issues.extend({"category": d.category, "description": f"{d.title}：{d.description}"} for d in all_diffs if d.needs_review)
            result = ProjectReviewResult(project.project_code, status, risk, forward, backward, equipment, schedule, scopes,
                                         self._timeline(forward, backward), review_issues, processed_at=now)
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
