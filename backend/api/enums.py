"""Enums shared by DB models and API schemas."""

from enum import StrEnum


class DocStatus(StrEnum):
    queued = "queued"
    processing = "processing"
    review = "review"
    done = "done"
    error = "error"


class ArtifactState(StrEnum):
    raw = "raw"
    cropped = "cropped"
    processed = "processed"


class ColumnKey(StrEnum):
    ring_no = "ring_no"
    species = "species"
    sex = "sex"
    age = "age"
    date = "date"
    time = "time"
    wing_length_mm = "wing_length_mm"
    weight_g = "weight_g"
    additional_remark = "additional_remark"


COLUMNS: list[ColumnKey] = list(ColumnKey)

# Printed form columns (left to right) each ColumnKey covers. The form has 11 columns:
# Bague No | Espèce | Sexe | Age | Jour Mois | Heure | Alle | Poids | – | – | laisser en blanc
COLUMN_GRID_SPAN: dict[ColumnKey, tuple[int, int]] = {
    ColumnKey.ring_no: (0, 0),
    ColumnKey.species: (1, 1),
    ColumnKey.sex: (2, 2),
    ColumnKey.age: (3, 3),
    ColumnKey.date: (4, 4),
    ColumnKey.time: (5, 5),
    ColumnKey.wing_length_mm: (6, 6),
    ColumnKey.weight_g: (7, 7),
    ColumnKey.additional_remark: (8, 10),
}


class FlagSource(StrEnum):
    llm = "llm"  # LLM uncertainty
    rule = "rule"  # plausibility rule
    user = "user"  # annotation added by a reviewer (deletable)


class Severity(StrEnum):
    error = "error"
    warning = "warning"
