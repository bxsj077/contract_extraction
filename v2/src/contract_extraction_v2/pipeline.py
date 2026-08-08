from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from .exporters import export_excel, load_checkpoint, save_checkpoint
from .pdf_io import LocalOcr, discover_contracts, extract_pdf_pages
from .rules import analyze_contract


LOGGER = logging.getLogger("contract_extraction_v2")


def load_config(path: Path | None = None) -> dict[str, object]:
    default = Path(__file__).resolve().parents[2] / "config" / "default.json"
    target = path or default
    return json.loads(target.read_text(encoding="utf-8"))


def run(input_root: Path, output_root: Path, config_path: Path | None = None,
        contracts: set[str] | None = None, resume: bool = True, force: bool = False,
        ocr_dpi: int | None = None, signature_dpi: int | None = None) -> dict[str, object]:
    config = load_config(config_path)
    if ocr_dpi:
        config["ocr_dpi"] = ocr_dpi
    if signature_dpi:
        config["signature_dpi"] = signature_dpi
    output_root.mkdir(parents=True, exist_ok=True)
    checkpoints = output_root / "contracts"
    cache = output_root / "ocr_cache"
    ocr = LocalOcr()
    payloads: list[dict[str, object]] = []
    items = discover_contracts(input_root, contracts)
    LOGGER.info("发现 %d 个合同文件夹", len(items))

    for index, item in enumerate(items, 1):
        checkpoint = checkpoints / f"{item.contract_id}.json"
        cached = load_checkpoint(checkpoint, item.fingerprint) if resume and not force else None
        if cached:
            LOGGER.info("[%d/%d] %s：指纹未变化，复用断点结果", index, len(items), item.contract_id)
            payloads.append(cached)
            continue
        LOGGER.info("[%d/%d] %s：开始处理 %d 个PDF", index, len(items), item.contract_id, len(item.pdfs))
        pages = []
        sources = []
        errors = []
        for pdf in item.pdfs:
            try:
                file_pages, source, file_errors = extract_pdf_pages(pdf, item.contract_id, cache, ocr,
                    int(config.get("ocr_dpi", 300)), int(config.get("signature_dpi", 600)),
                    int(config.get("native_text_min_chars", 80)), int(config.get("native_text_min_cjk", 20)), force,
                    dict(config.get("ocr_v2", {})))
                pages.extend(file_pages); sources.append(source); errors.extend(file_errors)
            except Exception as exc:
                errors.append(f"{pdf.name} 处理失败：{exc}")
                LOGGER.exception("%s/%s 处理失败", item.contract_id, pdf.name)
        output = analyze_contract(item.contract_id, str(item.folder), pages, sources, item.fingerprint, config, errors)
        save_checkpoint(output, checkpoints)
        payloads.append(output.payload())

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    excel = export_excel(payloads, output_root / f"合同信息提取_{timestamp}.xlsx")
    summary = {"合同数量": len(items), "结果数量": len(payloads), "Excel": str(excel),
               "JSON目录": str(checkpoints), "OCR缓存目录": str(cache)}
    (output_root / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
