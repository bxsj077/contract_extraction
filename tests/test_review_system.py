from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from contract_extraction.comparisons import compare_equipment, compare_schedule, compare_scopes
from contract_extraction.api import create_app
from contract_extraction.review_export import _header_cn, export_project_reviews, export_review
from contract_extraction.project_io import scan_projects
from contract_extraction.revenue_plan import compare_income_collection_plan, extract_plan_periods
from contract_extraction.structured import _extract_equipment
from contract_extraction.models import PageText
from contract_extraction.storage import ReviewStore
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

    def test_project_scan_detects_income_collection_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "P100"
            (project / "前向").mkdir(parents=True)
            (project / "后向").mkdir()
            (project / "收入收款计划").mkdir()
            (project / "前向" / "前向.pdf").touch()
            (project / "后向" / "后向.pdf").touch()
            (project / "收入收款计划" / "计划.xls").touch()
            found = scan_projects(project)[0]
            self.assertEqual(len(found.revenue_plan_files), 1)

    def test_service_resource_table_is_extracted_as_service_objects(self):
        text = "\n".join(["资源内容", "资源类型", "数量", "含税单价", "高性能型-1", "40", "11,154.15",
                           "高性能型-2", "服务器资源", "425", "15,913.74",
                           "生产汇聚交换机资", "44", "34,419.10"])
        page = PageText("合同.pdf", "合同.pdf", 1, text, "OCR 300DPI", confidence=.95)
        evidence = []
        items = _extract_equipment("P1", "前向", [page], evidence)
        self.assertEqual([(x.standard_name, x.quantity) for x in items],
                         [("服务器资源服务-高性能型-1", 40), ("服务器资源服务-高性能型-2", 425),
                          ("生产汇聚交换机资源", 44)])
        self.assertTrue(all(x.list_type == "维保/服务对象清单" for x in items))

    def test_maintenance_resource_and_multi_page_spare_list_are_extracted(self):
        page1 = PageText("后向合同.pdf", "后向合同.pdf", 8, "\n".join([
            "高性能型-1（Redis）", "CPU：2*16核", "服务器", "维护1年", "40",
            "高性能型-2（应用虚拟化）", "服务器", "维护1年", "425",
            "高性能型-3（中间件服务器）", "服务器", "维护1年", "245",
            "高性能型-4（Mysql服务器）", "服务器", "维护1年", "35",
            "大二层控制器资源", "设备", "维护1年", "1", "原厂质保", "4",
            "生产汇聚交换机资源", "设备", "维护1年", "44",
            "管理网/备份网汇聚交换机资源", "设备", "维护1年", "29",
            "管理网/备份网接入交换机资源", "设备", "维护1年", "13", "合计", "91",
        ]), "原生文本", confidence=1.0)
        page2 = PageText("后向合同.pdf", "后向合同.pdf", 9, "\n".join([
            "六、现场备件库清单", "备件类型", "备件参数", "数量", "单位",
            "CPU", "Xeon Silver 4216@2.1GHz×2", "3", "块",
            "内存", "MEMORY H HMA84GR7DJR4N-WM", "12", "块",
        ]), "原生文本", confidence=1.0)
        page3 = PageText("后向合同.pdf", "后向合同.pdf", 10, "\n".join([
            "网卡", "10GB Ethernet converged network adapter", "1", "块",
            "硬盘", "SSD PM1643a/45a", "6", "块",
        ]), "原生文本", confidence=1.0)
        page4 = PageText("后向合同.pdf", "后向合同.pdf", 11, "\n".join([
            "生产汇聚交换机整机", "生产汇聚交换机整机", "1", "台",
            "40G 光模块", "40G 光模块", "5", "块",
            "乙方需建立起不少于上述清单描述的应急备件库",
        ]), "原生文本", confidence=1.0)
        items = _extract_equipment("P1", "后向", [page1, page2, page3, page4], [])
        resources = [item for item in items if item.list_type == "维保/服务对象清单"]
        spares = [item for item in items if item.list_type == "现场备件库清单"]
        self.assertEqual(len(resources), 8)
        self.assertEqual(next(item.quantity for item in resources if item.standard_name == "大二层控制器资源"), 5)
        self.assertEqual(len(spares), 6)
        self.assertEqual(spares[0].model, "Xeon Silver 4216@2.1GHz×2")
        self.assertEqual(spares[-1].standard_name, "40G 光模块")

    def test_income_plan_period_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "计划.xls"
            path.write_text('''<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
<Worksheet ss:Name="收入计划"><Table>
<Row><Cell><Data ss:Type="String">起始日期</Data></Cell><Cell><Data ss:Type="String">终止日期</Data></Cell></Row>
<Row><Cell><Data ss:Type="String">2026-04-01</Data></Cell><Cell><Data ss:Type="String">2027-03-31</Data></Cell></Row>
</Table></Worksheet></Workbook>''', encoding="utf-8")
            periods = extract_plan_periods(path)
            self.assertEqual(periods[0]["start_date"], "2026-04-01")
            contract = self.contract("前向")
            contract.time_plan = TimePlan(12, "个月", "固定日期区间", start_date="2026-04-16", finish_date="2027-04-15")
            result = compare_income_collection_plan(contract, [str(path)])[0]
            self.assertEqual(result.status, "基本一致，需复核偏差")
            self.assertIn("-15天", result.description)

    def test_export_headers_are_chinese(self):
        self.assertEqual(_header_cn("risk_level"), "风险等级")
        self.assertEqual(_header_cn("time_plan.start_date"), "实际起算日期")
        self.assertEqual(_header_cn("time_plan.milestones.终验"), "时间节点-终验")
        self.assertEqual(_header_cn("direction"), "结构化合同方向")

    def test_export_merges_duration_and_creates_one_workbook_per_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ReviewStore(root / "review.db")
            payloads = [
                ("P001", {"duration_value": 12, "duration_unit": "个月", "duration_conclusion": "12个月"}),
                ("P002", {"duration_value": None, "duration_unit": "", "duration_conclusion": "按招标文件及投标文件执行"}),
            ]
            for code, plan in payloads:
                payload = {
                    "project_code": code, "status": "已完成", "risk_level": "低风险",
                    "forward": {"direction": "前向", "contract_number": code, "contract_name": f"{code}合同",
                                "time_plan": plan, "parse_metadata": {}},
                    "backward_contracts": [], "equipment_differences": [], "schedule_differences": [],
                    "plan_differences": [], "scope_differences": [], "review_issues": [],
                }
                store.upsert_project(code, str(root / code), "已完成", "低风险", payload)

            full_path = export_review(store, root / "全量.xlsx")
            project_paths = export_project_reviews(store, root / "分项目审查结果", "20260805_120000")

            full_wb = load_workbook(full_path, read_only=True, data_only=True)
            rows = list(full_wb["合同解析结果"].values)
            self.assertIn("工期", rows[0])
            self.assertNotIn("工期数值", rows[0])
            self.assertNotIn("工期单位", rows[0])
            duration_col = rows[0].index("工期")
            self.assertEqual([row[duration_col] for row in rows[1:]], ["12个月", "按招标文件及投标文件执行"])
            full_wb.close()
            self.assertEqual(len(project_paths), 2)
            for path in project_paths:
                wb = load_workbook(path, read_only=True, data_only=True)
                self.assertEqual(wb["项目审查汇总"].max_row, 2)
                self.assertEqual(wb["合同解析结果"].max_row, 2)
                wb.close()

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
