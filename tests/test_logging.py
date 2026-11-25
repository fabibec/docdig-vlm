"""Logging core: JSON format, rotating file, settings, worker -> main process queue forwarding."""

import json
import logging
import logging.handlers
import multiprocessing as mp
from pathlib import Path

import pytest

from backend.logging_config import (
    JsonFormatter,
    LogSettings,
    TextFormatter,
    parse_level,
    setup_logging,
    setup_worker_logging,
    start_queue_listener,
)


@pytest.fixture(autouse=True)
def restore_root_logger(monkeypatch):
    """Keep tests from leaking handlers/levels into each other (or reading LOG_* from the dev .env)."""
    for var in ("LOG_LEVEL", "LOG_FILE", "LOG_MAX_BYTES", "LOG_BACKUP_COUNT", "LOG_FILE_FORMAT", "LOG_LIBS_LEVEL"):
        monkeypatch.delenv(var, raising=False)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in list(root.handlers):
        if h not in handlers:
            root.removeHandler(h)
            h.close()
    for h in handlers:
        if h not in root.handlers:
            root.addHandler(h)
    root.setLevel(level)


def _settings(tmp_path: Path, **kw) -> LogSettings:
    return LogSettings(_env_file=None, file=tmp_path / "logs" / "docdig.log", **kw)


def _log_file(s: LogSettings) -> Path:
    assert s.file is not None, "file logging is disabled in these settings"
    return s.file


def _lines(path: Path | LogSettings) -> list[dict]:
    path = _log_file(path) if isinstance(path, LogSettings) else path
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _record(msg="hello %s", args=("world",), exc_info=None, **extra) -> logging.LogRecord:
    r = logging.LogRecord("backend.test", logging.WARNING, __file__, 42, msg, args, exc_info)
    r.__dict__.update(extra)
    return r


# ---------- formatter ----------


def test_json_formatter_fields_and_extras():
    out = json.loads(JsonFormatter().format(_record(batch="b-1", doc="d-1", page=3, duration_ms=12.5)))
    assert out["msg"] == "hello world"
    assert out["level"] == "WARNING"
    assert out["logger"] == "backend.test"
    assert out["process"] == mp.current_process().name
    assert out["line"] == 42
    assert out["ts"].endswith("+00:00") and "." in out["ts"]  # ISO 8601 UTC with ms
    assert (out["batch"], out["doc"], out["page"], out["duration_ms"]) == ("b-1", "d-1", 3, 12.5)
    assert "exc" not in out and "args" not in out and "taskName" not in out


def test_json_formatter_exception_and_unserialisable_extra():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        rec = _record(exc_info=sys.exc_info(), path=Path("/x/y"))
    out = json.loads(JsonFormatter().format(rec))
    assert "ValueError: boom" in out["exc"] and "Traceback" in out["exc"]
    assert out["path"] == "/x/y"  # default=str


def test_text_formatter_appends_extras():
    text = TextFormatter().format(_record(doc="d-1"))
    assert "WARNING" in text and "backend.test: hello world" in text and text.endswith("doc=d-1")


# ---------- settings ----------


@pytest.mark.parametrize(("value", "expected"), [("info", 20), ("DEBUG", 10), (" Warning ", 30), ("15", 15), (40, 40)])
def test_parse_level(value, expected):
    assert parse_level(value) == expected


def test_invalid_level_is_a_clear_error(monkeypatch):
    with pytest.raises(ValueError, match="unknown log level"):
        parse_level("loud")
    monkeypatch.setenv("LOG_LEVEL", "loud")
    with pytest.raises(ValueError, match="unknown log level"):
        LogSettings(_env_file=None)


def test_level_from_env_is_normalised(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert LogSettings(_env_file=None).level == "DEBUG"


def test_default_file_follows_web_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB_DATA_DIR", str(tmp_path))
    assert LogSettings(_env_file=None).file == tmp_path / "logs" / "docdig.log"


def test_empty_log_file_disables_file_handler(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_FILE", "")
    s = LogSettings(_env_file=None)
    assert s.file is None
    setup_logging(s)
    assert not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logging.getLogger().handlers)
    logging.getLogger("backend.test").warning("x")
    assert not (tmp_path / "logs").exists()


# ---------- setup ----------


def test_setup_writes_json_file(tmp_path):
    s = setup_logging(_settings(tmp_path, level="info"))
    logging.getLogger("backend.test").info("doc done", extra={"doc": "d-1", "page": 2})
    logging.getLogger("backend.test").debug("hidden")
    (rec,) = _lines(s)
    assert rec["msg"] == "doc done" and rec["doc"] == "d-1" and rec["page"] == 2


def test_setup_is_idempotent(tmp_path):
    root = logging.getLogger()
    s = _settings(tmp_path)
    setup_logging(s)
    after_first = len(root.handlers)
    setup_logging(s)
    setup_logging(s)
    ours = [h for h in root.handlers if getattr(h, "_docdig_handler", False)]
    assert len(root.handlers) == after_first and len(ours) == 2  # console + file, once
    logging.getLogger("backend.test").warning("once")
    assert len(_lines(s)) == 1


def test_libs_level_quiets_noisy_loggers(tmp_path):
    s = setup_logging(_settings(tmp_path, level="DEBUG", libs_level="ERROR"))
    logging.getLogger("httpx").warning("noise")
    logging.getLogger("backend.test").debug("mine")
    assert [r["msg"] for r in _lines(s)] == ["mine"]


def test_text_file_format(tmp_path):
    s = setup_logging(_settings(tmp_path, file_format="text"))
    logging.getLogger("backend.test").warning("plain", extra={"doc": "d-1"})
    text = _log_file(s).read_text(encoding="utf-8")
    assert "backend.test: plain doc=d-1" in text and not text.startswith("{")


def test_rotation(tmp_path):
    s = setup_logging(_settings(tmp_path, max_bytes=500, backup_count=2))
    log = logging.getLogger("backend.test")
    for i in range(50):
        log.warning("line %d %s", i, "x" * 50)
    files = sorted(p.name for p in _log_file(s).parent.iterdir())
    assert files == ["docdig.log", "docdig.log.1", "docdig.log.2"]
    for p in _log_file(s).parent.iterdir():
        _lines(p)  # every rotated file is still valid JSON lines


# ---------- worker processes ----------


def _child(queue) -> None:
    setup_worker_logging(queue, "INFO")
    log = logging.getLogger("backend.child")
    log.info("from child", extra={"doc": "d-9", "obj": object()})
    log.debug("filtered")
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError:
        log.exception("child failed")


def test_queue_forwarding_from_spawned_process(tmp_path):
    s = setup_logging(_settings(tmp_path))
    ctx = mp.get_context("spawn")
    queue, listener = start_queue_listener(ctx)
    try:
        p = ctx.Process(target=_child, args=(queue,), name="SpawnProcess-test")
        p.start()
        p.join(60)
        assert p.exitcode == 0
    finally:
        listener.stop()

    recs = [r for r in _lines(s) if r["logger"] == "backend.child"]
    assert [r["msg"] for r in recs] == ["from child", "child failed"]
    first, failed = recs
    assert first["process"] == "SpawnProcess-test" and first["doc"] == "d-9"
    assert first["obj"].startswith("<object object")  # unpicklable extra was stringified
    assert "ZeroDivisionError" in failed["exc"]
    assert "ZeroDivisionError" not in failed["msg"]  # traceback not duplicated into msg
