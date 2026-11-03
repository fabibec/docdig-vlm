import atexit
import base64
import logging
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
from huggingface_hub import hf_hub_download

from backend.pipeline.models.pipeline_parts import PipelineStep, PipelineTask, page_stem
from backend.pipeline.settings import settings

log = logging.getLogger(__name__)

PROMPT = (
    "\nExtract all readable content from the image in natural human reading order and output the result "
    "as a single Markdown document. For charts or images, represent them using an HTML image tag: <"
    + 'img src="images/bbox_{left}_{top}_{right}_{bottom}.jpg" />, where left, top, right, bottom are '
    "bounding box coordinates scaled to [0, 1000). Format formulas as LaTeX. Format tables as HTML: "
    "<table>...</table>. Transcribe all other text as standard Markdown. "
    "Preserve the original text without translation or paraphrasing."
)


def _download_models() -> tuple[Path, Path]:
    """Download the GGUFs (cached; skipped if already present)."""
    settings.model_cache_dir.mkdir(parents=True, exist_ok=True)
    token = settings.hf_token or os.environ.get("HF_TOKEN")
    return (
        Path(
            hf_hub_download(settings.ocr_model, settings.ocr_llm_file, local_dir=settings.model_cache_dir, token=token)
        ),
        Path(
            hf_hub_download(
                settings.ocr_model, settings.ocr_mmproj_file, local_dir=settings.model_cache_dir, token=token
            )
        ),
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OcrExtractor(PipelineStep):
    """Optional local OCR on ``cropped/`` -> ``ocr/pNNN.md`` via a llama.cpp server."""

    name = "ocr"

    def setup(self):
        self.proc = None
        self.client = None

        external = os.environ.get("OCR_SERVER_URL")
        if external:
            self.base_url = external.rstrip("/")
        else:
            self._start_server()

        self.client = httpx.Client(base_url=self.base_url, timeout=900)
        atexit.register(self.close)  # safety net if close() is never called

    def close(self):
        if getattr(self, "client", None):
            self.client.close()
            self.client = None
        proc = getattr(self, "proc", None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self.proc = None
        if getattr(self, "_log", None):
            self._log.close()
            self._log = None

    def _start_server(self):
        llm, mmproj = _download_models()
        port = _free_port()
        self.base_url = f"http://127.0.0.1:{port}"

        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = f"{settings.llama_cpp_dir}:{env.get('LD_LIBRARY_PATH', '')}"

        cmd = [
            str(settings.llama_cpp_bin),
            "-m",
            str(llm),
            "--mmproj",
            str(mmproj),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "-ngl",
            "99",
            "-c",
            str(settings.ocr_ctx_size),
            "--parallel",
            "1",
            "--image-min-tokens",
            str(settings.ocr_image_min_tokens),
            "--image-max-tokens",
            str(settings.ocr_image_max_tokens),
            "--jinja",
        ]
        log.info("Starting llama-server: %s", " ".join(cmd))
        self._log = settings.llama_cpp_log_file.open("w")
        self.proc = subprocess.Popen(cmd, env=env, stdout=self._log, stderr=subprocess.STDOUT)

        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited early, see {settings.llama_cpp_log_file}")
            try:
                if httpx.get(f"{self.base_url}/health", timeout=2).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.5)

        self.teardown()
        raise RuntimeError(f"llama-server not ready after 300s, see {settings.llama_cpp_log_file}")

    @staticmethod
    def _clean_truncated_repeats(
        text: str,
        min_text_len: int = 8000,
        max_period: int = 200,
        min_period: int = 1,
        min_repeat_chars: int = 100,
        min_repeat_times: int = 5,
    ) -> str:
        n = len(text)
        if n < min_text_len:
            return text

        max_period = min(max_period, n - 1)
        for unit_len in range(min_period, max_period + 1):
            if text[n - 1] != text[n - 1 - unit_len]:
                continue

            match_len = 1
            idx = n - 2
            while idx >= unit_len and text[idx] == text[idx - unit_len]:
                match_len += 1
                idx -= 1

            total_len = match_len + unit_len
            repeat_times = total_len // unit_len
            tail_len = total_len % unit_len

            if repeat_times >= min_repeat_times and total_len >= min_repeat_chars:
                return text[: n - total_len + unit_len] + text[n - tail_len :]

        return text

    def _ocr_image(self, img_file: Path) -> str:
        b64 = base64.b64encode(img_file.read_bytes()).decode()
        resp = self.client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                            {"type": "text", "text": PROMPT},
                        ],
                    }
                ],
                "max_tokens": settings.ocr_max_output_tokens,
                "temperature": 0.0,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        resp.raise_for_status()
        choice = resp.json()["choices"][0]
        if choice["finish_reason"] == "length":
            log.warning("%s hit max_tokens, output is truncated", img_file.name)
        text = self._clean_truncated_repeats(choice["message"]["content"].strip())
        return text

    def execute(self, task: PipelineTask):
        input_dir = task.step_dir("cropped")
        output_dir = task.step_dir(self.name)
        output_dir.mkdir(parents=True, exist_ok=True)

        img_files = [input_dir / f"{page_stem(n)}.jpg" for n in task.page_nums("cropped")]
        for img_file in img_files:
            md = self._ocr_image(img_file)
            (output_dir / f"{img_file.stem}.md").write_text(md, encoding="utf-8")
