import pytest

from contract_extraction_v2.inventory import is_service_content
from contract_extraction_v2.models import PageText
from contract_extraction_v2.structured import _extract_equipment


@pytest.mark.parametrize("name", [
    "软件部署实施", "硬件部署实施及系统集成", "系统集成", "安装调试", "培训", "技术服务",
])
def test_service_term_classifier(name):
    assert is_service_content(name)


def test_table_rows_do_not_bypass_v1_business_parser():
    page = PageText(
        "报价表.pdf", "报价表.pdf", 1, "服务清单", "OCR bbox表格恢复", 0.96,
        table_rows=[{
            "名称": "软件部署实施", "数量": "1", "单位": "项",
            "cells": ["软件部署实施", "1", "项"], "confidence": 0.96,
            "source_engine": "ocr_boxes",
        }],
    )
    items = _extract_equipment("P001", "后向", [page], [])
    assert items == []


def test_server_name_is_procurement_equipment_not_service():
    page = PageText(
        "报价表.pdf", "报价表.pdf", 1,
        "序号 采购内容 数量 单位 品牌 型号\n"
        "1 存储服务器 1 台 海康 DS-AS72024R",
        "原生文本层", 0.99,
    )
    items = _extract_equipment("P002", "后向", [page], [])
    server = next(item for item in items if item.standard_name == "存储服务器")
    assert server.category == "设备"
    assert server.model == "DS-AS72024R"
