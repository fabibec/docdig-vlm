from pipeline.pdf_extractor import pdf_to_img
from pipeline.bird_vocab import get_bird_vocab
from pipeline.shared import PROJECT_ROOT
import multiprocessing
from dotenv import load_dotenv

if __name__ == "__main__":
    multiprocessing.freeze_support()

    load_dotenv()
    get_bird_vocab()

    input_dir = PROJECT_ROOT / "data" / "input"
    for file in input_dir.iterdir():
        if file.suffix == ".pdf":
            pdf_to_img(file)


    from pipeline.table_extractor import extract_printed_table
    table_dir = PROJECT_ROOT / "data" / "output" / "img"

    for file in table_dir.iterdir():
        extract_printed_table(file)

    from pipeline.ovis_ocr import run_ocr
    run_ocr()
