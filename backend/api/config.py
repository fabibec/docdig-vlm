from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.pipeline.directories import DATA_DIR, PROJECT_ROOT


class Settings(BaseSettings):
    """Web app settings. Env vars with prefix ``WEB_`` or the project ``.env``.

    Volume layout under ``data_dir`` (see docs/backend.md):
        state/app.db                 local state, never synced
        batches/<batch>/<doc>/...    artifact tree (input, raw, cropped, processed)
    """

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_prefix="WEB_", extra="ignore")

    data_dir: Path = DATA_DIR
    db_path: Path | None = None  # default: <data_dir>/state/app.db
    frontend_dir: Path = PROJECT_ROOT / "frontend" / "dist" / "browser"
    pool_size: int = 2  # parallel documents (processes)
    max_upload_mb: int = 200  # per file

    @model_validator(mode="after")
    def _resolve(self):
        self.data_dir = self.data_dir.resolve()
        self.db_path = (self.db_path or self.data_dir / "state" / "app.db").resolve()
        return self

    @property
    def batches_dir(self) -> Path:
        return self.data_dir / "batches"


settings = Settings()
