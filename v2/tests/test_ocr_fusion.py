from contract_extraction_v2.ocr.engine import OcrResultLine
from contract_extraction_v2.ocr.fusion import merge_ocr_results


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
