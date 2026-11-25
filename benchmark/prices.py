"""Manual LLM prices for models genai-prices doesn't know yet (the pipeline records their cost as 0).

Remove an entry once genai-prices has the model. The report uses these only when a page's recorded cost is 0,
so it also fixes results that were collected before the entry was added.
"""

# "provider/model" (as in pages.csv `model`, or the --model argument) -> ($ per 1M input, $ per 1M output tokens)
PRICES: dict[str, tuple[float, float]] = {
    "meta/muse-ember": (0.10, 0.50),
}


def page_cost(model: str, cost: float, input_tokens: int, output_tokens: int) -> float:
    """The recorded cost, or the manual price when it is 0 and the model has an entry."""
    if cost or model not in PRICES:
        return cost
    per_in, per_out = PRICES[model]
    return (input_tokens * per_in + output_tokens * per_out) / 1_000_000
