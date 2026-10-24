import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Iterable

from rapidfuzz import fuzz, process

from backend.src.pipeline.bird_vocab import get_bird_vocab_dict

# 1. Exact ditto tokens for Espèce
DITTO_TOKENS = {"11", "4", "1", "II", "ii", "ll", '"', "''", "dito", "do."}
NON_SPECIES_NOTES = {"ring verloren", "verloren", "tot", "entflogen", "-"}

# Digits/symbols that OCR frequently confuses with letters (e.g. "Zilp2alp" -> "Zilpzalp").
# Applied to BOTH the OCR text and the vocabulary keys before fuzzy matching.
_OCR_CONFUSIONS = str.maketrans(
    {"0": "o", "1": "i", "2": "z", "5": "s", "6": "g", "8": "b", "|": "l"}
)
# Normalized strings shorter than this are never fuzzy-matched (too ambiguous).
_MIN_MATCH_LEN = 3


def normalize_for_match(text: str) -> str:
    """
    Reduces a string to a comparable form for fuzzy matching:
    lowercase, no accents, no parenthesised notes, no spaces/punctuation,
    and common OCR digit-for-letter confusions mapped to letters.
    E.g. 'Hans rotel' -> 'hansrotel', 'Zilp 2alp' -> 'zilpzalp',
    'Mönchsmeise (Alpen- oder Weidenmeise)' -> 'monchsmeise'.
    """
    text = text.replace("ß", "ss").lower()
    decomposed = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    no_parens = re.sub(r"\([^)]*\)", "", no_accents)
    letters_only = re.sub(r"[^a-z0-9]", "", no_parens)
    return letters_only.translate(_OCR_CONFUSIONS)


def build_match_index(vocab_keys: Iterable[str]) -> dict[str, str]:
    """Maps normalized vocabulary keys back to their original key (first one wins)."""
    index: dict[str, str] = {}
    for key in vocab_keys:
        norm = normalize_for_match(key)
        if len(norm) >= _MIN_MATCH_LEN:
            index.setdefault(norm, key)
    return index


def format_species(
    raw_val: str,
    last_species: str,
    vocab_dict: dict[str, str],
    match_index: dict[str, str],
    fuzzy_cutoff: float = 70.0,
    always_show_parens: bool = False,
) -> tuple[str, str]:
    """
    Returns (formatted_cell_text, updated_last_species).
    Maps synonyms/typos to 'MatchedName (GermanCanonicalName)'.

    Matching order:
      1. ditto tokens  -> copy the previous species
      2. exact key in vocabulary
      3. normalized exact match (case/accents/spaces/OCR confusions ignored)
      4. fuzzy match (rapidfuzz ratio) on the normalized strings, >= fuzzy_cutoff
    """
    val = raw_val.strip()
    if not val:
        return val, last_species

    if val.lower() in NON_SPECIES_NOTES:
        return val, last_species

    # Check if ditto token
    if val in DITTO_TOKENS or val.upper() in DITTO_TOKENS:
        if last_species:
            return last_species, last_species
        return val, last_species

    if val in vocab_dict:
        matched_key = val
    else:
        query = normalize_for_match(val)
        if len(query) < _MIN_MATCH_LEN:
            return val, val
        if query in match_index:
            matched_key = match_index[query]
        else:
            best = process.extractOne(
                query,
                list(match_index.keys()),
                scorer=fuzz.ratio,
                score_cutoff=fuzzy_cutoff,
            )
            if not best:
                return val, val
            matched_key = match_index[best[0]]

    canonical_de = vocab_dict[matched_key]

    if always_show_parens or matched_key != canonical_de:
        formatted = f"{matched_key} ({canonical_de})"
    else:
        formatted = canonical_de

    return formatted, formatted


def format_sex(raw_val: str) -> str:
    """
    Replaces 07, 0.7, 0,7, and 0-1 with ♂ (preserving parentheses if present).
    """
    val = raw_val.strip()
    if not val:
        return val

    return re.sub(
        r"\(?\b(?:0[.,]?7|0-1)\b\)?",
        lambda m: "(♂)" if "(" in m.group(0) else "♂",
        val,
    )


