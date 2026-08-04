from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contract_extraction.comparisons import compare_equipment, compare_schedule, compare_scopes
from contract_extraction.api import create_app
from contract_extraction.review_export import _header_cn
from contract_extraction.project_io import scan_projects
from contract_extraction.system_models import ContractStructured, EquipmentItem, ScopeItem, TimePlan


class ReviewSystemTests(unittest.TestCase):
    def contract(self, direction: str) -> ContractStructured:
        return ContractStructured("P001", direction, contract_type="集成实施类")

    def test_project_integrity_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "000123"; folder.mkdir()
            (folder / "前向合同.pdf").touch()
            project = scan_projects(Path(tmp))[0]
            self.assertEqual(project.project_code, "000123")
            self.assertEqual(project.status, "可解析单份合同")
            self.assertIn("缺少后向合同", project.issues)

    def test_api_uses_explicit_storage_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "contracts"
            output = Path(tmp) / "results"
            app = create_app(root, output)
            config_route = next(route for route in app.routes if getattr(route, "path", "") == "/api/config")
            config = config_route.endpoint()
            self.assertEqual(config["合同上传根目录"], str(root))
            self.assertEqual(config["审查结果目录"], str(output))
            self.assertTrue(root.exists())
            self.assertTrue(any(getattr(route, "path", "") == "/api/tasks/{task_id}" for route in app.routes))
            self.assertTrue(any(getattr(route, "path", "") == "/api/tasks" for route in app.routes))
            self.assertTrue(any(getattr(route, "path", "") == "/api/projects/{project_code}" and
                                "DELETE" in getattr(route, "methods", set()) for route in app.routes))

    def test_delete_project_removes_files_results_and_database_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "contracts"
            output = Path(tmp) / "results"
            app = create_app(root, output)
            service = app.state.service
            code = "DELETE001"
            project = root / code
            (project / "前向").mkdir(parents=True)
            (project / "后向").mkdir()
            (project / "前向" / "合同.pdf").touch()
            detail = output / "项目明细" / code
            detail.mkdir(parents=True)
            (detail / "结果.json").touch()
            cache = output / "ocr_cache" / f"{code}_前向"
            cache.mkdir(parents=True)
            service.store.upsert_project(code, str(project), "处理失败", "待确认", {"project_code": code})
            service.store.save_correction(code, "前向", "contract_name", "人工名称")
            endpoint = next(route.endpoint for route in app.routes
                            if getattr(route, "path", "") == "/api/projects/{project_code}"
                            and "DELETE" in getattr(route, "methods", set()))

            result = endpoint(code)

            self.assertEqual(result["status"], "项目已彻底删除")
            self.assertFalse(project.exists())
            self.assertFalse(detail.exists())
            self.assertFalse(cache.exists())
            self.assertIsNone(service.store.get_project(code))
            self.assertEqual(service.store.list_corrections(code), [])

    def test_single_project_root_with_multiple_backward_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "JSNJA2513970CGN00"
            (project / "前向").mkdir(parents=True)
            (project / "后向").mkdir()
            (project / "前向" / "主合同.pdf").touch()
            (project / "前向" / "附件.pdf").touch()
            (project / "后向" / "采购A.pdf").touch()
            (project / "后向" / "采购B.pdf").touch()
            found = scan_projects(project)[0]
            self.assertEqual(found.project_code, "JSNJA2513970CGN00")
            self.assertEqual(len(found.forward_pdfs), 2)
            self.assertEqual(len(found.backward_pdfs), 2)
            self.assertEqual(found.status, "可对比")

    def test_export_headers_are_chinese(self):
        self.assertEqual(_header_cn("risk_level"), "风险等级")
        self.assertEqual(_header_cn("time_plan.start_date"), "实际起算日期")
        self.assertEqual(_header_cn("time_plan.milestones.终验"), "时间节点-终验")
        self.assertEqual(_header_cn("direction"), "结构化合同方向")

    def test_equipment_shortage(self):
        f, b = self.contract("前向"), self.contract("后向")
        f.equipment = [EquipmentItem(standard_name="核心交换机", model="S6730", unit="台", quantity=2, evidence_id="F")]
        b.equipment = [EquipmentItem(standard_name="核心交换机", model="S6730", unit="台", quantity=1, evidence_id="B")]
        result = compare_equipment(f, b)[0]
        self.assertEqual(result.status, "数量不足")
        self.assertEqual(result.risk_level, "中风险")

    def test_schedule_late(self):
        f, b = self.contract("前向"), self.contract("后向")
        f.time_plan = TimePlan(90, "日", "收到开工令", start_date="2026-01-01", finish_date="2026-04-01")
        b.time_plan = TimePlan(100, "日", "收到开工令", start_date="2026-01-01", finish_date="2026-04-11")
        self.assertEqual(compare_schedule(f, b, 15)[0].status, "明确来不及")

    def test_schedule_missing_never_says_satisfied(self):
        result = compare_schedule(self.contract("前向"), self.contract("后向"))[0]
        self.assertEqual(result.status, "缺少起算依据")
        self.assertTrue(result.needs_review)

    def test_responsibility_weakened(self):
        f, b = self.contract("前向"), self.contract("后向")
        f.scopes = [ScopeItem("数据迁移", "负责完成", "全部历史数据", "全部", "", "前向", "", "F", .9)]
        b.scopes = [ScopeItem("数据迁移", "配合", "基础数据", "部分", "", "后向", "", "B", .9)]
        self.assertEqual(compare_scopes(f, b)[0].status, "责任程度弱化")


if __name__ == "__main__":
    unittest.main()
