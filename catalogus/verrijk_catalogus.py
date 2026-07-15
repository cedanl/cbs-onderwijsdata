"""
Verrijkt CBS catalogus met gestructureerde metadata uit de echte data.
Puur Python — geen LLM nodig. Haalt per dataset dimensiewaarden,
kolomtypes en voorbeeldwaarden op via de CBS OData API.

Gebruik:
  uv run python catalogus/verrijk_catalogus.py
  uv run python catalogus/verrijk_catalogus.py --no-skip-existing
  uv run python catalogus/verrijk_catalogus.py --limit 5
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from onderwijsdata import client

ROOT = Path(__file__).parent.parent
DEFAULT_INPUT = "data/02-prepared/cbs_datasets_ai.json"
DEFAULT_OUTPUT = "data/02-prepared/cbs_datasets_enriched.json"

TOP_N_VALUES = 25


def parse_args():
    p = argparse.ArgumentParser(description="Verrijkt CBS catalogus met data-metadata.")
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--no-skip-existing", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    return p.parse_args()


def fetch_dimensions(dataset_id: str, dim_names: list[str]) -> dict[str, dict]:
    result = {}
    for dim in dim_names:
        try:
            result[dim] = client.dimension(dataset_id, dim)
            time.sleep(0.05)
        except Exception as e:
            print(f" WARN dim {dim}: {e}", end="")
            result[dim] = {}
    return result


def fetch_definitions(dataset_id: str) -> dict[str, dict]:
    try:
        return client.definitions(dataset_id)
    except Exception as e:
        print(f" WARN defs: {e}", end="")
        return {}


def build_kolommen(dimensions: dict, definitions: dict, dim_names: list, meetwaarden: list) -> dict:
    kolommen = {}
    for dim in dim_names:
        if dim == "Perioden":
            continue
        waarden = dimensions.get(dim, {})
        labels = list(waarden.values())[:TOP_N_VALUES]
        if labels:
            kolommen[dim] = labels

    for mw in meetwaarden:
        defn = definitions.get(mw, {})
        info = defn.get("title", mw)
        unit = defn.get("unit", "")
        if unit:
            info += f" ({unit})"
        kolommen[mw] = info

    return kolommen


def build_kolomtypes(definitions: dict, dim_names: list, meetwaarden: list) -> dict:
    types = {}
    for dim in dim_names:
        defn = definitions.get(dim, {})
        odata_type = defn.get("type", "")
        if "Geo" in odata_type:
            types[dim] = "geo-dimensie"
        elif "Time" in odata_type:
            types[dim] = "tijd-dimensie"
        else:
            types[dim] = "dimensie"
    for mw in meetwaarden:
        defn = definitions.get(mw, {})
        unit = defn.get("unit", "")
        types[mw] = f"meetwaarde ({unit})" if unit else "meetwaarde"
    return types


def enrich_entry(entry: dict) -> dict:
    cbs_id = entry.get("_cbs_id", "")
    dim_names = entry.get("_dimensies", [])
    meetwaarden = entry.get("_meetwaarden", [])

    if not dim_names:
        return entry

    dimensions = fetch_dimensions(cbs_id, dim_names)
    definitions = fetch_definitions(cbs_id)
    time.sleep(0.05)

    kolommen = build_kolommen(dimensions, definitions, dim_names, meetwaarden)
    if kolommen:
        entry["_kolommen"] = kolommen

    kolomtypes = build_kolomtypes(definitions, dim_names, meetwaarden)
    if kolomtypes:
        entry["_kolomtypes"] = kolomtypes

    if "Perioden" in dimensions and dimensions["Perioden"]:
        labels = list(dimensions["Perioden"].values())
        entry["_periode_waarden"] = [labels[0], labels[-1]] if len(labels) > 1 else labels

    return entry


def _save(datasets, existing, output_path):
    output_list = [existing.get(e["_cbs_id"], e) for e in datasets]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_list, f, ensure_ascii=False, indent=2)


def main():
    args = parse_args()
    input_path = ROOT / args.input
    output_path = ROOT / args.output

    with open(input_path, encoding="utf-8") as f:
        datasets = json.load(f)
    print(f"Input: {len(datasets)} datasets uit {args.input}")

    existing = {}
    if output_path.exists():
        with open(output_path, encoding="utf-8") as f:
            existing = {e["_cbs_id"]: e for e in json.load(f)}
        print(f"Bestaande output: {len(existing)} entries")

    processed = 0
    skipped = 0
    failed = 0

    for idx, entry in enumerate(datasets, 1):
        cbs_id = entry["_cbs_id"]

        if not args.no_skip_existing and cbs_id in existing and existing[cbs_id].get("_kolommen"):
            skipped += 1
            continue

        if args.limit is not None and processed >= args.limit:
            break

        if entry.get("_archief"):
            skipped += 1
            continue

        processed += 1
        print(f"[{idx}/{len(datasets)}] {cbs_id} - {entry.get('bron', '?')[:60]}", end="", flush=True)

        try:
            enriched = enrich_entry(dict(entry))
            existing[cbs_id] = enriched
            n_cols = len(enriched.get("_kolommen", {}))
            print(f" OK ({n_cols} kolommen)")
        except Exception as e:
            print(f" FOUT: {e}")
            failed += 1
            continue

        if processed % 10 == 0:
            _save(datasets, existing, output_path)

    _save(datasets, existing, output_path)
    print(f"\nKlaar: {processed} verrijkt, {skipped} overgeslagen, {failed} mislukt → {args.output}")


if __name__ == "__main__":
    main()