def format_hour(raw_val: str) -> str:
    """
    Normalizes hour strings in column 6:
      '1330' -> '13:30'
      '500'  -> '5:00'
      '530'  -> '5:30'
      '7'    -> '7:00'
      '6.00' -> '6:00'
    """
    val = raw_val.strip()
    if not val:
        return val

    # Case 1: Already separated by '.', ',', or ':' (e.g. '6.00', '07.30', '6:00')
    m_sep = re.fullmatch(r"(\d{1,2})[.,:](\d{2})", val)
    if m_sep:
        h, m = int(m_sep.group(1)), int(m_sep.group(2))
        if 0 <= h <= 24 and 0 <= m <= 59:
            return f"{h}:{m:02d}"

    # Case 2: Pure digits (1 to 4 digits)
    m_digits = re.fullmatch(r"\d{1,4}", val)
    if m_digits:
        if len(val) <= 2:
            # '7' -> '7:00', '14' -> '14:00'
            h = int(val)
            if 0 <= h <= 24:
                return f"{h}:00"
        elif len(val) == 3:
            # '500' -> '5:00', '530' -> '5:30'
            h, m = int(val[0]), int(val[1:])
            if 0 <= h <= 24 and 0 <= m <= 59:
                return f"{h}:{m:02d}"
        elif len(val) == 4:
            # '1330' -> '13:30', '0600' -> '6:00'
            h, m = int(val[:2]), int(val[2:])
            if 0 <= h <= 24 and 0 <= m <= 59:
                return f"{h}:{m:02d}"

    return val


# Formatting tags carry no information we need; drop the tag but keep its text.
# Dropping them avoids unclosed-tag parse errors in ElementTree.
_FORMAT_TAG_RE = re.compile(
    r"</?(?:b|i|u|s|del|sup|sub|span|p|div|strong|em|font|center)\b[^>]*>",
    re.IGNORECASE,
)
_ENTITY_RE = re.compile(r"&(#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")
_TABLE_TAG_RE = re.compile(r"<(/?)(table|tr|td|th)\b[^>]*>", re.IGNORECASE)


def _decode_entity(m: re.Match) -> str:
    """Decodes HTML entities (e.g. &nbsp;, &eacute;) but never creates new tags."""
    entity = m.group(0)
    if entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&apos;"):
        return entity
    decoded = html.unescape(entity)
    return entity if decoded in ("<", ">", "&") else decoded


def _sanitize_html_for_xml(html_str: str) -> str:
    """
    Turns the model's loose HTML table into something xml.etree accepts:
      - only <table>, <tr>, <td>/<th> tags remain (colspan/rowspan kept)
      - thead/tbody/tfoot and formatting tags are removed (text is kept)
      - <br>/<hr>/<img> become spaces
      - HTML entities are decoded, stray '&' and '<' are escaped
    """
    # 1. Structure tags: drop attributes (except colspan/rowspan)
    html_str = re.sub(r"<(table|tr)\b[^>]*>", r"<\1>", html_str, flags=re.IGNORECASE)
    html_str = re.sub(
        r"</?(?:thead|tbody|tfoot)\b[^>]*>", "", html_str, flags=re.IGNORECASE
    )

    def _clean_cell_tag(m: re.Match) -> str:
        tag = m.group(1).lower()
        attrs = m.group(2)
        kept = [
            f'{am.group(1).lower()}="{am.group(2)}"'
            for am in re.finditer(
                r"(colspan|rowspan)\s*=\s*['\"]?(\d+)['\"]?", attrs, re.IGNORECASE
            )
        ]
        return f"<{tag} {' '.join(kept)}>" if kept else f"<{tag}>"

    html_str = re.sub(
        r"<(td|th)\b([^>]*)>", _clean_cell_tag, html_str, flags=re.IGNORECASE
    )
    html_str = re.sub(
        r"</(table|tr|td|th)\s*>",
        lambda m: f"</{m.group(1).lower()}>",
        html_str,
        flags=re.IGNORECASE,
    )

    # 2. Void tags -> whitespace (so "Bisternis<br>(♀)" becomes "Bisternis (♀)")
    html_str = re.sub(r"<(?:br|hr|img)\b[^>]*>", " ", html_str, flags=re.IGNORECASE)

    # 3. Formatting tags: drop the tag, keep the text
    html_str = _FORMAT_TAG_RE.sub("", html_str)

    # 4. Entities: decode safely, then escape any remaining bare '&' and stray '<'
    html_str = _ENTITY_RE.sub(_decode_entity, html_str)
    html_str = re.sub(r"&(?!(?:amp|lt|gt|quot|apos);)", "&amp;", html_str)
    html_str = re.sub(r"<(?!/?(?:table|tr|td|th)\b)", "&lt;", html_str)
    return html_str


