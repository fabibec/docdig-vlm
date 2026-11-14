"""/api/docs/{slug}: detail, flags, rows, resolve, notes, undo, rename, file + artifacts."""

import pytest

from backend.api.enums import ColumnKey, DocStatus, FlagSource
from backend.api.models import Flag, Row
from tests.api.helpers import empty_values, pdf_bytes

# ---------- detail / flags / rows ----------


def test_doc_detail_lists_pages_with_open_flags(api, factory):
    p = factory.processed_doc()
    factory.page(p.doc, 2, page_confidence=0.5)

    body = api.client.get(f"/api/docs/{p.doc.slug}").json()

    assert body["slug"] == p.doc.slug
    assert body["batch_slug"] == p.batch.slug
    assert body["batch_name"] == "Processed"
    assert body["status"] == "review"
    assert body["open_flags"] == 3
    assert body["pages"] == [
        {"num": 1, "open_flags": 3, "page_confidence": 0.9},
        {"num": 2, "open_flags": 0, "page_confidence": 0.5},
    ]
    assert body["artifact_url"] == f"/api/docs/{p.doc.slug}/artifacts/{{state}}/page/{{num}}"


def test_doc_detail_of_processing_doc_lists_known_page_numbers(api, factory):
    doc = factory.doc(factory.batch(), status=DocStatus.processing, num_pages=2)
    pages = api.client.get(f"/api/docs/{doc.slug}").json()["pages"]
    assert pages == [
        {"num": 1, "open_flags": 0, "page_confidence": None},
        {"num": 2, "open_flags": 0, "page_confidence": None},
    ]


def test_unknown_doc_is_404(api):
    for url in ("/api/docs/nope", "/api/docs/nope/flags", "/api/docs/nope/file", "/api/docs/nope/page/1/rows"):
        assert api.client.get(url).status_code == 404, url


def test_list_flags_filters_by_open(api, factory):
    p = factory.processed_doc()
    factory.flag(p.doc, 1, 3, resolved=True, message="done already")

    all_flags = api.client.get(f"/api/docs/{p.doc.slug}/flags").json()
    open_flags = api.client.get(f"/api/docs/{p.doc.slug}/flags", params={"open": True}).json()
    resolved = api.client.get(f"/api/docs/{p.doc.slug}/flags", params={"open": False}).json()

    assert [(f["page"], f["row"]) for f in all_flags] == [(1, 1), (1, 1), (1, 2), (1, 3)]
    assert len(open_flags) == 3 and not any(f["resolved"] for f in open_flags)
    assert [f["message"] for f in resolved] == ["done already"]
    first = all_flags[0]
    assert first["column"] == "weight_g"
    assert first["source"] == "rule"
    assert first["severity"] == "error"
    assert first["code"] == "not_a_number"


def test_page_rows_returns_merged_values_and_geometry(api, factory):
    doc = factory.doc(factory.batch(), status=DocStatus.done)
    page = factory.page(doc, width=1100, height=800)
    factory.row(page, 2, {"species": "Parus"}, y0=150, y1=200)
    factory.row(page, 1, {"species": "Pica", "sex": "m"}, corrections={"sex": "f"}, y0=100, y1=150)

    body = api.client.get(f"/api/docs/{doc.slug}/page/1/rows").json()

    assert (body["num"], body["width"], body["height"]) == (1, 1100, 800)
    assert body["columns"] == [c.value for c in ColumnKey]
    assert body["column_x"]["ring_no"] == [0, 100]
    assert body["column_x"]["weight_g"] == [700, 800]
    assert body["column_x"]["additional_remark"] == [800, 1100]  # spans the last 3 printed columns
    assert [r["idx"] for r in body["rows"]] == [1, 2]
    row1 = body["rows"][0]
    assert (row1["y0"], row1["y1"]) == (100, 150)
    assert row1["values"]["species"] == "Pica"
    assert row1["values"]["sex"] == "f"
    assert row1["corrected"] == ["sex"]
    assert set(row1["values"]) == {c.value for c in ColumnKey}


