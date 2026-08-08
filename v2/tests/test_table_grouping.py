from contract_extraction_v2.ocr.engine import OcrResultLine
from contract_extraction_v2.ocr.table_parser import build_table_rows, group_lines_by_y


def line(text: str, x1: int, x2: int) -> OcrResultLine:
    return OcrResultLine(text, 0.96, [[x1, 100], [x2, 100], [x2, 132], [x1, 132]])


def test_bbox_cells_recover_visual_row():
    lines = [
        line("服务器", 100, 210), line("H3C R4900", 300, 500),
        line("台", 620, 650), line("10", 720, 760),
    ]
    rows = group_lines_by_y(lines)
    assert len(rows) == 1
    assert [cell.text for cell in rows[0]] == ["服务器", "H3C R4900", "台", "10"]
    table = build_table_rows(lines)
    assert table[0]["cells"] == ["服务器", "H3C R4900", "台", "10"]
