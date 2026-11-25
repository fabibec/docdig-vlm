from .img_extractor import ImgExtractor
from .llm_extractor import LlmExtractor
from .llm_postprocessor import LlmPostprocessor
from .table_extractor import TableExtractor
from .table_orientation_detector import TableOrientationDetector


def __getattr__(name: str):
    # Optional OCR steps are imported lazily so the default pipeline needs neither vLLM nor pytesseract.
    if name == "OcrExtractor":
        from .ocr_extractor import OcrExtractor

        return OcrExtractor
    if name == "TesseractExtractor":
        from .tesseract_extractor import TesseractExtractor

        return TesseractExtractor
    raise AttributeError(name)


__all__ = [
    "ImgExtractor",
    "LlmExtractor",
    "LlmPostprocessor",
    "OcrExtractor",
    "TableExtractor",
    "TableOrientationDetector",
    "TesseractExtractor",
]
