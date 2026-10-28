import tempfile

from pdf2image import convert_from_path, pdfinfo_from_path

from backend.pipeline.models.pipeline_parts import PipelineStep, PipelineTask, page_stem


class ImgExtractor(PipelineStep):
    """Step 1: render every PDF page to ``raw/pNNN.jpg``."""

    name = "raw"

    def __init__(self, dpi: int = 200):
        self.dpi = dpi

    def execute(self, task: PipelineTask):
        output_dir = task.step_dir(self.name)
        output_dir.mkdir(parents=True, exist_ok=True)

        total = pdfinfo_from_path(task.input_pdf)["Pages"]
        nums = [n for n in range(1, total + 1) if task.pages is None or n in task.pages]

        with tempfile.TemporaryDirectory() as tmp:
            for i, num in enumerate(nums, 1):
                (image,) = convert_from_path(
                    task.input_pdf, dpi=self.dpi, first_page=num, last_page=num, output_folder=tmp
                )
                image.save(output_dir / f"{page_stem(num)}.jpg", "JPEG")
                task.report(self.name, i, len(nums))
