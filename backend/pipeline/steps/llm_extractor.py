import asyncio
import base64
import csv
import io
import logging
import ssl
import time
from pathlib import Path

import truststore
from genai_prices import Usage, calc_price
from openai import AsyncOpenAI, ContentFilterFinishReasonError, DefaultAsyncHttpx2Client, LengthFinishReasonError
from pydantic import FilePath, ValidationError
from tabulate import tabulate

from backend.pipeline.models.llm_responses import ExtractionResult, PageTable, TableRow
from backend.pipeline.models.pipeline_parts import PipelineStep, PipelineTask, page_stem
from backend.pipeline.settings import settings

log = logging.getLogger(__name__)


def write_result(out_dir: Path, num: int, result: ExtractionResult) -> None:
    """Write ``pNNN.json`` (the full result) plus ``pNNN.md`` and ``pNNN.csv`` (the rows) into ``out_dir``."""
    stem = page_stem(num)
    (out_dir / f"{stem}.json").write_text(result.model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8")

    rows = result.table.model_dump()["rows"]
    (out_dir / f"{stem}.md").write_text(
        tabulate(rows, headers="keys", tablefmt="pipe", disable_numparse=True),
        encoding="utf-8",
    )

    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(TableRow.model_fields))  # header even for 0 rows
    writer.writeheader()
    writer.writerows(rows)
    (out_dir / f"{stem}.csv").write_text(buf.getvalue(), encoding="utf-8", newline="")


def estimate_cost(provider_id: str, model_ref: str, input_tokens: int, output_tokens: int, ctx=None) -> float:
    """Price of one call in $ via genai-prices; 0.0 (with a warning) for models it doesn't know yet."""
    try:
        price = calc_price(
            Usage(input_tokens=input_tokens, output_tokens=output_tokens), model_ref=model_ref, provider_id=provider_id
        )
    except LookupError:
        log.warning("No price known for %s/%s, cost recorded as 0", provider_id, model_ref, extra=ctx or {})
        return 0.0
    return round(float(price.total_price), 6)


class LlmRefusal(RuntimeError):
    """The model refused to answer; raised instead of exiting so pool workers survive."""


class LlmExtractor(PipelineStep):
    """Step 3: ``cropped/pNNN.jpg`` -> ``processed/pNNN.json`` (ExtractionResult) + ``.md``."""

    name = "processed"

    def setup(self):
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

        self.runner = asyncio.Runner()
        self.client = AsyncOpenAI(
            base_url=str(settings.llm_api_url),
            api_key=settings.llm_api_key,
            http_client=DefaultAsyncHttpx2Client(verify=ctx, http2=True),
        )

        self.model = settings.llm_model_name
        self.provider_id, self.model_ref = settings.llm_model_name.split("/")
        self.reasoning_effort = settings.llm_reasoning_effort
        self.seed = settings.llm_seed

        self.concurrency = settings.step_concurrency

        with settings.llm_prompt_file.open("r", encoding="utf-8") as f:
            self.prompt = f.read()

    def close(self):
        self.runner.run(self.client.close())
        self.runner.close()

    @staticmethod
    def _encode_image(path, mime="image/jpeg", detail="high"):
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}", "detail": detail},
        }

    async def _llm_extraction(self, image: FilePath, ctx: dict | None = None) -> ExtractionResult:
        ctx = {**(ctx or {}), "file": image.name, "model": self.model}
        log.debug("LLM request for %s (model %s)", image.name, self.model, extra=ctx)
        try:
            completion = await self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Extract the table."},
                            LlmExtractor._encode_image(image),
                        ],
                    },
                ],
                response_format=PageTable,
                reasoning_effort=self.reasoning_effort,
                seed=self.seed,
            )
        except (LengthFinishReasonError, ContentFilterFinishReasonError, ValidationError) as e:
            log.warning("LLM returned invalid output for %s: %s", image.name, type(e).__name__, extra=ctx)
            raise

        msg = completion.choices[0].message
        if msg.refusal or msg.parsed is None:
            log.warning(
                "LLM refused or returned no parsed output for %s: %s",
                image.name,
                msg.refusal or "no parsed output",
                extra=ctx,
            )
            raise LlmRefusal(f"{image.name}: {msg.refusal or 'no parsed output'}")

        u = completion.usage
        if u is None:
            log.warning("LLM response for %s has no token usage, cost recorded as 0", image.name, extra=ctx)
        input_tokens = u.prompt_tokens if u else 0
        # completion_tokens already includes reasoning tokens (completion_tokens_details is only a breakdown).
        output_tokens = u.completion_tokens if u else 0

        return ExtractionResult(
            source=self.model,
            table=msg.parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=estimate_cost(self.provider_id, self.model_ref, input_tokens, output_tokens, ctx),
        )

    async def _run_all(self, task: PipelineTask, nums: list[int]):
        sem = asyncio.Semaphore(self.concurrency)
        in_dir, out_dir = task.step_dir("cropped"), task.step_dir(self.name)
        done = 0

        async def bounded(num: int):
            nonlocal done
            ctx = {"pdf": task.input_pdf.name, "step": self.name, "page": num}
            async with sem:
                start = time.perf_counter()
                result = await self._llm_extraction(in_dir / f"{page_stem(num)}.jpg", ctx)
                duration_ms = round((time.perf_counter() - start) * 1000)
            log.info(
                "LLM extracted page %d of %s with %s: %d rows, %d input / %d output tokens, ~$%.6f, %d ms",
                num,
                task.input_pdf.name,
                self.model,
                len(result.table.rows),
                result.input_tokens,
                result.output_tokens,
                result.estimated_cost,
                duration_ms,
                extra={
                    **ctx,
                    "model": self.model,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "estimated_cost": result.estimated_cost,
                    "duration_ms": duration_ms,
                },
            )
            write_result(out_dir, num, result)

            done += 1
            task.report(self.name, done, len(nums))

        await asyncio.gather(*(bounded(n) for n in nums))

    def execute(self, task: PipelineTask):
        task.step_dir(self.name).mkdir(parents=True, exist_ok=True)
        self.runner.run(self._run_all(task, task.page_nums("cropped")))
