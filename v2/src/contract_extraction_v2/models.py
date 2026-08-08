from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class OcrLine:
    text: str
    score: float
    box: list[list[float]] | list[Any] = field(default_factory=list)
    engine: str = ""
    model: str = ""
    engine_version: str = ""
    dpi: int = 0
    preprocessing: str = ""
    ocr_pass: str = "normal"


@dataclass(slots=True)
class PageText:
    file_name: str
    file_path: str
    page: int
    text: str
    method: str
    confidence: float | None = None
    native_text: str = ""
    ocr_lines: list[OcrLine] = field(default_factory=list)
    signature_enhanced: bool = False
    page_type: str = "NORMAL"
    native_quality: dict[str, Any] = field(default_factory=dict)
    native_structure_quality: dict[str, Any] = field(default_factory=dict)
    table_regions: list[dict[str, Any]] = field(default_factory=list)
    table_rows: list[dict[str, Any]] = field(default_factory=list)
    ocr_pass: str = "native"
    retry_used: bool = False
    engine: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


@dataclass(slots=True)
class Evidence:
    contract_id: str
    field_name: str
    value: object
    source_file: str
    page: int | str
    quote: str
    clause: str = ""
    method: str = "规则抽取"
    ocr_confidence: float | str = ""
    field_confidence: str = "高"
    conflict: str = ""
    needs_review: str = "否"

    def to_row(self) -> dict[str, object]:
        return {
            "合同号": self.contract_id,
            "字段名": self.field_name,
            "提取值": self.value,
            "来源文件": self.source_file,
            "PDF页码": self.page,
            "原文证据": self.quote,
            "条款号或表格名称": self.clause,
            "提取方式": self.method,
            "OCR置信度": self.ocr_confidence,
            "字段置信度": self.field_confidence,
            "冲突说明": self.conflict,
            "是否需人工复核": self.needs_review,
        }


@dataclass(slots=True)
class ContractFiles:
    contract_id: str
    folder: Path
    pdfs: list[Path]
    fingerprint: str


@dataclass(slots=True)
class ContractOutput:
    result: dict[str, object]
    evidence: list[Evidence]
    pages: list[PageText]
    source_files: list[dict[str, object]]
    fingerprint: str
    errors: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, object]:
        return {
            "结构化结果": self.result,
            "字段证据": [item.to_row() for item in self.evidence],
            "页面OCR结果": [item.to_dict() for item in self.pages],
            "源文件清单": self.source_files,
            "源文件指纹": self.fingerprint,
            "失败信息": self.errors,
        }
