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
    ring_no: str = Field(description="Column 'Bague No', as printed.")
    species: str = Field(
        description="Column 'Espèce'. Use the exact name from the allowed species list when "
        "it matches; for ditto marks (II, 11, \", ''), repeat the species of the row above."
    )
    sex: str = Field(description="Column '♂ Sexe ♀': keep ♂, ♀, (♂), (♀), × as printed.")
    age: str = Field(description="Column 'Age', as printed (e.g. 'ad.', 'u', 'Fd').")
    date: str = Field(description="Column 'Jour Mois', as printed (e.g. '10.7.72').")
    time: str = Field(
        description="Column 'Heure' as HH:MM, e.g. '1330' -> '13:30', '830' -> '8:30'."
    )
    wing_length_mm: str = Field(
        description="Column 'Alle': wing length in millimetres, as printed (e.g. '68')."
    )
    weight_g: str = Field(
        description="Column 'Poids': weight in grams, as printed (e.g. '12.5')."
    )
    additional_remark: str = Field(
        description="Column 'laisser en blanc': Additional text contained in the last three columns. Transcribe as is."
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
