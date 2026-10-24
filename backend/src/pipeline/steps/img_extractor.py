import tempfile

from pdf2image import convert_from_path

from backend.src.pipeline.models.pipeline_parts import PipelineStep, PipelineTask


class ImgExtractor(PipelineStep):
    def setup(self):
        return super().setup()

    def execute(self, task: PipelineTask):
        output_dir = task.output_directory / "imgs"
        output_dir.mkdir(parents=True, exist_ok=True)
        task.last_step_output_directory = output_dir

        with tempfile.TemporaryDirectory() as path:
            for file in task.files:
                images = convert_from_path(file, output_folder=path)

                for idx, image in enumerate(images):
                    image.save(output_dir / f"{file.stem}_p{idx:02d}.jpg", "JPEG")
