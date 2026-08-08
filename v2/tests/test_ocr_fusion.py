from contract_extraction_v2.ocr.engine import OcrResultLine
from contract_extraction_v2.ocr.fusion import merge_native_and_ocr_text, merge_ocr_results


def _line(score: float, ocr_pass: str) -> OcrResultLine:
    return OcrResultLine(
        "签订日期：2026年3月1日", score,
        [[100, 100], [500, 100], [500, 140], [100, 140]],
        "RapidOCR", "PP-OCRv6", "3.9.2", 600, ocr_pass, ocr_pass,
    )


def test_signature_duplicate_kept_once():
    merged = merge_ocr_results([_line(0.91, "normal")], [_line(0.97, "signature")])
    assert [line.text for line in merged] == ["签订日期：2026年3月1日"]
    assert merged[0].score == 0.97


def test_native_clause_keeps_v1_placeholder_and_ocr_adds_only_new_text():
    native = "1.5 工期：本合同生效后[ 150 ]天。"
    ocr = OcrResultLine(
        "1.5 工期：本合同生效后150天。", 0.98,
        [[100, 100], [500, 100], [500, 140], [100, 140]],
        "RapidOCR", "PP-OCRv6", "3.9.2", 300, "normal", "normal",
    )
    merged = merge_native_and_ocr_text(native, [ocr])
    assert merged.splitlines()[0] == native
    assert merged.count("工期") == 1
