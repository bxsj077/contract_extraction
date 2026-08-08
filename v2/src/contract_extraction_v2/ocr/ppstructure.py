from __future__ import annotations

from html.parser import HTMLParser
from typing import Any


class PPStructureV3Adapter:
    """Optional PaddleOCR PP-StructureV3 adapter with non-fatal fallback."""

    def __init__(self) -> None:
        self._pipeline: Any | None = None

    def available(self) -> bool:
        try:
            from paddleocr import PPStructureV3  # noqa: F401
            return True
        except (ImportError, RuntimeError):
            return False

    def _get_pipeline(self) -> Any:
        if self._pipeline is None:
            from paddleocr import PPStructureV3
            self._pipeline = PPStructureV3(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        return self._pipeline

    def parse(self, image: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        try:
            if hasattr(image, "mode"):
                import numpy as np
                image = np.asarray(image)
            output = self._get_pipeline().predict(input=image)
            rows: list[dict[str, Any]] = []
            raw: list[Any] = []
            for result in output:
                data = getattr(result, "json", None)
                if callable(data):
                    data = data()
                raw.append(data if data is not None else str(result))
                rows.extend(_rows_from_result(data))
            return rows, {"available": True, "result_count": len(raw), "raw": raw}
        except ImportError as exc:
            return [], {"available": False, "fallback": "ocr_boxes", "reason": str(exc)}
        except RuntimeError as exc:
            return [], {"available": True, "fallback": "ocr_boxes", "reason": str(exc)}


def _rows_from_result(value: Any) -> list[dict[str, Any]]:
    """Conservatively expose table rows from currently supported JSON shapes."""

    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"table_rows", "rows"} and isinstance(item, list):
                for row in item:
                    if isinstance(row, dict):
                        rows.append({**row, "source_engine": "ppstructure"})
                    elif isinstance(row, list):
                        rows.append({"cells": [str(cell) for cell in row], "source_engine": "ppstructure"})
            elif isinstance(item, str) and "html" in key.lower() and "<tr" in item.lower():
                rows.extend(_rows_from_html(item))
            else:
                rows.extend(_rows_from_result(item))
    elif isinstance(value, list):
        for item in value:
            rows.extend(_rows_from_result(item))
    return rows


class _TableHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self.current_row = []
        elif tag.lower() in {"td", "th"} and self.current_row is not None:
            self.current_cell = []

    def handle_data(self, data: str) -> None:
        if self.current_cell is not None:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self.current_row is not None and self.current_cell is not None:
            self.current_row.append(" ".join("".join(self.current_cell).split()))
            self.current_cell = None
        elif tag.lower() == "tr" and self.current_row is not None:
            if any(self.current_row):
                self.rows.append(self.current_row)
            self.current_row = None


def _rows_from_html(value: str) -> list[dict[str, Any]]:
    parser = _TableHtmlParser()
    parser.feed(value)
    return [{"cells": row, "source_engine": "ppstructure"} for row in parser.rows]
