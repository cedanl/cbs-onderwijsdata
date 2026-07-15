"""
Verrijkt CBS datasets in cbs_datasets_ai.json met LLM-analyse:
kolomtoelichting, waardenbereik, combineerbaar_met, niet_geschikt_voor,
tag-suggesties, voorbeeldvragen en samenvatting.

Gebruik:
  uv run python catalogus/verrijk_catalogus.py
  uv run python catalogus/verrijk_catalogus.py --no-skip-existing
  uv run python catalogus/verrijk_catalogus.py --limit 5
  uv run python catalogus/verrijk_catalogus.py --model openai/gpt-4.1-mini
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from onderwijsdata import client

DIM_TYPES = {"Dimension", "GeoDimension", "TimeDimension"}


def parse_args():
    p = argparse.ArgumentParser(
        description="Verrijkt CBS datasets met LLM-analyse via OpenRouter."
    )
    p.add_argument("--base-url", default="https://openrouter.ai/api/v1",
                   help="API base URL (default: OpenRouter)")
    p.add_argument("--model", default="openai/gpt-4.1-mini",
                   help="LLM model ID (default: openai/gpt-4.1-mini)")
    p.add_argument("--api-key", default=os.environ.get("LLM_API_KEY"),
                   help="API key (default: $LLM_API_KEY)")
    p.add_argument("--input", default="data/02-prepared/cbs_datasets_ai.json",
                   help="Input JSON bestand")
    p.add_argument("--output", default="data/02-prepared/cbs_datasets_enriched.json",
                   help="Output JSON bestand")
    p.add_argument("--no-skip-existing", action="store_true",
                   help="Her-verrijk entries die al kolomtoelichting hebben")
    p.add_argument("--limit", type=int, default=None,
                   help="Verrijk maximaal N entries")
    return p.parse_args()


def fetch_dataset_info(dataset_id: str, dimensies: list[str]) -> dict:
    """Haalt properties, dimensiewaarden (top 20 per dim) en sample data (top 50) op."""
    props = client.properties(dataset_id)
    time.sleep(0.1)

    dimensions = {}
    for dim in dimensies:
        try:
            waarden = client.dimension(dataset_id, dim)
            time.sleep(0.1)
            items = list(waarden.items())[:20]
            dimensions[dim] = {k: v for k, v in items}
        except Exception:
            dimensions[dim] = {}

    try:
        sample_rows = client.data(dataset_id, **{"$top": 50})
        time.sleep(0.1)
    except Exception:
        sample_rows = []

    return {
        "props": props,
        "dimensions": dimensions,
        "sample_rows": sample_rows,
    }


def analyze_dataset(info: dict, entry: dict) -> dict:
    """Analyseer de opgehaalde data: per kolom type, unieke waarden, bereik."""
    sample_rows = info["sample_rows"]
    props = info["props"]
    prop_keys = {p.get("Key"): p for p in props if p.get("Key")}

    kolommen = {}
    for row in sample_rows:
        for col, val in row.items():
            if col not in kolommen:
                kolommen[col] = {
                    "type": None,
                    "unieke_waarden": set(),
                    "min_waarde": None,
                    "max_waarde": None,
                    "null_count": 0,
                    "total_count": 0,
                }
            info_col = kolommen[col]
            info_col["total_count"] += 1

            if val is None or val == "" or val == "NaN":
                info_col["null_count"] += 1
                continue

            info_col["unieke_waarden"].add(str(val))

            if info_col["type"] is None:
                try:
                    num = float(str(val).replace(",", "."))
                    info_col["type"] = "numeriek"
                    info_col["min_waarde"] = num
                    info_col["max_waarde"] = num
                except (ValueError, TypeError):
                    info_col["type"] = "string"
            elif info_col["type"] == "numeriek":
                try:
                    num = float(str(val).replace(",", "."))
                    if info_col["min_waarde"] is None or num < info_col["min_waarde"]:
                        info_col["min_waarde"] = num
                    if info_col["max_waarde"] is None or num > info_col["max_waarde"]:
                        info_col["max_waarde"] = num
                except (ValueError, TypeError):
                    pass

    dimensie_namen = set(entry.get("_dimensies", []))
    meetwaarde_namen = set(entry.get("_meetwaarden", []))

    for col, info_col in kolommen.items():
        unique_list = sorted(info_col["unieke_waarden"])
        info_col["unieke_waarden"] = unique_list[:20]
        info_col["aantal_uniek"] = len(unique_list)
        null_pct = (info_col["null_count"] / info_col["total_count"] * 100
                    if info_col["total_count"] > 0 else 0)
        info_col["null_percentage"] = round(null_pct, 1)
        info_col["is_dimensie"] = col in dimensie_namen
        info_col["is_meetwaarde"] = col in meetwaarde_namen
        del info_col["total_count"]
        del info_col["null_count"]

    dimensie_analyse = {}
    for dim, waarden in info["dimensions"].items():
        dimensie_analyse[dim] = {
            "aantal_categorieen": len(waarden),
            "voorbeelden": list(waarden.values())[:10],
        }

    perioden = []
    if "Perioden" in info["dimensions"]:
        perioden = list(info["dimensions"]["Perioden"].values())

    meetwaarden_bereik = {}
    for col, info_col in kolommen.items():
        if info_col["is_meetwaarde"]:
            meetwaarden_bereik[col] = {
                "min": info_col["min_waarde"],
                "max": info_col["max_waarde"],
                "eenheid": prop_keys.get(col, {}).get("Unit", ""),
            }

    return {
        "kolommen": kolommen,
        "dimensie_analyse": dimensie_analyse,
        "periode": {
            "eerste": perioden[-1] if perioden else None,
            "laatste": perioden[0] if perioden else None,
            "aantal_perioden": len(perioden),
        },
        "meetwaarden_bereik": meetwaarden_bereik,
        "aantal_rijen_sample": len(sample_rows),
    }


def extract_json(text: str):
    """Probeer JSON te parsen, met fallback naar markdown codeblocks."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, TypeError):
            pass
    return None


