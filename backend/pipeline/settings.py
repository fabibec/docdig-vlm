from pathlib import Path
from typing import Literal

from pydantic import Field, FilePath, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.pipeline.directories import PROJECT_ROOT, PROMPT_DIR


class Settings(BaseSettings):
    # LLM Extractor
    llm_api_url: HttpUrl = Field(alias="LLM_BASE_URL")
    llm_api_key: str
    llm_model_name: str = Field(default="openai/gpt-6-luna")
    llm_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] = Field(default="low")
    llm_seed: int = Field(default=42)
    step_concurrency: int = Field(default=100)
    llm_prompt_file: FilePath = Field(default=PROMPT_DIR / "llm_prompt.txt")

    # OCR Extractor
    ocr_model: str = Field(default="Abiray/OvisOCR2-GGUF")
    ocr_llm_file: str = Field(default="OvisOCR2-F16.gguf")
    ocr_mmproj_file: str = Field(default="mmproj-F32.gguf")
    hf_token: str | None = None

    # Plain Path (not FilePath/DirectoryPath): OCR is optional, so these must not be required to
    # exist at import time (e.g. in the Docker image). They are checked/created when OCR starts.
    llama_cpp_dir: Path = Field(default=PROJECT_ROOT / "lib")
    llama_cpp_bin: Path = Field(default=PROJECT_ROOT / "lib" / "llama-server")
    llama_cpp_log_file: Path = Field(default=PROJECT_ROOT / "llama-server.log")
    model_cache_dir: Path = Field(default=PROJECT_ROOT / "models" / "ovis-ocr2")

    ocr_ctx_size: int = Field(default=16384)
    ocr_max_output_tokens: int = Field(default=8192)
    ocr_image_min_tokens: int = Field(default=256)
    ocr_image_max_tokens: int = Field(default=4096)

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="allow")


settings = Settings()
