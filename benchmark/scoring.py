"""Scoring rules of the benchmark. Pure functions, no I/O except reading reference CSVs.

Rows are matched by position (the LLM's ``row_number`` is ignored). A cell scores 1 (correct) to 0 (wrong);
cells empty in reference and prediction are not counted.

- ``ring_no``: per character. One wrong digit out of six gives 5/6. A substitution that repeats on a page
  (same position, same wrong character, in at least ``SYSTEMATIC_MIN_ROWS`` rows, e.g. A50401 for A90401 on
  every row) is fully penalised once; later rows pay only ``SYSTEMATIC_REPEAT_COST`` of a character,
  because a human fixes it with one bulk edit.
- ``species``, ``additional_remark``: 1 - Levenshtein distance / length of the longer string.
- all other columns: exact match after normalisation.
- Flags: a wrong cell the LLM flagged gets ``FLAG_WRONG_CREDIT`` of its error back; a correct cell the LLM
  flagged loses ``FLAG_CORRECT_PENALTY`` (it costs a human check). This is the flag-adjusted score.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import Levenshtein

from backend.pipeline.steps.llm_postprocessor import resolve_dittos

COLUMNS = [
    "ring_no",
    "species",
    "sex",
    "age",
    "date",
    "time",
    "wing_length_mm",
    "weight_g",
    "additional_remark",
]
WEIGHTS = dict.fromkeys(COLUMNS, 1.0) | {"species": 3.0}
FUZZY_COLUMNS = {"species", "additional_remark"}
FORWARD_FILL = ("date", "time")  # ring prefix handled separately
# Date/time are only carried into rows with a bird measured, not into notes like "Ring verloren".
BIRD_COLUMNS = ("sex", "age", "wing_length_mm", "weight_g")

SYSTEMATIC_MIN_ROWS = 3
SYSTEMATIC_REPEAT_COST = 0.1
FLAG_WRONG_CREDIT = 0.5
FLAG_CORRECT_PENALTY = 0.25

# Values in the reference that are scan artifacts, not data (being removed from the dataset).
EMPTY_VALUES = {"-", "–", "."}

# Substrings (casefolded) to map the free-text `column` of an LLM uncertainty to a column key.
COLUMN_ALIASES = {
    "ring_no": ("bague", "ring"),
    "species": ("esp", "species", "art"),
    "sex": ("sex", "geschlecht"),
    "age": ("age", "alter"),
    "date": ("jour", "mois", "date", "datum"),
    "time": ("heure", "time", "zeit"),
    "wing_length_mm": ("aile", "alle", "wing", "flügel"),
    "weight_g": ("poids", "weight", "gewicht"),
    "additional_remark": ("blanc", "remark", "bemerk"),
}


# ---------------------------------------------------------------- reference


@dataclass
class RefPage:
    rows: list[dict[str, str]]
    dittos: set[tuple[int, str]]  # (0-based row, column) resolved from a ditto mark


def clean(v: str) -> str:
    v = (v or "").strip()
    return "" if v in EMPTY_VALUES else v


def load_reference(path: Path) -> RefPage:
    """Reference CSV (``;``-separated, columns by position) -> rows with dittos and carry-overs resolved."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)  # header
        rows = [{c: clean(v) for c, v in zip(COLUMNS, r, strict=False)} for r in reader]
    rows = [{c: r.get(c, "") for c in COLUMNS} for r in rows]
    while rows and not any(rows[-1].values()):
        rows.pop()
    dittos = {(i, c) for i, c, _, _ in resolve_dittos(rows)}
    return RefPage(forward_fill(rows), dittos)


