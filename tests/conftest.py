"""Shared pytest setup for the whole suite.

Runs before any test module imports ``backend``: the settings objects are created at import
time, so the environment must be prepared first.

- ``LLM_BASE_URL`` / ``LLM_API_KEY``: dummy defaults so the pipeline settings load without a
  ``.env``. Values already set (e.g. by CI) are kept; no test calls the LLM either way.
- ``WEB_DATA_DIR`` always points to a temporary folder (``WEB_DB_PATH`` inside it), so the
  tests never touch the real ``data/`` state, even if the shell exports those variables.
"""

import atexit
import os
import shutil
import tempfile

os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:9")  # discard port: an accidental call fails fast
os.environ.setdefault("LLM_API_KEY", "test-dummy")
os.environ.setdefault("LOG_FILE", "")  # no rotating log file during tests

_data_dir = tempfile.mkdtemp(prefix="docdig-test-")
atexit.register(shutil.rmtree, _data_dir, ignore_errors=True)
os.environ["WEB_DATA_DIR"] = _data_dir
os.environ["WEB_DB_PATH"] = os.path.join(_data_dir, "state", "app.db")  # beats a WEB_DB_PATH in .env
