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
    ctx = {"model": settings.ocr_model, "cache_dir": str(settings.model_cache_dir)}
    log.info(
        "Fetching OCR model %s (%s, %s) into %s",
        settings.ocr_model,
        settings.ocr_llm_file,
        settings.ocr_mmproj_file,
        settings.model_cache_dir,
        extra=ctx,
    )
    start = time.perf_counter()
    paths = (
        Path(
            hf_hub_download(settings.ocr_model, settings.ocr_llm_file, local_dir=settings.model_cache_dir, token=token)
        ),
        Path(
            hf_hub_download(
                settings.ocr_model, settings.ocr_mmproj_file, local_dir=settings.model_cache_dir, token=token
            )
        ),
    )
    duration_ms = round((time.perf_counter() - start) * 1000)
    log.info(
        "OCR model %s ready after %d ms", settings.ocr_model, duration_ms, extra={**ctx, "duration_ms": duration_ms}
    )
    return paths


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
            log.info("Using external OCR server at %s", self.base_url, extra={"step": self.name})
        else:
            self._start_server()

        self.client = httpx.Client(base_url=self.base_url, timeout=900)
        atexit.register(self.close)  # safety net if close() is never called

    def close(self):
        if client := getattr(self, "client", None):
            client.close()
            self.client = None
        proc = getattr(self, "proc", None)
        if proc and proc.poll() is None:
            log.info("Stopping llama-server (pid %d)", proc.pid, extra={"step": self.name, "pid": proc.pid})
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                log.warning(
                    "llama-server did not stop within 15s, killing it", extra={"step": self.name, "pid": proc.pid}
                )
                proc.kill()
                proc.wait()
        self.proc = None
        if log_file := getattr(self, "_log", None):
            log_file.close()
            self._log = None

    def _start_server(self):
        if not settings.llama_cpp_bin.is_file():
            raise FileNotFoundError(f"llama-server binary not found at {settings.llama_cpp_bin} (set LLAMA_CPP_BIN)")
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
        ctx = {"step": self.name, "port": port, "model_file": llm.name}
        log.info(
            "Starting llama-server on port %d with model %s (mmproj %s), log: %s",
            port,
            llm.name,
            mmproj.name,
            settings.llama_cpp_log_file,
            extra=ctx,
        )
        log.debug("llama-server command: %s", " ".join(cmd), extra=ctx)
        self._log = settings.llama_cpp_log_file.open("w")
        self.proc = subprocess.Popen(cmd, env=env, stdout=self._log, stderr=subprocess.STDOUT)

        start = time.monotonic()
        deadline = start + 300
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                self.close()
                raise RuntimeError(f"llama-server exited early, see {settings.llama_cpp_log_file}")
            try:
                if httpx.get(f"{self.base_url}/health", timeout=2).status_code == 200:
                    duration_ms = round((time.monotonic() - start) * 1000)
                    log.info(
                        "llama-server ready on port %d after %d ms",
                        port,
                        duration_ms,
                        extra={**ctx, "duration_ms": duration_ms},
                    )
                    return
            except httpx.TransportError:
                pass
            time.sleep(0.5)

        log.warning(
            "llama-server not ready after 300s, shutting it down (see %s)", settings.llama_cpp_log_file, extra=ctx
        )
        self.close()
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
        if self.client is None:
            raise RuntimeError("OcrExtractor.setup() was not called")
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
            log.warning("%s hit max_tokens, output is truncated", img_file.name, extra={"file": img_file.name})
        text = self._clean_truncated_repeats(choice["message"]["content"].strip())
        return text

    def execute(self, task: PipelineTask):
        input_dir = task.step_dir("cropped")
        output_dir = task.step_dir(self.name)
        output_dir.mkdir(parents=True, exist_ok=True)

        pdf = task.input_pdf.name
        nums = task.page_nums("cropped")
        for num in nums:
            img_file = input_dir / f"{page_stem(num)}.jpg"
            start = time.perf_counter()
            md = self._ocr_image(img_file)
            (output_dir / f"{img_file.stem}.md").write_text(md, encoding="utf-8")
            duration_ms = round((time.perf_counter() - start) * 1000)
            log.debug(
                "OCR page %d of %s: %d chars in %d ms",
                num,
                pdf,
                len(md),
                duration_ms,
                extra={"pdf": pdf, "step": self.name, "page": num, "duration_ms": duration_ms},
            )
        log.info("OCR done for %d page(s) of %s", len(nums), pdf, extra={"pdf": pdf, "step": self.name})
