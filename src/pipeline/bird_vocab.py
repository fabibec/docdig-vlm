import ssl

import httpx
import orjson
import truststore

from pipeline.shared import OUTPUT_DIR


def get_bird_vocab():
    # Use system certificate stores
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client = httpx.Client(verify=ctx)

    response = client.get(
        "https://www.vogelwarte.ch/wp-content/assets/json/bird/list_de.json"
    )
    data = orjson.loads(response.content)
    bird_list = {}
    for bird in data:
        art = bird["artname"]
        synonyms = bird["synonyme"]
        bird_list[art] = art
        for syn in synonyms:
            bird_list[syn] = art

    storage_dir = OUTPUT_DIR / "vocab"
    storage_dir.mkdir(exist_ok=True, parents=True)

    (storage_dir / "bird_vocab.json").write_bytes(
        orjson.dumps(
            bird_list,
            option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS | orjson.OPT_APPEND_NEWLINE,
        )
    )
