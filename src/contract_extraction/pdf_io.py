from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageEnhance, ImageOps
from pypdf import PdfReader

from .models import ContractFiles, OcrLine, PageText


OUTPUT_NAMES = {"_合同提取结果", "output", "outputs", "ocr_cache", "text_corpus", "candidate_reports"}
SIGNATURE_HINT = re.compile(r"签订日期|签署日期|签章日期|签字盖章|以下无正文|合同签署页|法定代表人|授权代表")


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
    for folder in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name):
        if folder.name.startswith((".", "_")) or folder.name in OUTPUT_NAMES:
            continue
        if wanted and folder.name not in wanted:
            continue
        pdfs = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
        if pdfs:
            items.append(ContractFiles(folder.name, folder, pdfs, contract_fingerprint(folder, pdfs)))
    return items


def text_quality(text: str, min_chars: int = 80, min_cjk: int = 20) -> dict[str, object]:
    compact = re.sub(r"\s+", "", text or "")
    cjk = len(re.findall(r"[\u3400-\u9fff]", compact))
    replacement = compact.count("�")
    usable = len(compact) >= min_chars and cjk >= min_cjk and replacement <= max(2, len(compact) * 0.01)
    return {"chars": len(compact), "cjk": cjk, "cjk_ratio": round(cjk / len(compact), 4) if compact else 0,
            "replacement": replacement, "usable": usable}


class LocalOcr:
    def __init__(self) -> None:
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR
            self._engine = RapidOCR()
        return self._engine

    def recognize(self, image: Image.Image) -> tuple[list[OcrLine], float]:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            temp_path = Path(tmp.name)
        try:
            image.save(temp_path)
            result, _ = self.engine(str(temp_path))
        finally:
            temp_path.unlink(missing_ok=True)
        lines = [OcrLine(text=str(item[1]), score=float(item[2]), box=item[0]) for item in (result or [])]
        return lines, (sum(line.score for line in lines) / len(lines) if lines else 0.0)


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
    """Use the red channel to weaken red seals while retaining dark handwriting."""
    channel = ImageOps.autocontrast(image.convert("RGB").getchannel("R"))
    channel = ImageEnhance.Contrast(channel).enhance(2.2)
    channel = ImageEnhance.Sharpness(channel).enhance(1.8)
    return channel.convert("RGB")


def _load_cache(path: Path) -> dict[str, object] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def _cached_ocr(pdf: Path, page_index: int, dpi: int, cache_path: Path, ocr: LocalOcr,
                enhanced: bool = False, force: bool = False) -> tuple[list[OcrLine], float]:
    cached = None if force else _load_cache(cache_path)
    if cached and cached.get("dpi") == dpi and bool(cached.get("enhanced")) == enhanced:
        lines = [OcrLine(**item) for item in cached.get("lines", [])]
        return lines, float(cached.get("mean_confidence", 0.0))
    image = render_page(pdf, page_index, dpi)
    lines, mean = ocr.recognize(suppress_red_seal(image) if enhanced else image)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"pdf": str(pdf), "page": page_index + 1, "dpi": dpi,
        "enhanced": enhanced, "engine": "RapidOCR / ONNX Runtime", "mean_confidence": round(mean, 6),
        "lines": [{"text": x.text, "score": x.score, "box": x.box} for x in lines]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return lines, mean


def extract_pdf_pages(pdf: Path, contract_id: str, cache_root: Path, ocr: LocalOcr,
                      ocr_dpi: int = 300, signature_dpi: int = 600, min_chars: int = 80,
                      min_cjk: int = 20, force: bool = False) -> tuple[list[PageText], dict[str, object], list[str]]:
    pages: list[PageText] = []
    errors: list[str] = []
    file_hash = sha256_file(pdf)
    pdf_cache = cache_root / contract_id / file_hash[:12]
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
        quality = text_quality(native, min_chars, min_cjk)
        signature_page = bool(SIGNATURE_HINT.search(native)) or page_no >= max(1, len(native_pages) - 2)
        parts = [native.strip()] if native.strip() else []
        methods = ["原生文本层"] if quality["usable"] else []
        all_lines: list[OcrLine] = []
        scores: list[float] = []
        enhanced = False
        try:
            if not quality["usable"]:
                lines, mean = _cached_ocr(pdf, page_index, ocr_dpi, pdf_cache / f"page_{page_no:04d}_ocr.json", ocr, force=force)
                all_lines.extend(lines); scores.append(mean); parts.append("\n".join(x.text for x in lines)); methods.append(f"OCR {ocr_dpi}DPI")
            if signature_page:
                lines, mean = _cached_ocr(pdf, page_index, signature_dpi, pdf_cache / f"page_{page_no:04d}_signature.json", ocr, enhanced=True, force=force)
                all_lines.extend(lines); scores.append(mean); parts.append("\n".join(x.text for x in lines)); methods.append(f"签章增强OCR {signature_dpi}DPI")
                enhanced = True
        except Exception as exc:
            errors.append(f"{pdf.name} 第{page_no}页OCR失败：{exc}")
        pages.append(PageText(file_name=pdf.name, file_path=str(pdf), page=page_no,
            text="\n".join(x for x in parts if x).strip(), native_text=native,
            method="+".join(methods) or "无可用文本", confidence=min(scores) if scores else None,
            ocr_lines=all_lines, signature_enhanced=enhanced))

    source = {"文件名": pdf.name, "路径": str(pdf), "页数": len(native_pages), "大小": pdf.stat().st_size,
              "修改时间": pdf.stat().st_mtime, "SHA256": file_hash}
    return pages, source, errors
