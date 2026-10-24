from typing import Literal

from pydantic import Field, FilePath, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.src.pipeline.directories import PROJECT_ROOT, PROMPT_DIR


class Settings(BaseSettings):
    llm_api_url: HttpUrl = Field(alias="LLM_BASE_URL")
    llm_api_key: str
    llm_model_name: str = Field(default="openai/gpt-6-luna")
    llm_reasoning_effort: Literal[
        "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = Field(default="low")
    llm_seed: int = Field(default=42)

    llm_prompt_file: FilePath = Field(default=PROMPT_DIR / "llm_prompt.txt")

    ocr_model: str = Field(default="ATH-MaaS/OvisOCR2")

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="allow"
    )


settings = Settings()
