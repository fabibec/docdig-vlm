from .img_extractor import ImgExtractor
from .llm_extractor import LlmExtractor
from .table_extractor import TableExtractor


def __getattr__(name: str):
    # OcrExtractor needs vLLM (optional `ocr` dependency group), so import it only on demand.
    if name == "OcrExtractor":
        from .ocr_extractor import OcrExtractor

        return OcrExtractor
    raise AttributeError(name)


__all__ = ["ImgExtractor", "LlmExtractor", "OcrExtractor", "TableExtractor"]
