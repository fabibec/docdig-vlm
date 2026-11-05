from pydantic import BaseModel, Field


class Uncertainty(BaseModel):
    """One cell or row the model is not sure about."""

    row_number: int | None = Field(
        default=None,
        description="1-based data row index (header not counted). None if not tied to one row.",
    )
    column: str = Field(
        description="Column header the value belongs to, e.g. 'Espèce'."
    )
    value_read: str = Field(description="Exactly what is written in the cell.")
    reason: str = Field(
        description="Why it is unclear, e.g. 'handwriting ambiguous', "
        "'name not in species list', 'digit could be 1 or 7'."
    )
    best_guess: str | None = Field(
        default=None,
        description="Most likely real value, or null if there is no guess.",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in best_guess (0 = none, 1 = certain)."
    )


class TableRow(BaseModel):
    """One data row of the bird ringing form. Empty cells are empty strings."""

    row_number: int = Field(
        description="1-based data row index from the top (header not counted)."
    )
    ring_no: str = Field(
        description="Column 'Bague No'. Only the last digit is printed (row 1 -> 1, ..., row 10 -> 0). "
        "Return handwritten prefix + printed digit. The prefix carries over until a new one is written; "
        "a shorter new prefix only overwrites the trailing part of the previous one."
    )
    species: str = Field(
        description="Column 'Espèce'. Names mix German/Latin/French; do not normalize. "
        "Use knowledge of birds in Switzerland to disambiguate handwriting; if unsure, return the "
        "closest valid name and add an uncertainty. Ditto marks (II, ii, ll, 11, 1, \", '', dito, do.) "
        "-> repeat the species of the row above."
    )
    sex: str = Field(
        description="Column '♂ Sexe ♀'. MUST be converted, never output the symbols: ♂ -> m, "
        "♀ -> f, × -> X. Keep brackets and the order of appearance, e.g. '(♂)' -> '(m)', "
        "'♀' -> 'f'. Allowed characters: m, f, X, (, ) or empty string."
    )
    age: str = Field(
        description="Column 'Age'. One of N, NF, F, Fd, Fnd, Fv, Fnv. Normalize dj -> Fd, ad -> Fnd; "
        "drop trailing dots. Use context for unclear writing; report anything that doesn't fit."
    )
    date: str = Field(
        description="Column 'Jour Mois', as written (e.g. '10.7.72'). Empty cells carry over the "
        "date from the row above until a new entry occurs."
    )
    time: str = Field(
        description="Column 'Heure' as HH:MM; change only the format, not the digits "
        "('1330' -> '13:30', '830' -> '08:30', '7' -> '07:00'). Empty cells carry over the time "
        "from the row above until a new entry occurs."
    )
    wing_length_mm: str = Field(
        description="Column 'Alle': wing length in millimetres, as written (e.g. '68')."
    )
    weight_g: str = Field(
        description="Column 'Poids': weight in grams, as written (e.g. '12.5')."
    )
    additional_remark: str = Field(
        description="Last three columns ('laisser en blanc'), treated as one. Transcribe numbers or "
        "places as is; any other word loosely matching is 'Brutfleck'."
    )


class PageTable(BaseModel):
    """Structured result for one scanned page."""

    rows: list[TableRow] = Field(
        description="All data rows from top to bottom, including empty ones."
    )
    uncertainties: list[Uncertainty] = Field(
        default_factory=list,
        description="Everything you are not sure about. Empty list if you are sure about everything.",
    )
    page_confidence: float = Field(
        ge=0.0, le=1.0, description="Overall confidence in the whole table."
    )


class ExtractionResult(BaseModel):
    """PageTable plus the file it came from (added by this script, not by the LLM)."""

    source: str
    table: PageTable
    input_tokens: int
    output_tokens: int
    estimated_cost: float