def test_page_rows_with_too_few_grid_lines_fall_back_to_page_width(api, factory):
    doc = factory.doc(factory.batch(), status=DocStatus.done)
    factory.page(doc, width=500, col_bounds=[0, 250])
    column_x = api.client.get(f"/api/docs/{doc.slug}/page/1/rows").json()["column_x"]
    assert column_x["ring_no"] == [0, 250]
    assert column_x["species"] == [0, 500]


def test_page_rows_of_unprocessed_page_is_404(api, factory):
    doc = factory.doc(factory.batch(), status=DocStatus.queued)
    res = api.client.get(f"/api/docs/{doc.slug}/page/1/rows")
    assert res.status_code == 404
    assert res.json()["detail"] == "page not processed"


# ---------- resolve ----------


def test_resolve_row_stores_corrections_and_resolves_its_flags(api, factory):
    p = factory.processed_doc()
    url = f"/api/docs/{p.doc.slug}/page/1/rows/1/resolve"

    res = api.client.post(url, json={"values": {"weight_g": " 12.5 ", "time": "08:00"}})

    assert res.status_code == 200
    body = res.json()
    assert sorted(body["resolved_flag_ids"]) == sorted(f.id for f in p.flags[:2])
    assert body["doc"]["open_flags"] == 1
    assert body["doc"]["status"] == "review"
    assert factory.get(Row, p.rows[0].id).corrections == {"weight_g": "12.5", "time": "08:00"}
    assert factory.get(Row, p.rows[0].id).values["weight_g"] == "1x"  # machine output untouched
    assert factory.get(Flag, p.flags[2].id).resolved is False  # other rows untouched
    assert api.event_types() == ["doc.status", "batch.updated", "review.pending"]


def test_resolve_row_with_empty_body_accepts_machine_values(api, factory):
    p = factory.processed_doc()
    res = api.client.post(f"/api/docs/{p.doc.slug}/page/1/rows/2/resolve", json={})
    assert res.json()["resolved_flag_ids"] == [p.flags[2].id]
    assert factory.get(Row, p.rows[1].id).corrections == {}


def test_resolving_last_flags_marks_doc_done(api, factory):
    p = factory.processed_doc()
    api.client.post(f"/api/docs/{p.doc.slug}/page/1/rows/1/resolve", json={})
    body = api.client.post(f"/api/docs/{p.doc.slug}/page/1/rows/2/resolve", json={}).json()
    assert body["doc"]["status"] == "done"
    assert body["doc"]["open_flags"] == 0


def test_undo_restores_row_exactly(api, factory):
    """Undo = send the previous values back + reopen the resolved flags."""
    p = factory.processed_doc()
    slug = p.doc.slug
    before = api.client.get(f"/api/docs/{slug}/page/1/rows").json()["rows"][0]

    resolved = api.client.post(
        f"/api/docs/{slug}/page/1/rows/1/resolve", json={"values": {"weight_g": "12", "species": "Parus ater"}}
    ).json()
    api.client.post(
        f"/api/docs/{slug}/page/1/rows/1/resolve",
        json={"values": {"weight_g": before["values"]["weight_g"], "species": before["values"]["species"]}},
    )
    reopened = api.client.post(f"/api/docs/{slug}/flags/reopen", json={"ids": resolved["resolved_flag_ids"]})

    after = api.client.get(f"/api/docs/{slug}/page/1/rows").json()["rows"][0]
    assert after == before
    assert after["corrected"] == []
    assert factory.get(Row, p.rows[0].id).corrections == {}
    assert reopened.json()["doc"]["open_flags"] == 3


def test_resolve_unknown_row_is_404(api, factory):
    p = factory.processed_doc()
    assert api.client.post(f"/api/docs/{p.doc.slug}/page/1/rows/99/resolve", json={}).status_code == 404
    assert api.client.post(f"/api/docs/{p.doc.slug}/page/9/rows/1/resolve", json={}).status_code == 404


