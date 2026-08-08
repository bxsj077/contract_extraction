from __future__ import annotations

from dataclasses import asdict, dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Protocol


def _as_box(value: Any) -> list[list[float]]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    try:
        return [[float(point[0]), float(point[1])] for point in value]
    except (TypeError, ValueError, IndexError):
        return []


@dataclass(slots=True)
class OcrResultLine:
    text: str
    score: float
    box: list[list[float]] = field(default_factory=list)
    engine: str = ""
    model: str = ""
    engine_version: str = ""
    dpi: int = 0
    preprocessing: str = ""
    ocr_pass: str = "normal"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PageOcrResult:
    page: int
    page_type: str
    native_text: str = ""
    ocr_lines: list[OcrResultLine] = field(default_factory=list)
    mean_confidence: float | None = None
    selected_text: str = ""
    table_regions: list[dict[str, Any]] = field(default_factory=list)
    table_rows: list[dict[str, Any]] = field(default_factory=list)
    ocr_pass: str = "native"
    retry_used: bool = False
    signature_enhanced: bool = False
    engine: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OcrEngine(Protocol):
    engine_name: str
    engine_version: str
    model_name: str

    def recognize(
        self,
        image: Any,
        *,
        dpi: int,
        preprocessing: str,
        ocr_pass: str,
    ) -> list[OcrResultLine]: ...


class RapidOcrEngine:
    """Adapter for RapidOCR 3.x `RapidOCROutput`.

    RapidOCR 3.9+ uses PP-OCRv6 by default.  The import and model construction are
    lazy so pure-text parsing, the Web application and tests can start even when
    the optional runtime has not yet been installed.
    """

    engine_name = "RapidOCR"
    model_name = "PP-OCRv6"

    def __init__(self, engine: Any | None = None) -> None:
        try:
            self.engine_version = version("rapidocr")
        except PackageNotFoundError:
            self.engine_version = "not-installed"
        self._engine = engine

    def _get_engine(self) -> Any:
        if self._engine is None:
            try:
                from rapidocr import RapidOCR
            except ImportError as exc:
                raise RuntimeError(
                    "V2需要rapidocr>=3.9和onnxruntime；请在v2目录执行 python -m pip install -e ."
                ) from exc
            self._engine = RapidOCR()
        return self._engine

    def recognize(
        self,
        image: Any,
        *,
        dpi: int,
        preprocessing: str,
        ocr_pass: str,
    ) -> list[OcrResultLine]:
        output = self._get_engine()(image)
        if output is None:
            return []

        # RapidOCR 3.x public result object.
        boxes = getattr(output, "boxes", None)
        texts = getattr(output, "txts", None)
        scores = getattr(output, "scores", None)

        # Defensive compatibility for pre-3.x/fake engines used in tests.  V2
        # never exposes this raw shape above the adapter.
        if texts is None and isinstance(output, tuple):
            raw = output[0] or []
            boxes = [item[0] for item in raw]
            texts = [item[1] for item in raw]
            scores = [item[2] for item in raw]

        if texts is None:
            return []
        if hasattr(texts, "tolist"):
            texts = texts.tolist()
        if hasattr(boxes, "tolist"):
            boxes = boxes.tolist()
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        boxes = boxes or [[] for _ in texts]
        scores = scores or [0.0 for _ in texts]

        lines: list[OcrResultLine] = []
        for box, text, score in zip(boxes, texts, scores):
            clean = str(text or "").strip()
            if not clean:
                continue
            lines.append(OcrResultLine(
                text=clean,
                score=float(score or 0.0),
                box=_as_box(box),
                engine=self.engine_name,
                model=self.model_name,
                engine_version=self.engine_version,
                dpi=dpi,
                preprocessing=preprocessing,
                ocr_pass=ocr_pass,
            ))
        return lines