SYSTEM_PROMPT = """Je bent een expert op het gebied van Nederlandse onderwijsdata van het CBS.
Je analyseert metadata van CBS OData-datasets en geeft bruikbare informatie voor beleids-
en datamedewerkers bij onderwijsinstellingen.

Je antwoordt ALTIJD met een enkel JSON-object, zonder extra tekst, uitleg of markdown.
Gebruik alleen de gevraagde velden."""


def build_user_prompt(entry: dict, analysis: dict) -> str:
    """Bouw het prompt voor de LLM."""
    dim_info = json.dumps(analysis["dimensie_analyse"], ensure_ascii=False, indent=2)
    periode = analysis["periode"]
    meet_bereik = json.dumps(analysis["meetwaarden_bereik"], ensure_ascii=False, indent=2)

    kolom_overzicht = []
    for col, info in analysis["kolommen"].items():
        regel = f"  - {col} (type={info['type']}, uniek={info['aantal_uniek']}"
        if info["is_dimensie"]:
            regel += ", dimensie"
        if info["is_meetwaarde"]:
            regel += ", meetwaarde"
        if info["null_percentage"] > 0:
            regel += f", {info['null_percentage']}% null"
        if info["min_waarde"] is not None:
            regel += f", bereik={info['min_waarde']}-{info['max_waarde']}"
        regel += ")"
        kolom_overzicht.append(regel)

    return f"""Analyseer deze CBS onderwijsdataset en geef verrijkte metadata.

## Dataset-info
- Naam: {entry.get('bron', 'onbekend')}
- CBS ID: {entry.get('_cbs_id', 'onbekend')}
- Periode: {entry.get('periode', 'onbekend')}
- Beschrijving: {entry.get('doel', '')}
- Thema: {entry.get('_thema', '')}
- Bestaande tags: {', '.join(entry.get('tags', []))}

## Dimensie-analyse
{dim_info}

## Periode-info
- Eerste: {periode.get('eerste')}
- Laatste: {periode.get('laatste')}
- Aantal periodes: {periode.get('aantal_perioden')}

## Meetwaarden-bereik
{meet_bereik}

## Kolommen (sample: {analysis['aantal_rijen_sample']} rijen)
{chr(10).join(kolom_overzicht)}

## Taak
Geef een JSON-object met deze velden:
{{
  "samenvatting": "<3-4 zinnen: wat meet deze dataset, voor wie relevant, belangrijkste kenmerken>",
  "voorbeeldvragen": ["<vraag 1>", ..., "<vraag 8-10>"],
  "tags": ["<tag1>", ..., "<tag5-8>"],
  "niet_geschikt_voor": ["<toepassing 1>", ..., "<toepassing 2-3>"],
  "kolomtoelichting": {{"<kolomnaam>": "<toelichting in het Nederlands>", ...}},
  "waardenbereik": {{"<kolomnaam>": {{"min": ..., "max": ..., "beschrijving": "..."}}, ...}},
  "combineerbaar_met": ["<beschrijving van andere CBS datasets waar deze mee gecombineerd kan worden>", ...]
}}

Voorbeelden moeten concrete analyevragen zijn die een beleidsmedewerker zou stellen.
Tags zijn korte trefwoorden voor zoekbaarheid.
niet_geschikt_voor: noem 2-3 toepassingen die NIET geschikt zijn voor deze dataset.
kolomtoelichting: een toelichting per kolom die in het dataset-overzicht staat.
waardenbereik: voor meetwaarden het bereik, voor dimensies een beschrijving van de categorieën.
combineerbaar_met: andere CBS datasets of typen analyses die goed combineren."""


