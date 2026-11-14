"""backend/api/processing.py without the process pool: persistence, failures, restart recovery.

``worker.run_doc`` is replaced by a fake that returns a canned ``DocResult``; it runs in the
default thread pool instead of the spawn process pool, so no pipeline or LLM is ever touched.
"""

import asyncio

import pytest

import backend.api.processing as api_processing
from backend.api.enums import ColumnKey, DocStatus, FlagSource
from backend.api.models import Doc, Flag, Page, Row
from backend.api.processing import Processor, processor
from backend.pipeline.models.llm_responses import ExtractionResult, PageTable, TableRow, Uncertainty
from backend.pipeline.models.pipeline_parts import PipelineTask, collect_result
from backend.pipeline.models.results import DocResult, PageGeometry, PageResult
from tests.api.helpers import COL_BOUNDS

GEOM = PageGeometry(
    width=1100,
    height=800,
    header=(0, 50),
    row_bounds=[(50, 100), (100, 150)],
    col_bounds=COL_BOUNDS,
)


def _row(n: int, **values: str) -> TableRow:
    return TableRow(row_number=n, **({c.value: "" for c in ColumnKey} | values))


def _page(num: int, rows=(), uncertainties=(), cost: float | None = None) -> PageResult:
    table = PageTable(rows=list(rows), uncertainties=list(uncertainties), page_confidence=0.8)
    return PageResult(num=num, geometry=GEOM, table=table, estimated_cost=cost)


# ---------- _persist ----------


def test_persist_stores_pages_rows_flags_and_cost(api, factory):
    doc = factory.doc(factory.batch(), status=DocStatus.processing)
    result = DocResult(
        pages=[
            _page(1, [_row(1, ring_no=" A1 ", weight_g="5"), _row(2, weight_g="abc")], cost=0.001),
            _page(
                2,
                [_row(1, time="8h"), _row(9, species="Pica")],  # row 9 has no geometry
                [Uncertainty(row_number=1, column="Espèce", value_read="?", reason="smudge", confidence=0.3)],
                cost=0.002,
            ),
        ]
    )

    api.run(processor._persist, doc.id, result)

    stored = factory.get(Doc, doc.id)
    assert stored.status is DocStatus.review
    assert stored.num_pages == 2
    assert stored.open_flags == 3
    assert stored.total_cost == pytest.approx(0.003)
    pages = sorted(factory.all(Page, doc_id=doc.id), key=lambda p: p.num)
    assert [(p.num, p.estimated_cost, p.page_confidence) for p in pages] == [(1, 0.001, 0.8), (2, 0.002, 0.8)]
    assert pages[0].col_bounds == GEOM.col_bounds
    rows = sorted(factory.all(Row, page_id=pages[0].id), key=lambda r: r.idx)
    assert [(r.idx, r.y0, r.y1) for r in rows] == [(1, 50, 100), (2, 100, 150)]
    assert rows[0].values["ring_no"] == "A1"
    assert rows[0].corrections == {}
    (orphan,) = [r for r in factory.all(Row, page_id=pages[1].id) if r.idx == 9]
    assert (orphan.y0, orphan.y1) == (0, 0)
    flags = factory.all(Flag, doc_id=doc.id)
    assert sorted((f.page_num, f.row_idx, f.code or f.source.value) for f in flags) == [
        (1, 2, "not_a_number"),
        (2, 1, "llm"),
        (2, 1, "time_format"),
    ]
    assert api.event_types() == ["doc.status", "batch.updated", "review.pending"]


def test_persist_without_flags_marks_doc_done(api, factory):
    doc = factory.doc(factory.batch(), status=DocStatus.processing, num_pages=1)
    api.run(processor._persist, doc.id, DocResult(pages=[_page(1, [_row(1, weight_g="12")], cost=0.0)]))
    stored = factory.get(Doc, doc.id)
    assert stored.status is DocStatus.done
    assert stored.open_flags == 0
    assert stored.total_cost == 0.0


def test_persist_without_pages_marks_doc_error(api, factory):
    doc = factory.doc(factory.batch(), status=DocStatus.processing)
    api.run(processor._persist, doc.id, DocResult(pages=[]))
    stored = factory.get(Doc, doc.id)
    assert stored.status is DocStatus.error
    assert stored.error == "no page produced a result"


@pytest.mark.parametrize(
    ("costs", "total"),
    [
        ([0.002, None, 0.004, None], 0.006),  # pages without a cost are skipped
        ([0.001, 0.002, 0.001], 0.004),
        ([1 / 3, 1 / 3], 0.666667),  # rounded to 6 decimals
        ([None, None], None),  # no cost known -> stays unknown
    ],
)
def test_total_cost_is_sum_of_page_costs(api, factory, costs, total):
    doc = factory.doc(factory.batch(), status=DocStatus.processing)
    api.run(processor._persist, doc.id, DocResult(pages=[_page(i + 1, cost=c) for i, c in enumerate(costs)]))
    out = api.client.get(f"/api/docs/{doc.slug}").json()
    assert out["total_cost"] == total