def test_resolve_rejects_unknown_column(api, factory):
    p = factory.processed_doc()
    res = api.client.post(f"/api/docs/{p.doc.slug}/page/1/rows/1/resolve", json={"values": {"bogus": "1"}})
    assert res.status_code == 422


def test_resolve_page_saves_rows_and_resolves_all_page_flags(api, factory):
    p = factory.processed_doc()
    factory.page(p.doc, 2)
    other_page_flag = factory.flag(p.doc, 2, 1)
    factory.refresh_flags(p.doc)

    res = api.client.post(
        f"/api/docs/{p.doc.slug}/page/1/resolve",
        json={"rows": {"1": {"weight_g": "12"}, "3": {"weight_g": "95"}}},
    )

    assert res.status_code == 200
    assert sorted(res.json()["resolved_flag_ids"]) == sorted(f.id for f in p.flags)
    assert res.json()["doc"]["open_flags"] == 1
    assert factory.get(Row, p.rows[0].id).corrections == {"weight_g": "12"}
    assert factory.get(Row, p.rows[2].id).corrections == {}  # equal to machine value -> no correction
    assert factory.get(Flag, other_page_flag.id).resolved is False


def test_resolve_page_errors(api, factory):
    p = factory.processed_doc()
    assert api.client.post(f"/api/docs/{p.doc.slug}/page/7/resolve", json={}).status_code == 404
    res = api.client.post(f"/api/docs/{p.doc.slug}/page/1/resolve", json={"rows": {"42": {"sex": "m"}}})
    assert res.status_code == 404
    assert factory.get(Flag, p.flags[0].id).resolved is False  # nothing committed


# ---------- reviewer notes ----------


def test_add_note_creates_user_flag(api, factory):
    p = factory.processed_doc()
    res = api.client.post(
        f"/api/docs/{p.doc.slug}/flags",
        json={"page": 1, "row": 3, "column": "species", "message": "  check the species  "},
    )

    assert res.status_code == 201
    body = res.json()
    (flag,) = body["flags"]
    assert flag["source"] == "user"
    assert flag["severity"] == "warning"
    assert flag["column"] == "species"
    assert flag["message"] == "check the species"
    assert flag["resolved"] is False
    assert body["doc"]["open_flags"] == 4
    assert api.event_types() == ["doc.status", "batch.updated", "review.pending"]


def test_note_on_done_doc_moves_it_back_to_review(api, factory):
    doc = factory.doc(factory.batch(), status=DocStatus.done)
    factory.row(factory.page(doc), 1)
    res = api.client.post(f"/api/docs/{doc.slug}/flags", json={"page": 1, "row": 1, "message": "hm"})
    assert res.status_code == 201
    assert res.json()["doc"]["status"] == "review"
    assert res.json()["doc"]["open_flags"] == 1
    assert res.json()["flags"][0]["column"] is None


def test_note_on_unknown_row_is_404(api, factory):
    p = factory.processed_doc()
    for page, row in ((1, 99), (2, 1)):
        res = api.client.post(f"/api/docs/{p.doc.slug}/flags", json={"page": page, "row": row, "message": "x"})
        assert res.status_code == 404


@pytest.mark.parametrize("status", [DocStatus.queued, DocStatus.processing, DocStatus.error])
def test_note_on_unprocessed_doc_is_409(api, factory, status):
    doc = factory.doc(factory.batch(), status=status)
    res = api.client.post(f"/api/docs/{doc.slug}/flags", json={"page": 1, "row": 1, "message": "x"})
    assert res.status_code == 409


def test_note_validates_message(api, factory):
    p = factory.processed_doc()
    for message in ("", "x" * 2001):
        res = api.client.post(f"/api/docs/{p.doc.slug}/flags", json={"page": 1, "row": 1, "message": message})
        assert res.status_code == 422


