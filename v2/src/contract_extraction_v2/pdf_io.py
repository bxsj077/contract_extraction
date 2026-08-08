from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

from PIL import Image
from pypdf import PdfReader

from .models import ContractFiles, OcrLine, PageText
from .ocr.cache import OcrCacheV2, cache_key
from .ocr.engine import OcrResultLine, PageOcrResult, RapidOcrEngine
from .ocr.fusion import merge_native_and_ocr_text, merge_ocr_results
from .ocr.page_classifier import PageType, classify_page
from .ocr.ppstructure import PPStructureV3Adapter
from .ocr.preprocess import preprocess_by_name, preprocess_signature
from .ocr.quality import analyze_text_quality, assess_ocr_quality
from .ocr.table_parser import build_table_rows, group_lines_by_y


OUTPUT_NAMES = {
    "_合同提取结果", "output", "outputs", "ocr_cache", "text_corpus",
    "candidate_reports", "data_v2",
}
SIGNATURE_HINT = re.compile(
    r"签订日期|签署日期|签章日期|签字盖章|以下无正文|合同签署页|法定代表人|授权代表|甲方.?盖章|乙方.?盖章"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def contract_fingerprint(folder: Path, pdfs: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for pdf in sorted(pdfs):
        stat = pdf.stat()
        digest.update(str(pdf.relative_to(folder)).encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(sha256_file(pdf).encode("ascii"))
    return digest.hexdigest()


def discover_contracts(root: Path, wanted: set[str] | None = None) -> list[ContractFiles]:
    items: list[ContractFiles] = []
    for folder in sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name):
        if folder.name.startswith((".", "_")) or folder.name in OUTPUT_NAMES:
            continue
        if wanted and folder.name not in wanted:
            continue
        pdfs = sorted(path for path in folder.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")
        if pdfs:
            items.append(ContractFiles(folder.name, folder, pdfs, contract_fingerprint(folder, pdfs)))
    return items


def text_quality(text: str, min_chars: int = 80, min_cjk: int = 20) -> dict[str, object]:
    """Backwards-compatible facade with V2 content and structure metrics."""

    quality = analyze_text_quality(text, min_chars, min_cjk)
    data = quality.to_dict()
    data.update({
        "chars": quality.total_chars,
        "cjk": quality.cjk_chars,
        "replacement": quality.replacement_chars,
        "usable": _v1_native_text_usable(text, min_chars, min_cjk),
    })
    return data


def _v1_native_text_usable(text: str, min_chars: int, min_cjk: int) -> bool:
    """Preserve the exact V1 decision boundary for business-input selection."""

    compact = re.sub(r"\s+", "", text or "")
    cjk = len(re.findall(r"[\u3400-\u9fff]", compact))
    replacement = compact.count("�")
    return (
        len(compact) >= min_chars
        and cjk >= min_cjk
        and replacement <= max(2, len(compact) * 0.01)
    )


def _ocr_visual_text(lines: list[OcrResultLine], page_type: PageType) -> str:
    """Improve OCR reading order without introducing business-field mapping."""

    if page_type not in {PageType.TABLE, PageType.MIXED}:
        return "\n".join(line.text for line in lines)
    rows = group_lines_by_y(lines)
    positioned = {id(cell) for row in rows for cell in row}
    visual = [" ".join(cell.text for cell in row if cell.text.strip()) for row in rows]
    visual.extend(line.text for line in lines if id(line) not in positioned and line.text.strip())
    return "\n".join(line for line in visual if line.strip())


class LocalOcr:
    """Compatibility facade used by the unchanged V2 business pipeline."""

    def __init__(self, engine: RapidOcrEngine | None = None) -> None:
        self.adapter = engine or RapidOcrEngine()

    @property
    def engine(self) -> Any:
        return self.adapter._get_engine()  # compatibility for diagnostics only

    def recognize(
        self,
        image: Image.Image,
        *,
        dpi: int = 300,
        preprocessing: str = "normal",
        ocr_pass: str = "normal",
    ) -> tuple[list[OcrResultLine], float]:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as stream:
            temp_path = Path(stream.name)
        try:
            image.save(temp_path)
            lines = self.adapter.recognize(
                str(temp_path), dpi=dpi, preprocessing=preprocessing, ocr_pass=ocr_pass,
            )
        finally:
            temp_path.unlink(missing_ok=True)
        mean = sum(line.score for line in lines) / len(lines) if lines else 0.0
        return lines, mean


def render_page(pdf: Path, page_index: int, dpi: int) -> Image.Image:
    import fitz

    document = fitz.open(pdf)
    try:
        page = document.load_page(page_index)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    finally:
        document.close()


def suppress_red_seal(image: Image.Image) -> Image.Image:
    """Compatibility alias for the V2 signature preprocessing pipeline."""

    return preprocess_signature(image)


def _line_to_model(line: OcrResultLine) -> OcrLine:
    return OcrLine(
        text=line.text, score=line.score, box=line.box, engine=line.engine,
        model=line.model, engine_version=line.engine_version, dpi=line.dpi,
        preprocessing=line.preprocessing, ocr_pass=line.ocr_pass,
    )


def _cached_ocr_v2(
    pdf: Path,
    pdf_sha256: str,
    page_index: int,
    dpi: int,
    cache: OcrCacheV2,
    ocr: LocalOcr,
    *,
    preprocessing: str,
    ocr_pass: str,
    enhanced: bool = False,
    force: bool = False,
) -> tuple[list[OcrResultLine], float, dict[str, Any]]:
    adapter = ocr.adapter
    key = cache_key(
        pdf_sha256=pdf_sha256, page=page_index + 1, engine=adapter.engine_name,
        engine_version=adapter.engine_version, model=adapter.model_name, dpi=dpi,
        preprocess=preprocessing, ocr_pass=ocr_pass, enhanced=enhanced,
    )
    cached = None if force else cache.load(key)
    if cached:
        lines, metadata = cached
        return lines, float(metadata.get("mean_confidence", 0.0)), {**metadata, "cache_hit": True}

    image = render_page(pdf, page_index, dpi)
    processed = preprocess_by_name(image, preprocessing)
    lines, mean = ocr.recognize(
        processed, dpi=dpi, preprocessing=preprocessing, ocr_pass=ocr_pass,
    )
    metadata = {
        "pdf": str(pdf), "pdf_sha256": pdf_sha256, "page": page_index + 1,
        "engine": adapter.engine_name, "model": adapter.model_name,
        "engine_version": adapter.engine_version, "dpi": dpi, "pass": ocr_pass,
        "preprocess": preprocessing, "enhanced": enhanced,
        "mean_confidence": round(mean, 6), "cache_hit": False,
    }
    cache.save(key, lines, metadata)
    return lines, mean, metadata


def _select_better(
    first: list[OcrResultLine],
    second: list[OcrResultLine],
    *,
    page_type: str,
) -> tuple[list[OcrResultLine], str, dict[str, Any]]:
    first_rows = build_table_rows(first) if page_type in {PageType.TABLE.value, PageType.MIXED.value} else []
    second_rows = build_table_rows(second) if page_type in {PageType.TABLE.value, PageType.MIXED.value} else []
    first_quality = assess_ocr_quality(first, page_type=page_type, table_rows=first_rows)
    second_quality = assess_ocr_quality(second, page_type=page_type, table_rows=second_rows)
    if second_quality.score > first_quality.score:
        return second, "retry", {
            "first_quality": first_quality.to_dict(), "retry_quality": second_quality.to_dict(),
            "selection": "retry",
        }
    return first, "normal", {
        "first_quality": first_quality.to_dict(), "retry_quality": second_quality.to_dict(),
        "selection": "normal",
    }


def _table_rows(
    lines: list[OcrResultLine],
    image: Image.Image | None,
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    table_engine = str(settings.get("table_engine", "auto")).lower()
    if table_engine == "off":
        return [], "off", {"reason": "table_engine=off"}
    enable_pp = bool(settings.get("enable_ppstructure", False))
    if table_engine == "ppstructure" or (table_engine == "auto" and enable_pp):
        adapter = PPStructureV3Adapter()
        if image is not None:
            pp_rows, diagnostics = adapter.parse(image)
            if pp_rows:
                return pp_rows, "ppstructure", diagnostics
            fallback = build_table_rows(lines, source_engine="ocr_boxes")
            return fallback, "ocr_boxes", diagnostics
    return build_table_rows(lines, source_engine="ocr_boxes"), "ocr_boxes", {}


def _page_result_to_model(pdf: Path, result: PageOcrResult, methods: list[str]) -> PageText:
    diagnostics = dict(result.diagnostics)
    diagnostics.update({
        "page_type": result.page_type,
        "ocr_used": bool(result.ocr_lines),
        "ocr_dpi": max((line.dpi for line in result.ocr_lines), default=0),
        "retry_used": result.retry_used,
        "mean_confidence": result.mean_confidence,
        "signature_enhanced": result.signature_enhanced,
        "table_detected": result.page_type in {PageType.TABLE.value, PageType.MIXED.value},
        "table_rows": len(result.table_rows),
    })
    return PageText(
        file_name=pdf.name, file_path=str(pdf), page=result.page,
        text=result.selected_text.strip(), native_text=result.native_text,
        method="+".join(methods) or "无可用文本", confidence=result.mean_confidence,
        ocr_lines=[_line_to_model(line) for line in result.ocr_lines],
        signature_enhanced=result.signature_enhanced, page_type=result.page_type,
        native_quality=dict(diagnostics.get("native_quality", {})),
        native_structure_quality=dict(diagnostics.get("native_structure_quality", {})),
        table_regions=result.table_regions, table_rows=result.table_rows,
        ocr_pass=result.ocr_pass, retry_used=result.retry_used,
        engine=result.engine, diagnostics=diagnostics,
    )


def extract_pdf_pages(
    pdf: Path,
    contract_id: str,
    cache_root: Path,
    ocr: LocalOcr,
    ocr_dpi: int = 300,
    signature_dpi: int = 600,
    min_chars: int = 80,
    min_cjk: int = 20,
    force: bool = False,
    ocr_config: dict[str, Any] | None = None,
) -> tuple[list[PageText], dict[str, object], list[str]]:
    settings: dict[str, Any] = {
        "normal_dpi": ocr_dpi,
        "retry_dpi": 450,
        "signature_dpi": signature_dpi,
        "retry_mean_confidence": 0.90,
        "retry_key_field_confidence": 0.88,
        "table_engine": "auto",
        "enable_table_detection": True,
        "enable_smart_retry": True,
        "enable_signature_fusion": True,
        "enable_ppstructure": False,
    }
    settings.update(ocr_config or {})
    normal_dpi = int(settings["normal_dpi"])
    retry_dpi = int(settings["retry_dpi"])
    signature_dpi = int(settings["signature_dpi"])
    cache = OcrCacheV2(cache_root)
    pages: list[PageText] = []
    errors: list[str] = []
    file_hash = sha256_file(pdf)
    reader = PdfReader(str(pdf), strict=False)
    native_pages: list[str] = []
    for page_no, page in enumerate(reader.pages, 1):
        try:
            native_pages.append(page.extract_text() or "")
        except Exception as exc:
            native_pages.append("")
            errors.append(f"{pdf.name} 第{page_no}页文本层读取失败：{exc}")

    for page_index, native in enumerate(native_pages):
        page_no = page_index + 1
        quality = analyze_text_quality(native, min_chars, min_cjk)
        native_v1_usable = _v1_native_text_usable(native, min_chars, min_cjk)
        classification = classify_page(native, native_quality=quality)
        page_type = classification.page_type
        # Keep V1's final-page signature coverage while fusing instead of
        # appending duplicate native/normal/enhanced OCR text.
        signature_candidate = bool(SIGNATURE_HINT.search(native)) or page_no >= max(1, len(native_pages) - 2)
        if signature_candidate and page_type == PageType.NORMAL:
            page_type = PageType.SIGNATURE
        methods: list[str] = []
        lines: list[OcrResultLine] = []
        selected_text = native.strip()
        retry_used = False
        signature_enhanced = False
        ocr_pass = "native"
        table_rows: list[dict[str, Any]] = []
        table_engine = "off"
        mean: float | None = None
        diagnostics: dict[str, Any] = {
            "native_quality": quality.to_dict(),
            "native_structure_quality": {
                "structure_usable": quality.structure_usable,
                "short_line_ratio": quality.short_line_ratio,
                "single_char_line_ratio": quality.single_char_line_ratio,
                "digit_fragmentation_ratio": quality.digit_fragmentation_ratio,
                "model_fragmentation_ratio": quality.model_fragmentation_ratio,
            },
            "classification": {
                "type": page_type.value, "table_score": classification.table_score,
                "signature_score": classification.signature_score,
                "reasons": classification.reasons,
            },
            "v1_native_text_usable": native_v1_usable,
        }
        try:
            needs_ocr = (
                not native_v1_usable
                or (bool(settings.get("enable_table_detection", True)) and page_type in {PageType.TABLE, PageType.MIXED})
                or page_type in {PageType.SIGNATURE, PageType.MIXED}
            )
            if native_v1_usable:
                methods.append("原生文本层")
            if not needs_ocr:
                selected_text = native.strip()
            else:
                first, first_mean, first_meta = _cached_ocr_v2(
                    pdf, file_hash, page_index, normal_dpi, cache, ocr,
                    preprocessing="normal", ocr_pass="normal", force=force,
                )
                lines, mean, ocr_pass = first, first_mean, "normal"
                methods.append(f"RapidOCR PP-OCRv6 {normal_dpi}DPI")
                # Coordinates can reveal a table even when native text was too
                # fragmented to provide enough header evidence.
                reclassified = classify_page(native, first, quality)
                if page_type == PageType.NORMAL and reclassified.page_type in {PageType.TABLE, PageType.MIXED}:
                    page_type = reclassified.page_type
                    diagnostics["classification_after_ocr"] = {
                        "type": page_type.value, "table_score": reclassified.table_score,
                        "signature_score": reclassified.signature_score,
                        "reasons": reclassified.reasons,
                    }
                preliminary_rows = build_table_rows(first) if page_type in {PageType.TABLE, PageType.MIXED} else []
                first_quality = assess_ocr_quality(
                    first, page_type=page_type.value, table_rows=preliminary_rows,
                    retry_mean_confidence=float(settings["retry_mean_confidence"]),
                    retry_key_field_confidence=float(settings["retry_key_field_confidence"]),
                )
                diagnostics["first_pass"] = {**first_meta, "quality": first_quality.to_dict()}
                if bool(settings.get("enable_smart_retry", True)) and first_quality.retry_required and page_type not in {PageType.SIGNATURE}:
                    retry, _, retry_meta = _cached_ocr_v2(
                        pdf, file_hash, page_index, retry_dpi, cache, ocr,
                        preprocessing="retry", ocr_pass="retry", force=force,
                    )
                    lines, ocr_pass, selection = _select_better(first, retry, page_type=page_type.value)
                    mean = sum(line.score for line in lines) / len(lines) if lines else 0.0
                    retry_used = True
                    methods.append(f"智能二次OCR {retry_dpi}DPI")
                    diagnostics["retry_reason"] = first_quality.retry_reasons
                    diagnostics["retry_pass"] = {**retry_meta, **selection}

                if page_type in {PageType.SIGNATURE, PageType.MIXED}:
                    enhanced, _, enhanced_meta = _cached_ocr_v2(
                        pdf, file_hash, page_index, signature_dpi, cache, ocr,
                        preprocessing="signature", ocr_pass="signature", enhanced=True, force=force,
                    )
                    if bool(settings.get("enable_signature_fusion", True)):
                        lines = merge_ocr_results(lines, enhanced)
                    else:
                        enhanced_quality = assess_ocr_quality(enhanced, page_type=page_type.value)
                        normal_quality = assess_ocr_quality(lines, page_type=page_type.value)
                        if enhanced_quality.score > normal_quality.score:
                            lines = enhanced
                    mean = sum(line.score for line in lines) / len(lines) if lines else 0.0
                    signature_enhanced = True
                    ocr_pass = "signature_fusion"
                    methods.append(f"签章增强融合 {signature_dpi}DPI")
                    diagnostics["signature_pass"] = enhanced_meta

                if page_type in {PageType.TABLE, PageType.MIXED}:
                    table_image = render_page(pdf, page_index, normal_dpi)
                    table_rows, table_engine, table_diag = _table_rows(lines, table_image, settings)
                    diagnostics["table_engine"] = table_engine
                    diagnostics["table_diagnostics"] = table_diag
                if page_type in {PageType.SIGNATURE, PageType.MIXED}:
                    selected_text = merge_native_and_ocr_text(native, lines)
                elif native_v1_usable:
                    # Critical compatibility rule: OCR/table diagnostics may be
                    # richer, but V1-usable native text remains the sole
                    # business parser input.
                    selected_text = native.strip()
                elif lines:
                    selected_text = (
                        merge_native_and_ocr_text(native, lines)
                        if native.strip()
                        else _ocr_visual_text(lines, page_type)
                    )
                elif native.strip():
                    selected_text = native.strip()
                    methods.append("原生文本层回退")
        except Exception as exc:
            errors.append(f"{pdf.name} 第{page_no}页OCR失败：{exc}")
            if native.strip():
                selected_text = native.strip()
                methods.append("原生文本层回退")

        result = PageOcrResult(
            page=page_no, page_type=page_type.value, native_text=native,
            ocr_lines=lines, mean_confidence=mean, selected_text=selected_text,
            table_rows=table_rows, ocr_pass=ocr_pass, retry_used=retry_used,
            signature_enhanced=signature_enhanced,
            engine=ocr.adapter.engine_name if lines else "native",
            diagnostics=diagnostics,
        )
        pages.append(_page_result_to_model(pdf, result, methods))

    source = {
        "文件名": pdf.name, "路径": str(pdf), "页数": len(native_pages),
        "大小": pdf.stat().st_size, "修改时间": pdf.stat().st_mtime,
        "SHA256": file_hash, "OCR版本": "V2", "OCR引擎": "RapidOCR / PP-OCRv6",
    }
    return pages, source, errors
