from __future__ import annotations

import unittest

from contract_extraction_v2.date_utils import calculate_end_date, extract_signing_dates
from contract_extraction_v2.models import PageText
from contract_extraction_v2.rules import analyze_contract, classify


def page(text: str, number: int = 1, confidence: float | None = None, enhanced: bool = False) -> PageText:
    return PageText("合同.pdf", "合同.pdf", number, text, "签章增强OCR 600DPI" if enhanced else "原生文本层",
                    confidence, text, [], enhanced)


class RuleTests(unittest.TestCase):
    def test_classification(self):
        kind, _, _, _ = classify([page("本项目为系统集成及项目实施，包含设备采购、安装调试和部署上线。")])
        self.assertEqual(kind, "集成实施类")
        kind, _, _, _ = classify([page("提供三年运行维护和驻场运维服务，负责巡检及故障处理。")])
        self.assertEqual(kind, "运维类")

    def test_stamp_date_requires_review(self):
        dates = extract_signing_dates([page("乙方（盖章） 签订日期：2026年5月29日", 10, 0.72, True)])
        self.assertEqual(dates[0].value.isoformat(), "2026-05-29")
        self.assertEqual(dates[0].party, "乙方")
        self.assertTrue(dates[0].needs_review)

    def test_partial_sign_date_can_drive_start(self):
        pages = [page("系统集成项目实施。合同工期为30日，自合同签订之日起开始。"),
                 page("乙方（盖章） 签订日期：2026年5月29日", 9, 0.72, True)]
        config = {"classification_margin": 2, "ocr_review_threshold": 0.9,
                  "signing_date_policy": "latest_recognized", "max_summary_chars": 800}
        result = analyze_contract("HT001", "HT001", pages, [{"文件名": "合同.pdf"}], "fingerprint", config, []).result
        self.assertEqual(result["乙方签约日期"], "2026-05-29")
        self.assertEqual(result["工期起算具体日期"], "2026-05-29")
        self.assertEqual(result["签约日期需人工确认"], "是")
        self.assertIn("不等同于三方全部盖章生效日", result["签约日期识别说明"])

    def test_operations_stops_deep_extraction(self):
        result = analyze_contract("HT002", "HT002", [page("三年运行维护及驻场运维服务。")],
                                  [{"文件名": "合同.pdf"}], "fp", {"classification_margin": 2}, []).result
        self.assertEqual(result["合同性质"], "运维类")
        self.assertEqual(result["工期原文"], "")

    def test_contract_name_falls_back_to_standalone_cover_title(self):
        cover = page(
            "江苏中烟工业有限责任公司\n"
            "安全子领域数字化转型一期建设项目主合同\n"
            "合同签订地点：江苏省南京市\n"
            "买方：江苏中烟工业有限责任公司\n"
            "卖方：中电鸿信信息科技有限公司\n"
            "本项目包含系统集成、设备采购和安装调试。"
        )
        result = analyze_contract(
            "HT003", "HT003", [cover], [{"文件名": "主合同.pdf"}], "fp",
            {"classification_margin": 2, "max_summary_chars": 800}, [],
        ).result
        self.assertEqual(result["合同名称"], "安全子领域数字化转型一期建设项目主合同")

    def test_calendar_days(self):
        from datetime import date
        end, note = calculate_end_date(date(2026, 5, 29), 30, "日")
        self.assertEqual(end.isoformat(), "2026-06-28")
        self.assertEqual(note, "")


if __name__ == "__main__":
    unittest.main()
