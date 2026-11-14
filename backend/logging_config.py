"""Logging setup shared by the API and the pipeline CLI.

- Console: plain text on stderr.
- File: rotating log, one JSON object per line (default ``<data dir>/logs/docdig.log``).
- Pool worker processes never open the file. They send records over a multiprocessing queue;
  a ``QueueListener`` in the main process hands them to the root handlers.

All settings come from ``LOG_*`` env vars or the project ``.env`` (see ``LogSettings``).
Add context to a record with ``extra=``: ``log.info("doc done", extra={"batch": b, "doc": d, "page": n})``.
"""

import json
import logging
import logging.handlers
import multiprocessing as mp
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.pipeline.directories import DATA_DIR, PROJECT_ROOT

CONSOLE_FORMAT = "%(asctime)s %(levelname)s %(processName)s %(name)s: %(message)s"
NOISY_LOGGERS = (
    "httpx",
    "httpx2",
    "httpcore",
    "httpcore2",
    "openai",
    "sqlalchemy",
    "aiosqlite",
    "multipart",
    "python_multipart",
    "PIL",
)
_MARK = "_docdig_handler"  # attribute set on handlers installed here (so setup can replace them)


def parse_level(value: str | int) -> int:
    """'info' / 'INFO' / '20' / 20 -> 20. Raises ValueError for unknown names."""
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    levels = logging.getLevelNamesMapping()
    try:
        return levels[text.upper()]
    except KeyError:
        valid = ", ".join(sorted(k for k in levels if k != "NOTSET"))
        raise ValueError(f"unknown log level {value!r}, choose from {valid}") from None


class LogSettings(BaseSettings):
    """Env vars with prefix ``LOG_`` (or the project ``.env``)."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_prefix="LOG_", extra="ignore", populate_by_name=True
    )

    # Same env var as the web app's data dir, so the log lands next to its state (Docker: /data/logs).
    web_data_dir: Path | None = Field(default=None, validation_alias="WEB_DATA_DIR")
    level: str = "INFO"
    # Unset -> <data dir>/logs/docdig.log; LOG_FILE= (empty) -> no file log.
    file: Path | None = Field(default_factory=lambda d: (d.get("web_data_dir") or DATA_DIR) / "logs" / "docdig.log")
    max_bytes: int = 10_485_760
    backup_count: int = 5
    file_format: Literal["json", "text"] = "json"
    libs_level: str = "WARNING"

    @field_validator("level", "libs_level")
    @classmethod
    def _check_level(cls, v: str) -> str:
        parse_level(v)
        return v.strip().upper()

    @field_validator("file", mode="before")
    @classmethod
    def _empty_disables(cls, v: Any) -> Any:
        return None if v is None or (isinstance(v, str) and not v.strip()) else v

    @model_validator(mode="after")
    def _resolve(self):
        if self.file is not None:
            self.file = self.file.resolve()  # like WEB_DATA_DIR in backend/api/config.py
        return self


_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


def record_extras(record: logging.LogRecord) -> dict[str, Any]:
    """Attributes passed via ``extra=`` (everything that is not a standard LogRecord field)."""
    return {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS and not k.startswith("_")}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, msg, process, module, line, [exc], [stack], extras."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "process": record.processName,
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        elif record.exc_text:
            out["exc"] = record.exc_text
        if record.stack_info:
            out["stack"] = self.formatStack(record.stack_info)
        for k, v in record_extras(record).items():
            out.setdefault(k, v)
        return json.dumps(out, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Plain text; extras are appended as ``key=value`` unless the message already shows the value."""

    def __init__(self) -> None:
        super().__init__(CONSOLE_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        message = record.getMessage()
        if extras := {k: v for k, v in record_extras(record).items() if str(v) not in message}:
            first, nl, rest = text.partition("\n")
            text = first + " " + " ".join(f"{k}={v}" for k, v in extras.items()) + nl + rest
        return text


def _mark(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _MARK, True)
    return handler


def _reset_root() -> logging.Logger:
    """Remove (and close) the handlers a previous setup installed; foreign handlers stay."""
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, _MARK, False):
            root.removeHandler(h)
            h.close()
    return root


def _apply_levels(root: logging.Logger, level: str | int, libs_level: str | int) -> None:
    root.setLevel(parse_level(level))
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(parse_level(libs_level))


_current: LogSettings | None = None


def setup_logging(settings: LogSettings | None = None) -> LogSettings:
    """Configure the root logger of the main process: console + rotating file. Safe to call repeatedly."""
    global _current
    s = settings or LogSettings()
    root = _reset_root()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(TextFormatter())
    root.addHandler(_mark(console))

    if s.file is not None:
        s.file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            s.file, maxBytes=s.max_bytes, backupCount=s.backup_count, encoding="utf-8", delay=True
        )
        fh.setFormatter(JsonFormatter() if s.file_format == "json" else TextFormatter())
        root.addHandler(_mark(fh))

    _apply_levels(root, s.level, s.libs_level)
    logging.captureWarnings(True)
    _current = s
    return s


def current_settings() -> LogSettings:
    """The settings of the last ``setup_logging`` call (or fresh ones from the env)."""
    return _current or LogSettings()


# ---------- multiprocessing ----------


class _RootForwarder(logging.Handler):
    """Listener-side handler: hand a worker's record to the current root handlers."""

    def emit(self, record: logging.LogRecord) -> None:
        logging.getLogger().handle(record)


class _WorkerQueueHandler(logging.handlers.QueueHandler):
    """Like QueueHandler, but keeps ``msg`` free of the traceback and makes extras picklable."""

    _formatter = logging.Formatter()

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        r = logging.makeLogRecord(record.__dict__)
        r.msg, r.args = record.getMessage(), None
        if record.exc_info:
            r.exc_text = self._formatter.formatException(record.exc_info)
        r.exc_info = None
        for k, v in record_extras(r).items():
            if not isinstance(v, str | int | float | bool | type(None)):
                setattr(r, k, str(v))
        return r


def start_queue_listener(ctx: Any = None) -> tuple[Any, logging.handlers.QueueListener]:
    """Main process: create a log queue (from the given mp context) and start a listener on it.

    Pass the queue to ``setup_worker_logging`` in each child; call ``listener.stop()`` on shutdown.
    """
    queue = (ctx or mp.get_context("spawn")).Queue()
    listener = logging.handlers.QueueListener(queue, _RootForwarder())
    listener.start()
    return queue, listener


def setup_worker_logging(queue: Any, level: str | int = "INFO", libs_level: str | int = "WARNING") -> None:
    """Child process: drop all root handlers and send every record to the main process over ``queue``."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.addHandler(_mark(_WorkerQueueHandler(queue)))
    _apply_levels(root, level, libs_level)
    logging.captureWarnings(True)
