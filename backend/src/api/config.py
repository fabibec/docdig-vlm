from pathlib import Path
from typing import ClassVar

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.src.pipeline.directories import DATA_DIR, PROJECT_ROOT

import secrets


class Settings(BaseSettings):
    """Read from environment variables (prefix WEB_) or a .env file in the working directory."""
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_prefix="WEB_", extra="ignore")

    load_dotenv()

    web_dir: ClassVar[Path] = DATA_DIR / "web"
    web_dir.mkdir(parents=True, exist_ok=True)

    data_dir: Path = DATA_DIR # uploads + pipeline output
    db_path: Path = web_dir / "app.db"  # kept OUTSIDE data_dir so /artifacts can't leak it
    api_url: str = "http://127.0.0.1:8000" # where the worker reaches the API
    internal_token: str = secrets.token_urlsafe(32) # shared secret for /internal/events
    cors_origins: list[str] = ["http://localhost:4200"] # env value must be JSON: '["https://a.org","https://b.org"]'

    @field_validator("data_dir", "db_path")
    @classmethod
    def _absolute(cls, v: Path) -> Path:
        return v.resolve()


settings = Settings()