def forward_fill(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Mirror the pipeline's carry-over rules: date/time carry down, ring prefix carries down.

    Empty date/time stay empty in rows without any bird measurement (e.g. "Ring verloren").
    """
    last = dict.fromkeys(FORWARD_FILL, "")
    last_ring = ""
    for r in rows:
        bird = any(r[c] for c in BIRD_COLUMNS)
        for c in FORWARD_FILL:
            if r[c]:
                last[c] = r[c]
            elif bird:
                r[c] = last[c]
        ring = r["ring_no"]
        if ring and last_ring and len(ring) < len(last_ring):
            # shorter entry only overwrites the trailing part of the previous ring
            r["ring_no"] = last_ring[: len(last_ring) - len(ring)] + ring
        if r["ring_no"]:
            last_ring = r["ring_no"]
    return rows


def column_key(name: str) -> str | None:
    """Map the free-text column of an uncertainty (e.g. 'Espèce', 'species') to a column key."""
    n = name.casefold()
    for key, aliases in COLUMN_ALIASES.items():
        if n == key or any(a in n for a in aliases):
            return key
    return None


# ---------------------------------------------------------------- cell scores


def normalize(col: str, v: str) -> str:
    v = re.sub(r"\s+", " ", clean(v)).casefold().rstrip(".")
    if col == "ring_no":
        v = v.replace(" ", "")
    if col in ("wing_length_mm", "weight_g"):
        v = v.replace(",", ".")
        if re.fullmatch(r"\d+\.0+", v):
            v = v.split(".")[0]
    if col == "time" and re.fullmatch(r"\d:\d\d", v):
        v = "0" + v
    return v


def fuzzy(ref: str, pred: str) -> float:
    return max(0.0, 1 - Levenshtein.distance(ref, pred) / max(len(ref), len(pred)))


def ring_substitutions(ref: str, pred: str) -> list[tuple[int, str, str]]:
    """(position, reference char, predicted char) for equal-length rings, else []."""
    if len(ref) != len(pred):
        return []
    return [(i, r, p) for i, (r, p) in enumerate(zip(ref, pred, strict=True)) if r != p]


@dataclass
class Cell:
    """One scored cell. ``score`` is None when both sides are empty (not counted)."""

    column: str
    reference: str
    predicted: str
    flagged: bool = False
    score: float | None = None

    @property
    def kind(self) -> str:
        """ok, partial, wrong, hallucinated (reference empty) or missed (prediction empty)."""
        ref, pred = normalize(self.column, self.reference), normalize(self.column, self.predicted)
        if self.score is None or self.score == 1:
            return "ok"
        if not ref:
            return "hallucinated"
        if not pred:
            return "missed"
        return "partial" if self.score > 0 else "wrong"

    @property
    def points(self) -> float:
        """``score`` as a number, 0 for an uncounted cell. Use it on cells already filtered to counted ones."""
        return self.score or 0.0

    @property
    def is_error(self) -> bool:
        return self.score is not None and self.score < 1

    @property
    def adjusted(self) -> float | None:
        """Score with the flag bonus / penalty."""
        if self.score is None or not self.flagged:
            return self.score
        if self.score == 1:
            return 1 - FLAG_CORRECT_PENALTY
        return self.score + (1 - self.score) * FLAG_WRONG_CREDIT


def score_page[C: Cell](cells: list[C]) -> list[C]:
    """Fill ``Cell.score`` for all cells of one page (needed together for systematic ring errors).

    ``cells`` must be in row order; the first row with a systematic substitution pays in full.
    """
    rings = [(c, normalize("ring_no", c.reference), normalize("ring_no", c.predicted)) for c in cells]
    rings = [(c, r, p) for c, r, p in rings if c.column == "ring_no" and r and p]
    counts = Counter(s for _, r, p in rings for s in set(ring_substitutions(r, p)))
    systematic = {s for s, n in counts.items() if n >= SYSTEMATIC_MIN_ROWS}
    seen: set[tuple[int, str, str]] = set()

    for c in cells:
        ref, pred = normalize(c.column, c.reference), normalize(c.column, c.predicted)
        if not ref and not pred:
            c.score = None
        elif not ref or not pred:
            c.score = 0.0
        elif c.column == "ring_no":
            subs = ring_substitutions(ref, pred)
            if len(ref) != len(pred):
                c.score = fuzzy(ref, pred)
            else:
                cost = 0.0
                for s in subs:
                    cost += SYSTEMATIC_REPEAT_COST if s in systematic and s in seen else 1.0
                    seen.add(s)
                c.score = max(0.0, 1 - cost / len(ref))
        elif c.column in FUZZY_COLUMNS:
            c.score = fuzzy(ref, pred)
        else:
            c.score = float(ref == pred)
    return cells