def enrich_with_llm(entry: dict, analysis: dict, base_url: str, model: str,
                    api_key: str) -> dict | None:
    """Stuur analyse naar LLM en retourneer de verrijkte metadata."""
    user_prompt = build_user_prompt(entry, analysis)

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2048,
    }

    try:
        r = httpx.post(url, json=payload, headers=headers, timeout=120)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f"  HTTP fout {e.response.status_code}: {e.response.text[:200]}")
        return None
    except httpx.RequestError as e:
        print(f"  Netwerk fout: {e}")
        return None

    try:
        body = r.json()
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        print(f"  Onverwacht response-formaat: {e}")
        return None

    result = extract_json(text)
    if result is None:
        print(f"  JSON parse mislukt, eerste 200 tekens: {text[:200]}")
        return None

    return result


def main():
    args = parse_args()

    if not args.api_key:
        print("FOUT: Geen API key. Gebruik --api-key of zet $LLM_API_KEY.")
        sys.exit(1)

    root = Path(__file__).parent.parent
    input_path = root / args.input
    output_path = root / args.output

    with open(input_path, encoding="utf-8") as f:
        datasets = json.load(f)
    print(f"Input geladen: {len(datasets)} entries uit {args.input}")

    existing = {}
    if output_path.exists():
        with open(output_path, encoding="utf-8") as f:
            existing = {e["_cbs_id"]: e for e in json.load(f)}
        print(f"Bestaande output: {len(existing)} entries")

    total = len(datasets)
    processed = 0
    skipped = 0
    enriched = 0
    failed = 0

    for idx, entry in enumerate(datasets, 1):
        cbs_id = entry["_cbs_id"]
        naam = entry.get("bron", "onbekend")

        if not args.no_skip_existing and cbs_id in existing:
            existing_entry = existing[cbs_id]
            if existing_entry.get("kolomtoelichting"):
                enriched += 1
                continue

        if args.limit is not None and processed >= args.limit:
            break

        processed += 1
        print(f"[{idx}/{total}] {cbs_id} - {naam}", end="", flush=True)

        if entry.get("_archief"):
            print(" (overgeslagen, gearchiveerd)")
            skipped += 1
            continue

        dimensies = entry.get("_dimensies", [])
        if not dimensies:
            print(" (overgeslagen, geen dimensies)")
            skipped += 1
            continue

        try:
            info = fetch_dataset_info(cbs_id, dimensies)
        except Exception as e:
            print(f" FOUT bij ophalen: {e}")
            failed += 1
            continue

        analysis = analyze_dataset(info, entry)

        llm_result = enrich_with_llm(entry, analysis, args.base_url, args.model,
                                     args.api_key)
        if llm_result is None:
            print(" FOUT bij LLM-verrijking")
            failed += 1
            continue

        if cbs_id in existing:
            result_entry = existing[cbs_id]
        else:
            result_entry = dict(entry)

        result_entry["samenvatting"] = llm_result.get("samenvatting", "")
        result_entry["voorbeeldvragen"] = llm_result.get("voorbeeldvragen", [])
        result_entry["tags"] = llm_result.get("tags", entry.get("tags", []))
        result_entry["niet_geschikt_voor"] = llm_result.get("niet_geschikt_voor", [])
        result_entry["kolomtoelichting"] = llm_result.get("kolomtoelichting", {})
        result_entry["waardenbereik"] = llm_result.get("waardenbereik", {})
        result_entry["combineerbaar_met"] = llm_result.get("combineerbaar_met", [])
        result_entry["_llm_model"] = args.model

        existing[cbs_id] = result_entry
        enriched += 1
        print(f" ✓ ({len(llm_result.get('kolomtoelichting', {}))} kolommen)")

        output_list = [existing[e["_cbs_id"]] for e in datasets
                       if e["_cbs_id"] in existing]
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_list, f, ensure_ascii=False, indent=2)

    print(f"\nSamenvatting:")
    print(f"  Verrijkt:  {enriched}")
    print(f"  Overgeslagen: {skipped}")
    print(f"  Mislukt:   {failed}")
    print(f"  Totaal:    {len(existing)} → {output_path}")


if __name__ == "__main__":
    main()
