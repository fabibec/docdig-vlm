import os

# prepare_crops imports pipeline steps (and with them the LLM settings); dummies keep that offline.
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:9")
os.environ.setdefault("LLM_API_KEY", "test-dummy")
