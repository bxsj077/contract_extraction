"""OCR V2 pipeline with stable, engine-independent result types."""

from .engine import OcrResultLine, PageOcrResult, RapidOcrEngine

__all__ = ["OcrResultLine", "PageOcrResult", "RapidOcrEngine"]
