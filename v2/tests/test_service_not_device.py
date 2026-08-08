import pytest

from contract_extraction_v2.inventory import is_service_content
from contract_extraction_v2.models import PageText
from contract_extraction_v2.structured import _extract_equipment


@pytest.mark.parametrize("name", [
    "软件部署实施", "硬件部署实施及系统集成", "系统集成", "安装调试", "培训", "技术服务",
])
def test_service_term_classifier(name):
    assert is_service_content(name)


def test_service_table_row_is_not_procurement_device():
    page = PageText(
        "报价表.pdf", "报价表.pdf", 1, "服务清单", "OCR bbox表格恢复", 0.96,
        table_rows=[{
            "名称": "软件部署实施", "数量": "1", "单位": "项",
            "cells": ["软件部署实施", "1", "项"], "confidence": 0.96,
            "source_engine": "ocr_boxes",
        }],
    )
    items = _extract_equipment("P001", "后向", [page], [])
    service = next(item for item in items if item.standard_name == "软件部署实施")
    assert service.category == "软件/服务"
    assert service.list_type == "实施服务内容"
