import asyncio
import base64
import ssl
import sys

from pydantic import DirectoryPath, FilePath
import truststore
from genai_prices import Usage, calc_price
from openai import AsyncOpenAI, DefaultAsyncHttpx2Client
from tabulate import tabulate

from backend.pipeline.models.llm_responses import ExtractionResult, PageTable
from backend.pipeline.models.pipeline_parts import PipelineStep, PipelineTask
from backend.pipeline.settings import settings


class LlmExtractor(PipelineStep):
    def setup(self):
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

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
        self._sem = None


        with settings.llm_prompt_file.open("r", encoding="utf-8") as f:
            self.prompt = f.read()

    def _encode_image(path, mime="image/jpeg", detail="high"):
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}", "detail": detail},
        }

    async def _llm_extraction(self, image: FilePath, output_dir: DirectoryPath):
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
        if msg.refusal:
            # model refused; msg.parsed is None
            print(msg.refusal)
            sys.exit(1)
        else:
            result = msg.parsed.model_dump()

        u = completion.usage
        input_tokens = u.prompt_tokens
        output_tokens = (
            u.completion_tokens + u.completion_tokens_details.reasoning_tokens
        )

        price_data = calc_price(
            Usage(input_tokens=input_tokens, output_tokens=output_tokens),
            model_ref=self.model_ref,
            provider_id=self.provider_id,
        )

        result = ExtractionResult(
            source="",
            table=msg.parsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=round(price_data.total_price, 3),
        )

        (output_dir / f"{image.stem}.json").write_text(
            result.model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (output_dir / f"{image.stem}.md").write_text(
            tabulate(result.table.model_dump()["rows"], tablefmt="pipe"),
            encoding="utf-8",
        )

    async def _run_all(self, images, output_dir):
        sem = asyncio.Semaphore(self.concurrency)

        async def bounded(img):
            async with sem:
                await self._llm_extraction(img, output_dir)

        await asyncio.gather(*(bounded(img) for img in images))

    def execute(self, task: PipelineTask):
        input_dir = task.last_step_output_directory
        output_dir = task.output_directory / "llms" / self.provider_id / self.model_ref
        output_dir.mkdir(parents=True, exist_ok=True)
        task.last_step_output_directory = output_dir

        asyncio.run(self._run_all(
            sorted(p for p in input_dir.iterdir() if p.is_file()), output_dir
        ))







