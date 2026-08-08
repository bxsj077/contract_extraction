from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .engine import OcrResultLine


def cache_key(
    *, pdf_sha256: str, page: int, engine: str, engine_version: str, model: str,
    dpi: int, preprocess: str, ocr_pass: str, enhanced: bool,
) -> str:
    payload = {
        "pdf_sha256": pdf_sha256, "page": page, "engine": engine,
        "engine_version": engine_version, "model": model, "dpi": dpi,
        "preprocess": preprocess, "ocr_pass": ocr_pass, "enhanced": enhanced,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class OcrCacheV2:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def load(self, key: str) -> tuple[list[OcrResultLine], dict[str, Any]] | None:
        path = self.path_for(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return [OcrResultLine(**item) for item in payload.get("lines", [])], payload.get("metadata", {})
        except (OSError, ValueError, TypeError):
            return None

    def save(self, key: str, lines: list[OcrResultLine], metadata: dict[str, Any]) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "metadata": metadata,
            "lines": [line.to_dict() for line in lines],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
