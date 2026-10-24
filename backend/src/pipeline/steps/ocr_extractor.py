import re

from PIL import Image
from vllm import LLM

from backend.src.pipeline.directories import PROJECT_ROOT, PROMPT_DIR
from backend.src.pipeline.models.pipeline_parts import PipelineStep, PipelineTask
from backend.src.pipeline.settings import settings


class OcrExtractor(PipelineStep):
    def setup(self):
        self.model = LLM(
            model=settings.ocr_model,
            tensor_parallel_size=1,
            gpu_memory_utilization=0.8,
            gdn_prefill_backend="triton",
            max_num_seqs=1,
            max_num_batched_tokens=4096,
            download_dir=str(PROJECT_ROOT / "models"),
        )

        with (PROMPT_DIR / "ocr_prompt.txt").open("r") as f:
            prompt = f.read()

        self.prompt = self.model.get_tokenizer().apply_chat_template(
            [
                {
                    "role": "user",
                    "content": [{"type": "image"}, {"type": "text", "text": prompt}],
                }
            ],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )

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

    def execute(self, task: PipelineTask):
        provider_id, model_ref = settings.ocr_model.split("/")

        input_dir = task.last_step_output_directory
        output_dir = task.output_directory / "llms" / provider_id / model_ref
        output_dir.mkdir(parents=True, exist_ok=True)
        task.last_step_output_directory = output_dir

        # Natural sort so that scan_page_2 comes before scan_page_10
        img_files = sorted(
            (f for f in input_dir.iterdir() if not f.is_dir()),
            key=lambda f: [
                int(t) if t.isdigit() else t for t in re.split(r"(\d+)", f.name)
            ],
        )
        images = [Image.open(fname).convert("RGB") for fname in img_files]

        vllm_inputs = [
            {
                "prompt": self.prompt,
                "multi_modal_data": {"image": image},
                "mm_processor_kwargs": {
                    "images_kwargs": {
                        "min_pixels": 448 * 448,
                        "max_pixels": 2880 * 2880,
                    }
                },
            }
            for image in images
        ]

        outputs = self.model.generate(vllm_inputs)

        markdowns = []
        for output in outputs:
            text = output.outputs[0].text.strip()
            text = OcrExtractor._clean_truncated_repeats(text)
            markdowns.append(text)

        for img_file, md in zip(img_files, markdowns, strict=True):
            out_file = output_dir / f"{img_file.stem}.md"
            with out_file.open("w", encoding="utf-8") as f:
                f.write(md)
