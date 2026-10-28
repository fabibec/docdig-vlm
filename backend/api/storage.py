"""Artifact storage. The only module that knows the absolute artifact root.

Layout (relative to ``settings.batches_dir``)::

    <batch>/<doc>/input/<file>.pdf
                 /raw/pNNN.jpg
                 /cropped/pNNN.jpg, pNNN.geom.json
                 /processed/pNNN.json, pNNN.md

Local filesystem for now. A Nextcloud sync can later hook in at ``after_upload`` /
``after_processing`` / ``after_review`` (push the doc folder via WebDAV) without changing
callers: the pipeline keeps working on local files.
"""

import re
import unicodedata
from pathlib import Path

import anyio
from fastapi import UploadFile

from backend.api.config import settings
from backend.api.enums import ArtifactState

CHUNK = 1024 * 1024


def slugify(s: str, max_len: int = 40) -> str:
    s = s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:max_len].strip("-")


class FileTooLarge(Exception):
    pass


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root

    def doc_dir(self, rel_dir: str) -> Path:
        p = (self.root / rel_dir).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("path escapes artifact root")
        return p

    def input_pdf(self, rel_dir: str, filename: str) -> Path:
        return self.doc_dir(rel_dir) / "input" / filename

    def artifact(self, rel_dir: str, state: ArtifactState, num: int, suffix: str) -> Path:
        return self.doc_dir(rel_dir) / state.value / f"p{num:03d}{suffix}"

    async def save_upload(self, upload: UploadFile, rel_dir: str, filename: str, max_bytes: int) -> Path:
        """Stream an upload to ``<doc>/input/<filename>`` in 1 MB chunks."""
        target = self.input_pdf(rel_dir, filename)
        await anyio.Path(target.parent).mkdir(parents=True, exist_ok=True)
        written = 0
        async with await anyio.open_file(target, "wb") as out:
            while chunk := await upload.read(CHUNK):
                written += len(chunk)
                if written > max_bytes:
                    raise FileTooLarge(filename)
                await out.write(chunk)
        return target

    # --- hooks for a later Nextcloud sync (no-ops for now) ---
    async def after_upload(self, rel_dir: str) -> None: ...

    async def after_processing(self, rel_dir: str) -> None: ...

    async def after_review(self, rel_dir: str) -> None: ...


store = ArtifactStore(settings.batches_dir)
