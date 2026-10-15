from PIL import Image
from html_to_markdown import convert
from pipeline.shared import PROJECT_ROOT
from vllm import LLM, SamplingParams

class OvisOCR2Parser:
    def __init__(self, model_name_or_path: str):
        self.model = LLM(
            model=model_name_or_path,
            tensor_parallel_size=1,
            gpu_memory_utilization=0.2,
            gdn_prefill_backend="triton",
            max_num_seqs=4,
            max_num_batched_tokens=4096,
        )

        prompt = (
            "\nExtract all readable content from the image in natural human reading order and output the result as a single Markdown document. For charts or images, represent them using an HTML image tag: <"
            + 'img src="images/bbox_{left}_{top}_{right}_{bottom}.jpg" />, where left, top, right, bottom are bounding box coordinates scaled to [0, 1000). Format formulas as LaTeX. Format tables as HTML: <table>...</table>. Transcribe all other text as standard Markdown. Preserve the original text without translation or paraphrasing.'
        )

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

        self.sampling_params = SamplingParams(max_tokens=16384, temperature=0.0)

    def _clean_truncated_repeats(
        self,
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

    def parse(
        self, images: list[Image.Image], filter_imgtags: bool = True
    ) -> list[str]:
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

        outputs = self.model.generate(vllm_inputs, self.sampling_params)

        markdowns = []
        for output in outputs:
            text = output.outputs[0].text.strip()
            if filter_imgtags:
                text = "\n\n".join(
                    block
                    for block in text.split("\n\n")
                    if not block.strip().startswith('<img src="images/bbox_')
                )
            markdowns.append(self._clean_truncated_repeats(text))

        return markdowns


def run_ocr():
    parser = OvisOCR2Parser("ATH-MaaS/OvisOCR2")

    input_path = PROJECT_ROOT / "data" / "output" / "table"
    output_path = PROJECT_ROOT / "data" / "output" / "ocr"
    output_path.mkdir(exist_ok=True, parents=True)

    images = [
        Image.open(
            fname
        ) for fname in input_path.iterdir()
    ]

    markdowns = parser.parse(images)

    for i, md in enumerate(markdowns):
        f = open(f"ocr{i}.md", "w")
        f.write(convert(md).content)
        f.close()

