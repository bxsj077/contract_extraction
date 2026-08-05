from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from contract_extraction.api import _normalize_duration_correction
from contract_extraction.models import PageText
from contract_extraction.review_service import ReviewService
from contract_extraction.structured import _extract_time_plan
from contract_extraction.system_models import ContractStructured


class CorrectionTests(unittest.TestCase):
    def test_manual_duration_text_becomes_conclusion_not_number(self):
        value, conclusion = _normalize_duration_correction("供货要求等合同文件另有约定")
        self.assertIsNone(value)
        self.assertEqual(conclusion, "未明确：供货要求等合同文件另有约定")

    def test_manual_duration_digits_remain_numeric(self):
        value, conclusion = _normalize_duration_correction("120")
        self.assertEqual(value, 120)
        self.assertEqual(conclusion, "")

    def test_external_reference_duration_is_reported(self):
        page = PageText("合同.pdf", "合同.pdf", 3, "履行时间(期限):按招标文件及投标文件执行。", "原生文本层")
        plan = _extract_time_plan("P1", "前向", [page], {}, [])
        self.assertIsNone(plan.duration_value)
        self.assertIn("按招标文件及投标文件执行", plan.duration_conclusion)
        self.assertIn("需查阅", plan.calculation_status)

    def test_other_contract_terms_duration_is_not_numeric(self):
        page = PageText("合同.pdf", "合同.pdf", 4, "工期：供货要求等合同文件另有约定。", "原生文本层")
        plan = _extract_time_plan("P1", "前向", [page], {}, [])
        self.assertIsNone(plan.duration_value)
        self.assertIn("供货要求等合同文件另有约定", plan.duration_conclusion)
        self.assertIn("需查阅", plan.calculation_status)

    def test_bracketed_numeric_duration_is_numeric(self):
        page = PageText("合同.pdf", "合同.pdf", 3, "工期：本合同生效后[120]天。", "原生文本层")
        plan = _extract_time_plan("P1", "后向", [page], {}, [])
        self.assertEqual(plan.duration_value, 120)
        self.assertEqual(plan.duration_unit, "天")
        self.assertEqual(plan.duration_conclusion, "120天")

    def test_fixed_service_period_has_exact_start_and_finish(self):
        page = PageText("合同.pdf", "合同.pdf", 2,
                        "1.3服务期为2026年4月16日至2027年4月15日，共计12个月。", "OCR 300DPI")
        plan = _extract_time_plan("P1", "前向", [page], {}, [])
        self.assertEqual(plan.duration_value, 12)
        self.assertEqual(plan.duration_unit, "个月")
        self.assertEqual(plan.start_date, "2026-04-16")
        self.assertEqual(plan.finish_date, "2027-04-15")
        self.assertEqual(plan.start_condition_type, "固定日期区间")

    def test_manual_duration_correction_is_persistent_and_recalculates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = ReviewService(root / "contracts", root / "output")
            service.store.save_correction("P1", "前向", "time_plan.duration_value", 30, "人工核对")
            service.store.save_correction("P1", "前向", "time_plan.duration_unit", "日", "人工核对")
            service.store.save_correction("P1", "前向", "time_plan.start_date", "2026-01-01", "人工核对")
            contract = ContractStructured("P1", "前向")
            corrected = service._apply_corrections(contract, "前向")
            self.assertEqual(corrected.time_plan.duration_value, 30)
            self.assertEqual(corrected.time_plan.finish_date, "2026-01-31")
            self.assertEqual(corrected.parse_metadata["correction_count"], 3)
            self.assertEqual(len(service.store.list_corrections("P1")), 3)


if __name__ == "__main__":
    unittest.main()
