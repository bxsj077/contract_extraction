from contract_extraction_v2.ocr.quality import analyze_text_quality
from contract_extraction_v2.pdf_io import _v1_native_text_usable


def test_normal_contract_body_is_usable():
    text = "\n".join([
        "第一条 项目概况与合同范围",
        "乙方应按照合同约定完成设备安装、系统调试和验收工作。",
        "合同签订后双方应当按照项目计划履行各自义务。",
        "项目实施资料、竣工资料和验收报告应完整归档。",
    ] * 4)
    quality = analyze_text_quality(text)
    assert quality.content_usable
    assert quality.structure_usable


def test_too_short_text_is_not_usable():
    quality = analyze_text_quality("合同")
    assert not quality.content_usable


def test_insufficient_chinese_is_not_usable():
    quality = analyze_text_quality("A1B2C3 " * 30)
    assert not quality.content_usable


def test_replacement_garbage_is_not_usable():
    quality = analyze_text_quality(("合同�条款�" * 40))
    assert quality.replacement_ratio > 0.03
    assert not quality.content_usable


def test_fragmented_native_table_has_poor_structure():
    text = "\n".join([
        "服务器", "H3C", "R4900", "台", "10",
        "交换机", "H3C", "S6520", "台", "5",
    ] * 5)
    quality = analyze_text_quality(text, min_chars=20, min_cjk=2)
    assert quality.content_usable
    assert not quality.structure_usable
    assert quality.short_line_ratio > 0.6


def test_v1_usable_boundary_is_preserved_for_business_input():
    text = "合同双方应按约定履行设备交付、安装调试和验收义务。" * 10
    assert _v1_native_text_usable(text, 80, 20)
