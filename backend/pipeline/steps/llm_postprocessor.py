"""Pipeline step after the LLM: clean up ``processed/pNNN.json`` with deterministic rules.

Rules (each is also asked for in the LLM schema, this catches what the LLM missed):
- ``ditto``: ditto marks (``ll``, ``"``, ``u``, ``〃``, ``dito`` …) in the species and age columns are replaced by
  the nearest value above in the same column.
- ``age``: old age codes are converted, ``dj`` -> ``Fd`` and ``ad`` -> ``Fnd`` (any case, trailing dots ignored).
- ``carryover``: the LLM carries date and time down into every row. In rows without a bird measured (e.g.
  "Ring verloren" or empty rows) a date/time equal to the value above is cleared again.

Every change is recorded in ``ExtractionResult.postprocessed`` so later code (benchmark, review) can show it.
"""

from __future__ import annotations

import logging
import re
from typing import ClassVar

from backend.pipeline.models.llm_responses import CellFix, ExtractionResult, TableRow
from backend.pipeline.models.pipeline_parts import PipelineStep, PipelineTask, page_stem
from backend.pipeline.steps.llm_extractor import write_result

log = logging.getLogger(__name__)

# Compared after removing whitespace and casefolding ("I I" -> "ii"). Strokes, digits and "u" (a hasty "11")
# are safe for species: a name never consists of them.
DITTO_MARKS = frozenset(
    {
        '"',
        '""',
        '"""',
        "''",
        "〃",
        "”",
        "“",
        "„",
        "ll",
        "ii",
        "l",
        "1",
        "11",
        "|",
        "||",
        "dito",
        "do",
        "do.",
        "dto",
        "dto.",
        "u",
        "uu",
    }
)
# Age codes may be digits in some years, so digits don't count as ditto marks there.
DITTO_MARKS_AGE = DITTO_MARKS - {"1", "11"}
# Species and age only: in the other columns the marks are real values (weight 11) or carried over by the LLM.
COLUMNS = ["species", "age"]


def is_ditto(value: str, column: str = "species") -> bool:
    marks = DITTO_MARKS_AGE if column == "age" else DITTO_MARKS
    return re.sub(r"\s+", "", value).casefold() in marks


def resolve_dittos(rows: list[dict[str, str]], columns: list[str] = COLUMNS) -> list[tuple[int, str, str, str]]:
    """Replace ditto marks in ``rows`` in place by the nearest non-empty value above.

    Returns ``(row_index, column, original, value)`` for every change. A ditto mark with nothing above
    stays as it is.
    """
    fixes = []
    for col in columns:
        last = ""
        for i, row in enumerate(rows):
            value = row.get(col) or ""
            if is_ditto(value, col):
                if last:
                    row[col] = last
                    fixes.append((i, col, value, last))
                continue
            if value.strip():
                last = value
    return fixes


# Old age codes -> current ones (keys casefolded, without dots and whitespace).
AGE_CODES = {"dj": "Fd", "ad": "Fnd"}


def convert_age_codes(rows: list[dict[str, str]]) -> list[tuple[int, str, str, str]]:
    """Replace old age codes in ``rows`` in place. Returns ``(row_index, "age", original, value)`` per change."""
    fixes = []
    for i, row in enumerate(rows):
        value = row.get("age") or ""
        new = AGE_CODES.get(re.sub(r"[\s.]+", "", value).casefold())
        if new:
            row["age"] = new
            fixes.append((i, "age", value, new))
    return fixes


CARRY_COLUMNS = ("date", "time")
# A row with none of these filled has no bird measured, so nothing to carry date/time into.
BIRD_COLUMNS = ("sex", "age", "wing_length_mm", "weight_g")


def clear_carryover(rows: list[dict[str, str]]) -> list[tuple[int, str, str, str]]:
    """Clear date/time carried into rows without a bird. Returns ``(row_index, column, original, "")``.

    A value equal to the nearest non-empty one above counts as carried; a different value was written on the
    form and stays.
    """
    fixes = []
    for col in CARRY_COLUMNS:
        last = ""
        for i, row in enumerate(rows):
            value = (row.get(col) or "").strip()
            if not value:
                continue
            if value == last and not any((row.get(c) or "").strip() for c in BIRD_COLUMNS):
                row[col] = ""
                fixes.append((i, col, value, ""))
            last = value
    return fixes


def postprocess_rows(rows: list[dict[str, str]]) -> list[tuple[int, str, str, str, str]]:
    """Apply all rules to ``rows`` in place. Returns ``(row_index, column, original, value, rule)`` per change."""
    return (
        [(*f, "ditto") for f in resolve_dittos(rows)]
        + [(*f, "age") for f in convert_age_codes(rows)]
        + [(*f, "carryover") for f in clear_carryover(rows)]
    )


class LlmPostprocessor(PipelineStep):
    """``processed/pNNN.json`` -> same files, cleaned by ``postprocess_rows``. Runs right after ``LlmExtractor``."""

    # Same step name as the LLM step, so `--steps processed` and the API progress events cover both.
    name: ClassVar[str] = "processed"

    def execute(self, task: PipelineTask) -> None:
        out_dir = task.step_dir(self.name)
        for num in task.page_nums(self.name, ".json"):
            ctx = {"pdf": task.input_pdf.name, "step": self.name, "page": num}
            path = out_dir / f"{page_stem(num)}.json"
            result = ExtractionResult.model_validate_json(path.read_text(encoding="utf-8"))

            rows = [r.model_dump() for r in result.table.rows]
            fixes = postprocess_rows(rows)
            if not fixes:
                log.debug("No post-processing needed for page %d", num, extra=ctx)
                continue

            result.table.rows = [TableRow.model_validate(r) for r in rows]
            result.postprocessed += [
                CellFix(row_number=rows[i]["row_number"], column=col, original=orig, value=value, rule=rule)
                for i, col, orig, value, rule in fixes
            ]
            write_result(out_dir, num, result)
            log.info(
                "Post-processed %d cell(s) on page %d of %s (%d ditto, %d age, %d carry-over)",
                len(fixes),
                num,
                task.input_pdf.name,
                sum(f[4] == "ditto" for f in fixes),
                sum(f[4] == "age" for f in fixes),
                sum(f[4] == "carryover" for f in fixes),
                extra={**ctx, "fixes": len(fixes)},
            )
