"""Adapter between your pipeline output and the app. This is the one file you must adapt.

Events and flags are the app's contract; how your LlmExtractor writes its result is yours.
"""

import json
import re
from pathlib import Path

COLS = ["Ring no.", "Species", "Age", "Sex", "Wing mm", "Weight g", "Time"]


def load_table(out_dir: Path) -> list[list[str]]:
    """TODO: read what your last pipeline step writes.
    Assumed here: <out_dir>/result.json = {"rows": [[...7 strings...], ...]}"""
    p = out_dir / "result.json"
    return json.loads(p.read_text())["rows"] if p.exists() else []


def make_flags(rows: list[list[str]]) -> list[dict]:
    """Post-processing / validation rules. Each flag: row, col, kind (bad|warn), msg, why, suggestion."""
    flags = []
    for r, row in enumerate(rows):
        if not re.fullmatch(r"HA \d{5}", row[0]):
            flags.append(
                {
                    "row": r,
                    "col": 0,
                    "kind": "warn",
                    "msg": "Ring number: format unclear",
                    "why": f"“{row[0]}” does not match HA + 5 digits.",
                    "suggestion": "",
                }
            )
        try:
            w = float(row[5])
            if not 3 <= w <= 120:
                raise ValueError
        except ValueError:
            flags.append(
                {
                    "row": r,
                    "col": 5,
                    "kind": "bad",
                    "msg": "Weight out of range",
                    "why": f"“{row[5]}” is implausible. Decimal point missed?",
                    "suggestion": "",
                }
            )
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", row[6]):
            flags.append(
                {
                    "row": r,
                    "col": 6,
                    "kind": "warn",
                    "msg": "Time not readable",
                    "why": f"“{row[6]}”",
                    "suggestion": "",
                }
            )
    return flags
