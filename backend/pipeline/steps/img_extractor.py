import logging
import tempfile
import time

from pdf2image import convert_from_path, pdfinfo_from_path

from backend.pipeline.models.pipeline_parts import PipelineStep, PipelineTask, page_stem

log = logging.getLogger(__name__)


class ImgExtractor(PipelineStep):
    """Step 1: render every PDF page to ``raw/pNNN.jpg``."""

    name = "raw"

    def __init__(self, dpi: int = 200):
        self.dpi = dpi

    def execute(self, task: PipelineTask):
        output_dir = task.step_dir(self.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        pdf = task.input_pdf.name

        total = pdfinfo_from_path(str(task.input_pdf))["Pages"]
        nums = [n for n in range(1, total + 1) if task.pages is None or n in task.pages]

        start = time.perf_counter()
        with tempfile.TemporaryDirectory() as tmp:
            for i, num in enumerate(nums, 1):
                page_start = time.perf_counter()
                (image,) = convert_from_path(
                    task.input_pdf, dpi=self.dpi, first_page=num, last_page=num, output_folder=tmp
                )
                image.save(output_dir / f"{page_stem(num)}.jpg", "JPEG")
                log.debug(
                    "Rendered page %d of %s (%dx%d px)",
                    num,
                    pdf,
                    image.width,
                    image.height,
                    extra={
                        "pdf": pdf,
                        "step": self.name,
                        "page": num,
                        "duration_ms": round((time.perf_counter() - page_start) * 1000),
                    },
                )
                task.report(self.name, i, len(nums))

        log.info(
            "Rendered %d of %d page(s) of %s at %d dpi",
            len(nums),
            total,
            pdf,
            self.dpi,
            extra={
                "pdf": pdf,
                "step": self.name,
                "pages": len(nums),
                "dpi": self.dpi,
                "duration_ms": round((time.perf_counter() - start) * 1000),
            },
        )