def _repair_table_structure(table_html: str) -> str:
    """
    Closes cells/rows the model left open and drops stray closing tags, so the
    result is a well-formed tree. Input must already be sanitized.
    E.g. '<tr><td>a<td>b</tr>' -> '<tr><td>a</td><td>b</td></tr>'.
    """
    out: list[str] = []
    pos = 0
    open_cell: str | None = None  # "td" or "th" while a cell is open
    row_open = False

    for m in _TABLE_TAG_RE.finditer(table_html):
        out.append(table_html[pos : m.start()])
        pos = m.end()
        closing = m.group(1) == "/"
        tag = m.group(2).lower()

        if tag in ("td", "th"):
            if open_cell:
                out.append(f"</{open_cell}>")
                open_cell = None
            if not closing:
                if not row_open:
                    out.append("<tr>")
                    row_open = True
                out.append(m.group(0))
                open_cell = tag

        elif tag == "tr":
            if open_cell:
                out.append(f"</{open_cell}>")
                open_cell = None
            if row_open:
                out.append("</tr>")
                row_open = False
            if not closing:
                out.append("<tr>")
                row_open = True

        else:  # table
            if open_cell:
                out.append(f"</{open_cell}>")
                open_cell = None
            if row_open:
                out.append("</tr>")
                row_open = False
            out.append(m.group(0))

    out.append(table_html[pos:])
    if open_cell:
        out.append(f"</{open_cell}>")
    if row_open:
        out.append("</tr>")
    return "".join(out)


def fix_ovis_html_with_etree(
    raw_output: str,
    species_col_idx: int = 1,  # 2nd column (0-based index 1)
    sex_col_idx: int = 2,  # 3rd column (0-based index 2)
    hour_col_idx: int = 5,  # 6th column (0-based index 5)
    fuzzy_cutoff: float = 70.0,
) -> str:
    vocab_dict = get_bird_vocab_dict()
    match_index = build_match_index(vocab_dict.keys())

    def _process_single_table(match: re.Match) -> str:
        raw_table = match.group(0)
        xml_ready = _repair_table_structure(_sanitize_html_for_xml(raw_table))

        try:
            root = ET.fromstring(xml_ready)
        except ET.ParseError as e:
            print(f"[ERROR] XML ParseError: {e} -> table left unchanged")
            return raw_table

        last_valid_species = ""

        for row_idx, tr in enumerate(root.findall(".//tr")):
            cells = [c for c in tr if c.tag.lower() in ("td", "th")]
            if not cells:
                continue

            # Skip header rows (rows containing 'Bague', 'Espèce', 'Sexe', 'Heure')
            row_text_lower = " ".join("".join(c.itertext()).lower() for c in cells)
            if row_idx < 3 and any(
                h in row_text_lower for h in ("bague", "esp", "sexe", "heure", "poids")
            ):
                continue

            # --- Column 2 (index 1): Espèce ---
            if len(cells) > species_col_idx:
                sp_td = cells[species_col_idx]
                raw_sp = "".join(sp_td.itertext()).strip()
                new_sp, last_valid_species = format_species(
                    raw_sp, last_valid_species, vocab_dict, match_index, fuzzy_cutoff
                )
                sp_td.clear()
                sp_td.text = new_sp

            # --- Column 3 (index 2): Sexe ---
            if len(cells) > sex_col_idx:
                sex_td = cells[sex_col_idx]
                raw_sex = "".join(sex_td.itertext()).strip()
                sex_td.clear()
                sex_td.text = format_sex(raw_sex)

            # --- Column 6 (index 5): Heure ---
            if len(cells) > hour_col_idx:
                hr_td = cells[hour_col_idx]
                raw_hr = "".join(hr_td.itertext()).strip()
                hr_td.clear()
                hr_td.text = format_hour(raw_hr)

        return ET.tostring(root, encoding="unicode", method="html")

    return re.sub(
        r"<table.*?>.*?</table>",
        _process_single_table,
        raw_output,
        flags=re.DOTALL | re.IGNORECASE,
    )
