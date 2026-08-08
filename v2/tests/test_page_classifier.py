from contract_extraction_v2.ocr.page_classifier import PageType, classify_page


def test_normal_body_page():
    text = "本合同约定双方权利义务，乙方按照甲方要求完成项目建设。" * 8
    assert classify_page(text).page_type == PageType.NORMAL


def test_equipment_table_page():
    text = "设备清单\n序号 设备名称 品牌 规格型号 数量 单位\n1 核心交换机 华为 S6730 2 台"
    assert classify_page(text).page_type == PageType.TABLE


def test_price_table_page():
    text = "报价表\n序号 产品名称 品牌 型号 数量 单位 单价 总价\n1 服务器 H3C R4900 10 台 100 1000"
    assert classify_page(text).page_type == PageType.TABLE


def test_signature_page():
    text = "甲方（盖章）：某公司\n乙方（盖章）：某公司\n法定代表人：张三\n签订日期：2026年3月1日"
    assert classify_page(text).page_type == PageType.SIGNATURE
