import json
import os
import ssl

import httpx
import truststore

from backend.src.pipeline.directories import OUTPUT_DIR


def _add_entry_to_map(
    bird_obj: dict, target_de_name: str, bird_map: dict[str, str]
) -> None:
    """Maps artname, synonyme, and scientific names from a JSON entry to the German artname."""
    art = bird_obj.get("artname", "").strip()
    if art:
        bird_map[art] = target_de_name

    synonyms = bird_obj.get("synonyme", "")
    if isinstance(synonyms, str) and synonyms.strip():
        for syn in synonyms.split(","):
            clean_syn = syn.strip()
            if clean_syn:
                bird_map[clean_syn] = target_de_name

    # Also grab scientific/Latin name if the JSON object includes it (e.g. 'wissname' or 'lat')
    for lat_key in ("wissname", "name_lat", "lat", "scientific_name"):
        lat_val = bird_obj.get(lat_key, "")
        if isinstance(lat_val, str) and lat_val.strip():
            bird_map[lat_val.strip()] = target_de_name


def fetch_bird_vocab() -> dict[str, str]:
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    bird_list: dict[str, str] = {}

    base_url = os.getenv("VOCAB_BASE_URL")

    with httpx.Client(verify=ctx, timeout=15.0) as client:
        data_de = client.get(f"{base_url}/list_de.json").json()
        data_fr = client.get(f"{base_url}/list_fr.json").json()
        data_it = client.get(f"{base_url}/list_it.json").json()

    # Zip across the aligned lists (all map back to de_bird["artname"])
    for de_bird, fr_bird, it_bird in zip(data_de, data_fr, data_it):
        canonical_de = de_bird["artname"].strip()

        _add_entry_to_map(de_bird, canonical_de, bird_list)
        _add_entry_to_map(fr_bird, canonical_de, bird_list)
        _add_entry_to_map(it_bird, canonical_de, bird_list)

    storage_dir = OUTPUT_DIR / "vocab"
    storage_dir.mkdir(exist_ok=True, parents=True)

    with (storage_dir / "bird_vocab.json").open("w", encoding="utf-8") as f:
        json.dump(
            bird_list,
            f,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )

    return bird_list


def get_bird_vocab_dict() -> dict[str, str]:
    vocab_file = OUTPUT_DIR / "vocab" / "bird_vocab.json"
    if not vocab_file.exists():
        return fetch_bird_vocab()

    with vocab_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_bird_vocab_set() -> set[str]:
    return set(get_bird_vocab_dict().keys())
