import asyncio
import base64
import ssl

import truststore
from genai_prices import Usage, calc_price
from openai import AsyncOpenAI, DefaultAsyncHttpx2Client
from pydantic import FilePath
from tabulate import tabulate

from backend.pipeline.models.llm_responses import ExtractionResult, PageTable
from backend.pipeline.models.pipeline_parts import PipelineStep, PipelineTask, page_stem
from backend.pipeline.settings import settings


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

    async def _llm_extraction(self, image: FilePath) -> ExtractionResult:
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

        msg = completion.choices[0].message
        if msg.refusal or msg.parsed is None:
            raise LlmRefusal(f"{image.name}: {msg.refusal or 'no parsed output'}")

        u = completion.usage
        input_tokens = u.prompt_tokens
        output_tokens = u.completion_tokens + u.completion_tokens_details.reasoning_tokens

        price_data = calc_price(
            Usage(input_tokens=input_tokens, output_tokens=output_tokens),
            model_ref=self.model_ref,
            provider_id=self.provider_id,
        )

        return ExtractionResult(
            source=self.model,
            table=msg.parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=round(price_data.total_price, 3),
        )

    async def _run_all(self, task: PipelineTask, nums: list[int]):
        sem = asyncio.Semaphore(self.concurrency)
        in_dir, out_dir = task.step_dir("cropped"), task.step_dir(self.name)
        done = 0

        async def bounded(num: int):
            nonlocal done
            async with sem:
                result = await self._llm_extraction(in_dir / f"{page_stem(num)}.jpg")
            (out_dir / f"{page_stem(num)}.json").write_text(
                result.model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8"
            )
            (out_dir / f"{page_stem(num)}.md").write_text(
                tabulate(result.table.model_dump()["rows"], headers="keys", tablefmt="pipe", disable_numparse=True),
                encoding="utf-8",
            )
            done += 1
            task.report(self.name, done, len(nums))

        await asyncio.gather(*(bounded(n) for n in nums))

    def execute(self, task: PipelineTask):
        task.step_dir(self.name).mkdir(parents=True, exist_ok=True)
        self.runner.run(self._run_all(task, task.page_nums("cropped")))
