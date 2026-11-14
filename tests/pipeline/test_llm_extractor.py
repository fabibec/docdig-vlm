"""LlmExtractor without network: the OpenAI client / the per-page extraction is replaced by fakes."""

import asyncio
import csv
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from genai_prices import Usage, calc_price

from backend.pipeline.models.llm_responses import ExtractionResult, PageTable, TableRow, Uncertainty
from backend.pipeline.models.pipeline_parts import collect_result, page_stem
from backend.pipeline.models.results import PageGeometry
from backend.pipeline.steps import LlmExtractor
from backend.pipeline.steps.llm_extractor import LlmRefusal
from benchmark import run_benchmark as rb

# The CSV the benchmark reads: row_number + the benchmark columns, by name.
COLUMNS = ["row_number", *rb.COLUMNS]


def table(n_rows: int = 2) -> PageTable:
    rows = [
        TableRow(
            row_number=i,
            ring_no=f"A9040{i}",
            species="Tannenmeise" if i % 2 else "Parus major, juv.",
            sex="m",
            age="Fd",
            date="16.7.",
            time="07:30",
            wing_length_mm="62",
            weight_g="9.5",
            additional_remark="",
        )
        for i in range(1, n_rows + 1)
    ]
    return PageTable(
        rows=rows,
        uncertainties=[
            Uncertainty(row_number=1, column="Espèce", value_read="Tanne", reason="unclear", confidence=0.4)
        ],
        page_confidence=0.8,
    )


@pytest.fixture
def extractor():
    ext = LlmExtractor()
    ext.setup()  # builds the client, no request is sent
    yield ext
    ext.close()


@pytest.fixture
def cropped_task(make_task):
    """A task with two cropped pages (the images are never decoded by the fakes)."""
    task = make_task()
    cropped = task.step_dir("cropped")
    cropped.mkdir(parents=True)
    for n in (1, 2):
        cv2.imwrite(str(cropped / f"{page_stem(n)}.jpg"), np.full((20, 20, 3), 255, np.uint8))
    return task


def fake_extraction(cost: float = 0.01, n_rows: int = 2, seen: list | None = None):
    async def run(image):
        if seen is not None:
            seen.append(image)
        return ExtractionResult(
            source="fake/model", table=table(n_rows), input_tokens=100, output_tokens=50, estimated_cost=cost
        )

    return run


def test_setup_reads_settings_and_prompt(extractor):
    assert extractor.prompt.strip()
    assert "/" in extractor.model
    assert f"{extractor.provider_id}/{extractor.model_ref}" == extractor.model
    assert extractor.concurrency >= 1


def test_execute_writes_json_markdown_and_csv(extractor, cropped_task, monkeypatch):
    seen = []
    monkeypatch.setattr(extractor, "_llm_extraction", fake_extraction(cost=0.0042, seen=seen))
    progress = []
    cropped_task.on_progress = lambda *a: progress.append(a)

    extractor.execute(cropped_task)

    out = cropped_task.step_dir("processed")
    assert sorted(p.name for p in out.iterdir()) == [
        "p001.csv",
        "p001.json",
        "p001.md",
        "p002.csv",
        "p002.json",
        "p002.md",
    ]
    assert sorted(p.name for p in seen) == ["p001.jpg", "p002.jpg"]
    assert sorted(progress) == [("processed", 1, 2), ("processed", 2, 2)]

    result = ExtractionResult.model_validate_json((out / "p001.json").read_text(encoding="utf-8"))
    assert result.estimated_cost == 0.0042
    assert result.table == table()

    with open(out / "p001.csv", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)  # comma separated (quoted where needed), unlike the `;` reference CSVs
        header, *rows = list(reader)
    assert header == COLUMNS
    assert rows[0] == ["1", "A90401", "Tannenmeise", "m", "Fd", "16.7.", "07:30", "62", "9.5", ""]
    assert rows[1][2] == "Parus major, juv."

    md = (out / "p001.md").read_text(encoding="utf-8").splitlines()
    assert md[0].startswith("| row_number") and "species" in md[0]
    assert len(md) == 2 + 2  # header, separator, two rows
    assert "07:30" in md[2] and "A90401" in md[2]  # disable_numparse keeps values as written
    assert "Parus major, juv." in md[3]


def test_execute_respects_page_filter(extractor, cropped_task, monkeypatch):
    seen = []
    monkeypatch.setattr(extractor, "_llm_extraction", fake_extraction(seen=seen))
    cropped_task.pages = [2]

    extractor.execute(cropped_task)

    assert [p.name for p in seen] == ["p002.jpg"]
    assert sorted(p.name for p in cropped_task.step_dir("processed").glob("*.json")) == ["p002.json"]


def test_execute_without_crops_writes_nothing(extractor, make_task, monkeypatch):
    monkeypatch.setattr(extractor, "_llm_extraction", fake_extraction())
    task = make_task()

    extractor.execute(task)

    assert list(task.step_dir("processed").iterdir()) == []


@pytest.mark.xfail(
    raises=IndexError,
    strict=True,
    reason="bug: llm_extractor builds the CSV header from rows[0]; use list(TableRow.model_fields) instead",
)
def test_page_with_no_rows_writes_header_only_csv(extractor, cropped_task, monkeypatch):
    monkeypatch.setattr(extractor, "_llm_extraction", fake_extraction(n_rows=0))
    cropped_task.pages = [1]

    extractor.execute(cropped_task)

    with open(cropped_task.step_dir("processed") / "p001.csv", encoding="utf-8", newline="") as f:
        assert list(csv.reader(f)) == [COLUMNS]