def test_delete_note_recomputes_status(api, factory):
    doc = factory.doc(factory.batch(), status=DocStatus.done)
    factory.row(factory.page(doc), 1)
    flag_id = api.client.post(f"/api/docs/{doc.slug}/flags", json={"page": 1, "row": 1, "message": "x"}).json()[
        "flags"
    ][0]["id"]

    res = api.client.delete(f"/api/docs/{doc.slug}/flags/{flag_id}")

    assert res.status_code == 200
    assert res.json()["flags"] == []
    assert res.json()["doc"]["status"] == "done"
    assert factory.get(Flag, flag_id) is None


def test_delete_machine_flag_is_409(api, factory):
    p = factory.processed_doc()
    for f in p.flags[:1] + p.flags[2:]:  # rule + llm
        assert api.client.delete(f"/api/docs/{p.doc.slug}/flags/{f.id}").status_code == 409
    assert factory.get(Flag, p.flags[0].id) is not None


def test_delete_flag_of_other_doc_is_404(api, factory):
    p = factory.processed_doc()
    other = factory.processed_doc(filename="other.pdf")
    note = factory.flag(other.doc, source=FlagSource.user)
    assert api.client.delete(f"/api/docs/{p.doc.slug}/flags/{note.id}").status_code == 404
    assert api.client.delete(f"/api/docs/{p.doc.slug}/flags/999999").status_code == 404


def test_reopen_flags(api, factory):
    p = factory.processed_doc()
    resolved = api.client.post(f"/api/docs/{p.doc.slug}/page/1/resolve", json={}).json()
    assert resolved["doc"]["status"] == "done"

    res = api.client.post(f"/api/docs/{p.doc.slug}/flags/reopen", json={"ids": [p.flags[2].id, p.flags[2].id]})

    assert res.status_code == 200
    assert [f["id"] for f in res.json()["flags"]] == [p.flags[2].id]
    assert res.json()["flags"][0]["resolved"] is False
    assert res.json()["doc"]["status"] == "review"
    assert res.json()["doc"]["open_flags"] == 1


def test_reopen_unknown_flag_is_404(api, factory):
    p = factory.processed_doc()
    other = factory.processed_doc(filename="other.pdf")
    for ids in ([p.flags[0].id, 999999], [other.flags[0].id]):
        assert api.client.post(f"/api/docs/{p.doc.slug}/flags/reopen", json={"ids": ids}).status_code == 404
    assert api.client.post(f"/api/docs/{p.doc.slug}/flags/reopen", json={"ids": []}).status_code == 422


# ---------- rename ----------


def test_rename_doc_renames_file_on_disk(api, factory):
    batch = factory.batch(slug="b")
    doc = factory.doc(batch, "old.pdf")
    input_dir = api.batches_dir / "b" / doc.slug / "input"
    content = (input_dir / "old.pdf").read_bytes()

    res = api.client.patch(f"/api/docs/{doc.slug}", json={"filename": "new.pdf"})

    assert res.status_code == 200
    assert res.json()["filename"] == "new.pdf"
    assert res.json()["slug"] == doc.slug
    assert not (input_dir / "old.pdf").exists()
    assert (input_dir / "new.pdf").read_bytes() == content
    assert api.client.get(f"/api/docs/{doc.slug}/file").content == content
    assert api.event_types() == ["doc.status", "batch.updated"]


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("no_ext", "no_ext.pdf"),
        ("  spaced.PDF  ", "spaced.PDF"),
        ("../../escape.pdf", "escape.pdf"),
        ("/abs/dir/file", "file.pdf"),
    ],
)
def test_rename_doc_normalises_filename(api, factory, given, stored):
    doc = factory.doc(factory.batch(slug="b"), "old.pdf")
    res = api.client.patch(f"/api/docs/{doc.slug}", json={"filename": given})
    assert res.status_code == 200
    assert res.json()["filename"] == stored
    assert (api.batches_dir / "b" / doc.slug / "input" / stored).is_file()


