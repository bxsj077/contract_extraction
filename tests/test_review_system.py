from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contract_extraction.comparisons import compare_equipment, compare_schedule, compare_scopes
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
