from backend.pipeline.directories import INPUT_DIR, OUTPUT_DIR
from backend.pipeline.models.pipeline_parts import Pipeline, PipelineTask
from backend.pipeline.steps import ImgExtractor, TableExtractor, LlmExtractor


if __name__ == "__main__":
    extraction_pipeline = Pipeline()
    extraction_pipeline.add_step(ImgExtractor()).add_step(TableExtractor()).add_step(
        LlmExtractor()
    ).setup()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    task = PipelineTask(
        files=[file for file in INPUT_DIR.iterdir()], output_directory=OUTPUT_DIR
    )
    extraction_pipeline.run_from_task(task)