@pytest.mark.parametrize("name", ["", ".pdf", "   ", "/", "a/.pdf"])
def test_rename_doc_rejects_invalid_names(api, factory, name):
    doc = factory.doc(factory.batch(), "old.pdf")
    assert api.client.patch(f"/api/docs/{doc.slug}", json={"filename": name}).status_code == 422


def test_rename_doc_to_taken_name_is_409_only_within_batch(api, factory):
    batch = factory.batch()
    a = factory.doc(batch, "a.pdf")
    factory.doc(batch, "b.pdf")
    factory.doc(factory.batch(), "c.pdf")

    taken = api.client.patch(f"/api/docs/{a.slug}", json={"filename": "b.pdf"})
    assert taken.status_code == 409
    assert "already exists" in taken.json()["detail"]
    assert api.client.patch(f"/api/docs/{a.slug}", json={"filename": "c.pdf"}).status_code == 200
    assert api.client.patch(f"/api/docs/{a.slug}", json={"filename": "c.pdf"}).status_code == 200  # same name


# ---------- files ----------


def test_get_doc_file_serves_pdf_inline(api):
    content = pdf_bytes("inline")
    slug = api.upload("Files", [("Scan (Records) 1972 #1.pdf", content)]).json()["slug"]
    (doc,) = api.client.get(f"/api/batches/{slug}/docs").json()["items"]

    res = api.client.get(f"/api/docs/{doc['slug']}/file")

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"] == 'inline; filename="Scan (Records) 1972 #1.pdf"'
    assert res.content == content


def test_get_doc_file_missing_on_disk_is_404(api, factory):
    doc = factory.doc(factory.batch(), with_file=False)
    res = api.client.get(f"/api/docs/{doc.slug}/file")
    assert res.status_code == 404
    assert res.json()["detail"] == "file not found"


@pytest.mark.parametrize("state", ["raw", "cropped"])
def test_image_artifacts_are_served_from_disk(api, factory, state):
    doc = factory.doc(factory.batch(slug="b"))
    path = api.batches_dir / "b" / doc.slug / state / "p002.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xd8\xff fake jpeg")

    res = api.client.get(f"/api/docs/{doc.slug}/artifacts/{state}/page/2")

    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content == b"\xff\xd8\xff fake jpeg"
    assert api.client.get(f"/api/docs/{doc.slug}/artifacts/{state}/page/1").status_code == 404


def test_processed_artifact_is_rendered_from_db_with_corrections(api, factory):
    doc = factory.doc(factory.batch(), status=DocStatus.done)
    page = factory.page(doc)
    factory.row(page, 1, {"ring_no": "007", "species": "Pica pica"}, corrections={"species": "Corvus"})

    res = api.client.get(f"/api/docs/{doc.slug}/artifacts/processed/page/1")

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    lines = res.text.splitlines()
    assert lines[0].replace(" ", "") == "|#|" + "|".join(c.value for c in ColumnKey) + "|"
    assert "Corvus" in lines[2] and "Pica" not in res.text
    assert "| 007 " in lines[2]  # no number parsing: leading zeros stay


def test_processed_artifact_falls_back_to_markdown_on_disk(api, factory):
    doc = factory.doc(factory.batch(slug="b"))
    md = api.batches_dir / "b" / doc.slug / "processed" / "p001.md"
    md.parent.mkdir(parents=True)
    md.write_text("| a |\n")

    assert api.client.get(f"/api/docs/{doc.slug}/artifacts/processed/page/1").text == "| a |\n"
    assert api.client.get(f"/api/docs/{doc.slug}/artifacts/processed/page/2").status_code == 404


def test_unknown_artifact_state_is_422(api, factory):
    doc = factory.doc(factory.batch())
    assert api.client.get(f"/api/docs/{doc.slug}/artifacts/input/page/1").status_code == 422


def test_empty_values_helper_matches_row_values():
    assert set(empty_values()) == {c.value for c in ColumnKey}
