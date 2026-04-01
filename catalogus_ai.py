"""
Verrijkt cbs_datasets.json met AI-samenvattingen via de Batches API.
Goedkoopste aanpak: 50% korting, asynchroon, ~$0.05 totaal met Haiku.

Gebruik:
  uv run python catalogus_ai.py submit   # Dien batch in, sla batch_id op
  uv run python catalogus_ai.py collect  # Haal resultaten op en sla op
"""
import json
import sys
import time
import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

MODEL    = "claude-haiku-4-5"
DATASETS = "cbs_datasets.json"
OUTPUT   = "cbs_datasets_ai.json"
ID_FILE  = ".batch_id"

SYSTEM = """Je bent een assistent die CBS onderwijsdatasets samenvat voor Nederlandse
beleids- en datamedewerkers bij onderwijsinstellingen. Geef altijd antwoord als JSON
zonder extra tekst of markdown."""

def make_prompt(entry: dict) -> str:
    return f"""Geef een samenvatting van deze CBS onderwijsdataset voor een beleidsmedewerker.

Dataset: {entry['bron']}
Periode: {entry.get('periode', 'onbekend')}
Beschrijving: {entry['doel']}
Thema: {entry.get('_thema', '')}

Geef terug als JSON met exact deze sleutels:
{{
  "samenvatting": "<2 zinnen: wat meet deze dataset en voor wie is het relevant>",
  "voorbeeldvragen": ["<vraag 1>", "<vraag 2>", "<vraag 3>"],
  "tags": ["<tag1>", "<tag2>", "..."]
}}

Tags zijn korte trefwoorden zoals: instroom, uitval, geslacht, regio, niveau, mbo, hbo, wo, po, vo, arbeidsmarkt, financiering."""


def submit():
    with open(DATASETS) as f:
        datasets = json.load(f)

    client = anthropic.Anthropic()

    requests = [
        Request(
            custom_id=entry["_cbs_id"],
            params=MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=512,
                system=SYSTEM,
                messages=[{"role": "user", "content": make_prompt(entry)}],
            ),
        )
        for entry in datasets
    ]

    batch = client.messages.batches.create(requests=requests)
    with open(ID_FILE, "w") as f:
        f.write(batch.id)

    print(f"Batch ingediend: {batch.id}")
    print(f"Status: {batch.processing_status}")
    print(f"Gebruik 'uv run python catalogus_ai.py collect' om resultaten op te halen.")


def collect():
    try:
        with open(ID_FILE) as f:
            batch_id = f.read().strip()
    except FileNotFoundError:
        print("Geen batch gevonden. Run eerst: uv run python catalogus_ai.py submit")
        sys.exit(1)

    client = anthropic.Anthropic()

    # Wacht tot batch klaar is
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        print(f"Status: {batch.processing_status} | "
              f"gereed: {batch.request_counts.succeeded} | "
              f"bezig: {batch.request_counts.processing}")
        if batch.processing_status == "ended":
            break
        time.sleep(10)

    # Resultaten ophalen
    results = {}
    for result in client.messages.batches.results(batch_id):
        if result.result.type == "succeeded":
            text = next((b.text for b in result.result.message.content if b.type == "text"), "")
            try:
                results[result.custom_id] = json.loads(text)
            except json.JSONDecodeError:
                print(f"  JSON parse fout voor {result.custom_id}: {text[:100]}")

    # Verrijken
    with open(DATASETS) as f:
        datasets = json.load(f)

    for entry in datasets:
        cbs_id = entry["_cbs_id"]
        if cbs_id in results:
            ai = results[cbs_id]
            entry["doel"]             = ai.get("samenvatting", entry["doel"])
            entry["voorbeeldvragen"]  = ai.get("voorbeeldvragen", [])
            entry["tags"]             = ai.get("tags", [])

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(datasets, f, ensure_ascii=False, indent=2)

    print(f"\nKlaar: {len(results)}/{len(datasets)} datasets verrijkt → {OUTPUT}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "submit":
        submit()
    elif cmd == "collect":
        collect()
    else:
        print("Gebruik: uv run python catalogus_ai.py [submit|collect]")