def test_reprocessing_replaces_pages_rows_and_all_flags(api, factory):
    p = factory.processed_doc()
    note = factory.flag(p.doc, 1, 3, source=FlagSource.user, message="mine")

    api.run(processor._persist, p.doc.id, DocResult(pages=[_page(1, [_row(1, species="Pica")])]))

    assert factory.get(Flag, note.id) is None
    assert factory.all(Flag, doc_id=p.doc.id) == []
    assert factory.get(Row, p.rows[2].id) is None
    (page,) = factory.all(Page, doc_id=p.doc.id)
    assert [r.idx for r in factory.all(Row, page_id=page.id)] == [1]
    assert factory.get(Doc, p.doc.id).status is DocStatus.done


# ---------- _fail / _process ----------


def test_fail_marks_doc_error_with_message(api, factory):
    doc = factory.doc(factory.batch(), status=DocStatus.processing)
    api.run(processor._fail, doc.id, RuntimeError("pdf is broken"))
    stored = factory.get(Doc, doc.id)
    assert stored.status is DocStatus.error
    assert stored.error == "RuntimeError: pdf is broken"
    assert api.event_types() == ["doc.status", "batch.updated", "review.pending"]


def test_fail_truncates_long_errors(api, factory):
    doc = factory.doc(factory.batch())
    api.run(processor._fail, doc.id, ValueError("x" * 5000))
    assert len(factory.get(Doc, doc.id).error) == 2000


def _run_process(api, monkeypatch, doc_id, run_doc):
    monkeypatch.setattr(api_processing.worker, "run_doc", run_doc)
    monkeypatch.setattr(processor, "_pool", None)  # None -> loop's default thread pool

    async def go():
        monkeypatch.setattr(processor, "_loop", asyncio.get_running_loop())
        await processor._process(doc_id)

    api.run(go)


def test_process_runs_worker_and_persists_result(api, factory, monkeypatch):
    doc = factory.doc(factory.batch(slug="b"))
    calls = []

    def fake_run_doc(doc_id, input_pdf, out_dir):
        calls.append((doc_id, input_pdf, out_dir))
        return DocResult(pages=[_page(1, [_row(1, weight_g="12")], cost=0.01)]).model_dump_json()

    _run_process(api, monkeypatch, doc.id, fake_run_doc)

    doc_dir = api.batches_dir / "b" / doc.slug
    assert calls == [(doc.id, str(doc_dir / "input" / "scan.pdf"), str(doc_dir))]
    assert factory.get(Doc, doc.id).status is DocStatus.done
    statuses = [e.doc.status for e in api.events if e.type == "doc.status"]
    assert statuses == [DocStatus.processing, DocStatus.done]


def test_process_failure_marks_doc_error(api, factory, monkeypatch):
    doc = factory.doc(factory.batch())

    def broken(*_):
        raise OSError("disk full")

    _run_process(api, monkeypatch, doc.id, broken)

    stored = factory.get(Doc, doc.id)
    assert stored.status is DocStatus.error
    assert stored.error == "OSError: disk full"


# ---------- progress ----------


def test_progress_updates_page_count_and_broadcasts_doc_event(api, factory):
    doc = factory.doc(factory.batch(), status=DocStatus.processing)
    api.run(processor._on_progress, doc.id, "raw", 1, 4)

    assert factory.get(Doc, doc.id).num_pages == 4
    (ev,) = api.events
    assert ev.type == "doc.status"
    assert (ev.progress.step.value, ev.progress.page, ev.progress.pages) == ("raw", 1, 4)


# ---------- restart recovery ----------


class _NoPool:
    def __init__(self, **_):
        pass

    def shutdown(self, **_):
        pass


def test_start_requeues_processing_and_queued_docs(api, factory, monkeypatch):
    batch = factory.batch()
    queued = factory.doc(batch, "a.pdf", status=DocStatus.queued)
    running = factory.doc(batch, "b.pdf", status=DocStatus.processing)
    factory.doc(batch, "c.pdf", status=DocStatus.done)
    factory.doc(batch, "d.pdf", status=DocStatus.error)
    monkeypatch.setattr(api_processing, "ProcessPoolExecutor", _NoPool)

    fresh = Processor(pool_size=1)
    enqueued: list[list[int]] = []
    monkeypatch.setattr(fresh, "enqueue", enqueued.append)
    api.run(fresh.start)
    api.run(fresh.stop)
    fresh._reader.join(timeout=5)

    assert enqueued == [[queued.id, running.id]]
    assert factory.get(Doc, running.id).status is DocStatus.queued
    assert not fresh._reader.is_alive()


# ---------- pipeline result -> DocResult ----------


def test_collect_result_forwards_page_cost(tmp_path):
    out = tmp_path / "doc"
    (out / "cropped").mkdir(parents=True)
    (out / "processed").mkdir()
    (out / "cropped" / "p001.geom.json").write_text(GEOM.model_dump_json())
    extraction = ExtractionResult(
        source="test-model",
        table=PageTable(rows=[_row(1)], page_confidence=0.9),
        input_tokens=100,
        output_tokens=50,
        estimated_cost=0.0025,
    )
    (out / "processed" / "p001.json").write_text(extraction.model_dump_json())
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    result = collect_result(PipelineTask(input_pdf=pdf, output_directory=out))

    (page,) = result.pages
    assert page.num == 1
    assert page.estimated_cost == 0.0025
    assert page.geometry == GEOM
