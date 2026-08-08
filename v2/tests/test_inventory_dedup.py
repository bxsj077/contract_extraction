from contract_extraction_v2.inventory import deduplicate_inventory_rows


def test_same_pdf_page_multi_ocr_row_is_deduplicated():
    base = {
        "清单类型": "采购交付清单", "名称": "核心交换机", "型号": "S6730",
        "数量": "2", "单位": "台", "来源文件": "合同.pdf", "页码": 12,
    }
    rows = [
        {**base, "confidence": 0.91, "source_engine": "native_table"},
        {**base, "confidence": 0.96, "source_engine": "ocr_boxes"},
    ]
    result = deduplicate_inventory_rows(rows)
    assert len(result) == 1
    assert result[0]["source_engine"] == "ocr_boxes"


def test_same_device_in_different_contracts_is_not_deduplicated():
    base = {"名称": "核心交换机", "型号": "S6730", "数量": "2", "单位": "台", "页码": 12}
    result = deduplicate_inventory_rows([
        {**base, "来源文件": "后向合同1.pdf"},
        {**base, "来源文件": "后向合同2.pdf"},
    ])
    assert len(result) == 2
