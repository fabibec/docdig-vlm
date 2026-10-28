"""Turns a pipeline page result into review flags: LLM uncertainties + plausibility rules.

Rule flags carry a ``code`` + ``params`` (translated in the frontend) and an English
``message`` fallback. LLM flags carry the model's reason as ``message``.
"""

import logging
import re

from backend.api.enums import ColumnKey, FlagSource, Severity
from backend.pipeline.models.llm_responses import TableRow, Uncertainty
from backend.pipeline.models.results import PageResult

log = logging.getLogger(__name__)

# Printed header (as the LLM reports it) -> column key. Matched case-insensitively by substring.
HEADER_KEYS: list[tuple[str, ColumnKey]] = [
    ("bague", ColumnKey.ring_no),
    ("ring", ColumnKey.ring_no),
    ("esp", ColumnKey.species),
    ("species", ColumnKey.species),
    ("sex", ColumnKey.sex),
    ("age", ColumnKey.age),
    ("jour", ColumnKey.date),
    ("date", ColumnKey.date),
    ("heure", ColumnKey.time),
    ("time", ColumnKey.time),
    ("alle", ColumnKey.wing_length_mm),
    ("aile", ColumnKey.wing_length_mm),
    ("wing", ColumnKey.wing_length_mm),
    ("poids", ColumnKey.weight_g),
    ("weight", ColumnKey.weight_g),
    ("blanc", ColumnKey.additional_remark),
    ("remark", ColumnKey.additional_remark),
]

RANGES: dict[ColumnKey, tuple[float, float, str]] = {
    ColumnKey.weight_g: (2, 2000, "weight_out_of_range"),
    ColumnKey.wing_length_mm: (20, 700, "wing_out_of_range"),
}
TIME_RE = re.compile(r"([01]?\d|2[0-3]):[0-5]\d")


def column_for(header: str) -> ColumnKey | None:
    h = header.lower()
    if h in ColumnKey.__members__:
        return ColumnKey(h)
    return next((key for needle, key in HEADER_KEYS if needle in h), None)


def row_values(row: TableRow) -> dict[str, str]:
    return {c.value: (getattr(row, c.value) or "").strip() for c in ColumnKey}


def _llm_flag(u: Uncertainty) -> dict | None:
    if u.row_number is None:
        log.info("skipping uncertainty without row: %s", u.reason)
        return None
    return dict(
        row_idx=u.row_number,
        column=column_for(u.column),
        source=FlagSource.llm,
        severity=Severity.warning,
        message=u.reason,
        suggestion=u.best_guess,
        confidence=u.confidence,
        params={"value": u.value_read},
    )


def _rule_flags(idx: int, values: dict[str, str]) -> list[dict]:
    flags = []
    for col, (lo, hi, code) in RANGES.items():
        raw = values.get(col.value, "")
        if not raw:
            continue
        try:
            num = float(raw.replace(",", "."))
        except ValueError:
            flags.append(dict(row_idx=idx, column=col, source=FlagSource.rule, severity=Severity.error,
                              code="not_a_number", params={"value": raw},
                              message=f"“{raw}” is not a number."))
            continue
        if not lo <= num <= hi:
            flags.append(dict(row_idx=idx, column=col, source=FlagSource.rule, severity=Severity.error,
                              code=code, params={"value": raw, "min": lo, "max": hi},
                              message=f"“{raw}” is outside {lo:g}–{hi:g}."))
    t = values.get(ColumnKey.time.value, "")
    if t and not TIME_RE.fullmatch(t):
        flags.append(dict(row_idx=idx, column=ColumnKey.time, source=FlagSource.rule,
                          severity=Severity.warning, code="time_format", params={"value": t},
                          message=f"“{t}” is not HH:MM."))
    return flags


def make_flags(page: PageResult) -> list[dict]:
    """Flag kwargs (without doc_id/page_num) for one page."""
    flags = [f for u in page.table.uncertainties if (f := _llm_flag(u))]
    for row in page.table.rows:
        values = row_values(row)
        if any(values.values()):
            flags += _rule_flags(row.row_number, values)
    return flags
