import tempfile
from pathlib import Path
from pipeline.shared import PROJECT_ROOT

from pdf2image import convert_from_path

def pdf_to_img(file: Path):
    output_dir = PROJECT_ROOT / "data" / "output" / "img"
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as path:
        images = convert_from_path(file, output_folder=path)

        for idx, image in enumerate(images):
            image.save(output_dir / f"{file.stem}_page_{idx}.jpg", "JPEG")