def test_processed_output_feeds_collect_result(extractor, cropped_task, monkeypatch):
    monkeypatch.setattr(extractor, "_llm_extraction", fake_extraction(cost=0.5))
    geom = PageGeometry(width=20, height=20, header=(0, 5), row_bounds=[(5, 10), (10, 15)], col_bounds=[0, 10, 20])
    for n in (1, 2):
        (cropped_task.step_dir("cropped") / f"{page_stem(n)}.geom.json").write_text(geom.model_dump_json())

    extractor.execute(cropped_task)
    result = collect_result(cropped_task)

    assert [(p.num, p.estimated_cost, len(p.table.rows)) for p in result.pages] == [(1, 0.5, 2), (2, 0.5, 2)]


def test_concurrency_limit_is_respected(extractor, make_task, monkeypatch):
    task = make_task()
    cropped = task.step_dir("cropped")
    cropped.mkdir(parents=True)
    for n in range(1, 7):
        (cropped / f"{page_stem(n)}.jpg").write_bytes(b"")
    state = {"now": 0, "max": 0}
    inner = fake_extraction()

    async def slow(image):
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return await inner(image)

    monkeypatch.setattr(extractor, "_llm_extraction", slow)
    extractor.concurrency = 2

    extractor.execute(task)

    assert state["max"] == 2
    assert len(list(task.step_dir("processed").glob("*.json"))) == 6


def test_execute_can_run_twice_on_the_same_runner(extractor, cropped_task, monkeypatch):
    monkeypatch.setattr(extractor, "_llm_extraction", fake_extraction())
    extractor.execute(cropped_task)
    extractor.execute(cropped_task)
    assert len(list(cropped_task.step_dir("processed").glob("*.json"))) == 2


# ---------------------------------------------------------------- _llm_extraction with a fake client


class FakeCompletions:
    def __init__(self, completion):
        self.completion = completion
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.completion


def completion(parsed=None, refusal=None, prompt_tokens=1000, completion_tokens=400, reasoning_tokens=100):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=refusal))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
        ),
    )


def use_fake_client(extractor, monkeypatch, comp) -> FakeCompletions:
    fake = FakeCompletions(comp)
    monkeypatch.setattr(extractor.client.beta.chat, "completions", fake)
    monkeypatch.setattr(extractor, "model", "openai/gpt-4o")
    monkeypatch.setattr(extractor, "provider_id", "openai")
    monkeypatch.setattr(extractor, "model_ref", "gpt-4o")
    return fake


def test_llm_extraction_builds_request_and_prices_tokens(extractor, cropped_task, monkeypatch):
    fake = use_fake_client(extractor, monkeypatch, completion(parsed=table()))
    image = cropped_task.step_dir("cropped") / "p001.jpg"

    result = extractor.runner.run(extractor._llm_extraction(image))

    (call,) = fake.calls
    assert call["model"] == "openai/gpt-4o"
    assert call["response_format"] is PageTable
    assert call["messages"][0] == {"role": "system", "content": extractor.prompt}
    image_part = call["messages"][1]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert call["reasoning_effort"] == extractor.reasoning_effort
    assert call["seed"] == extractor.seed

    assert result.source == "openai/gpt-4o"
    assert result.table == table()
    assert (result.input_tokens, result.output_tokens) == (1000, 500)  # reasoning tokens count as output
    expected = calc_price(Usage(input_tokens=1000, output_tokens=500), model_ref="gpt-4o", provider_id="openai")
    assert result.estimated_cost == pytest.approx(float(expected.total_price))
    assert result.estimated_cost > 0


@pytest.mark.parametrize(
    ("parsed", "refusal", "match"),
    [(None, "I can't help with that", "can't help"), (None, None, "no parsed output"), (table(), "nope", "nope")],
    ids=["refusal", "no-output", "refusal-wins"],
)
def test_refusal_or_missing_output_raises_llm_refusal(parsed, refusal, match, extractor, cropped_task, monkeypatch):
    use_fake_client(extractor, monkeypatch, completion(parsed=parsed, refusal=refusal))
    image = cropped_task.step_dir("cropped") / "p001.jpg"

    with pytest.raises(LlmRefusal, match=match) as err:
        extractor.runner.run(extractor._llm_extraction(image))

    assert "p001.jpg" in str(err.value)
    assert isinstance(err.value, RuntimeError)  # pool workers catch it as a normal error


def test_refusal_aborts_execute_and_writes_no_json(extractor, cropped_task, monkeypatch):
    use_fake_client(extractor, monkeypatch, completion(refusal="no"))
    cropped_task.pages = [1]

    with pytest.raises(LlmRefusal):
        extractor.execute(cropped_task)

    assert not (cropped_task.step_dir("processed") / "p001.json").exists()


def test_encode_image_returns_data_url(tmp_path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8abc")
    part = LlmExtractor._encode_image(img)
    assert part == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9hhYmM=", "detail": "high"}}


def test_extraction_result_json_round_trip():
    r = ExtractionResult(source="a/b", table=table(), input_tokens=1, output_tokens=2, estimated_cost=0.1)
    assert ExtractionResult.model_validate_json(r.model_dump_json()) == r
    assert json.loads(r.model_dump_json())["estimated_cost"] == 0.1
