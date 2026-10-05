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
import hashlib
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

# Lagen in een enriched-record:
#   1. bronmetadata + AI-annotaties: komen bij elke run ongewijzigd uit de input
#   2. deterministisch afgeleid (hieronder): alleen herbouwd als _verrijking verouderd is
AFGELEIDE_VELDEN = ("_kolommen", "_kolomtypes", "_periode_waarden", "niet_geschikt_voor", "_verrijking")
VERRIJKING_VERSIE = 1
# Velden waaruit de afgeleide laag wordt opgebouwd; een wijziging hierin invalideert die laag.
INPUT_VELDEN = ("_dimensies", "_meetwaarden", "_meetwaarden_details", "_geo_niveau")


def parse_args():
    p = argparse.ArgumentParser(description="Verrijkt CBS catalogus met data-metadata.")
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    p.add_argument("--no-skip-existing", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    return p.parse_args()


def fetch_dimensions(dataset_id: str, dim_names: list[str], fouten: list[str] | None = None) -> dict[str, dict]:
    result = {}
    for dim in dim_names:
        try:
            result[dim] = client.dimension(dataset_id, dim)
            time.sleep(0.05)
        except Exception as e:
            print(f" WARN {dim}: {type(e).__name__}", end="")
            result[dim] = {}
            if fouten is not None:
                fouten.append(dim)
    return result


def fetch_definitions(dataset_id: str, fouten: list[str] | None = None) -> dict[str, dict]:
    try:
        return client.definitions(dataset_id)
    except Exception as e:
        print(f" WARN defs: {type(e).__name__}", end="")
        if fouten is not None:
            fouten.append("definitions")
        return {}


def verrijking_stempel(entry: dict) -> dict:
    """Versie + inputhash van de afgeleide laag, zonder tijdstempel (idempotent)."""
    payload = json.dumps({v: entry.get(v) for v in INPUT_VELDEN}, sort_keys=True, ensure_ascii=False)
    return {
        "versie": VERRIJKING_VERSIE,
        "inputhash": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16],
    }


def is_actueel(bestaand: dict | None, entry: dict) -> bool:
    """De afgeleide laag is actueel als versie en inputhash overeenkomen met de bron."""
    return bool(
        bestaand
        and bestaand.get("_kolommen")
        and bestaand.get("_verrijking") == verrijking_stempel(entry)
    )


def ververs_bronvelden(bestaand: dict, entry: dict) -> dict:
    """Bronlaag uit de input, afgeleide laag uit het bestaande record.

    Zo volgt enriched altijd de gekozen bronmetadata (_laatste_update, _archief,
    AI-annotaties), ook als de dure afleiding wordt overgeslagen.
    """
    record = dict(entry)
    for veld in AFGELEIDE_VELDEN:
        if veld in bestaand:
            record[veld] = bestaand[veld]
    if "samenvatting" not in record and "samenvatting" in bestaand:
        record["samenvatting"] = bestaand["samenvatting"]
    return record


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


def build_niet_geschikt_voor(entry: dict) -> str | None:
    """Beperking op basis van een gecontroleerde regel: alleen landelijke dekking.

    Leeftijd/archief hoort niet hier: dat staat gestructureerd in `_archief` en
    `_periode_waarden`. Onbekende dekking (`_geo_niveau` leeg) geeft géén beperking.
    """
    if entry.get("_geo_niveau") == ["landelijk"]:
        return (
            "Niet geschikt voor analyses op gemeente-, wijk- of schoolniveau"
            " — alleen landelijke totalen beschikbaar."
        )
    return None


def build_samenvatting(entry: dict) -> str:
    """Bouwt een factuele één-regel beschrijving vanuit beschikbare metadata."""
    bron = entry.get("bron", "CBS dataset")
    dimensies = [d for d in entry.get("_dimensies", []) if d != "Perioden"]
    geo = entry.get("_geo_niveau", [])
    periode = entry.get("_periode_waarden", [])

    dim_str = ", ".join(dimensies) if dimensies else "meerdere dimensies"
    geo_str = ", ".join(geo) if geo else "landelijk"

    if len(periode) >= 2:
        periode_str = f"{periode[0]}–{periode[-1]}"
    elif len(periode) == 1:
        periode_str = periode[0]
    else:
        periode_str = "onbekend"

    return f"CBS dataset over {bron} met {dim_str} per {geo_str}, periode {periode_str}."


def enrich_entry(entry: dict) -> dict:
    cbs_id = entry.get("_cbs_id", "")
    dim_names = entry.get("_dimensies", [])
    meetwaarden = entry.get("_meetwaarden", [])

    if not dim_names:
        return entry

    fouten: list[str] = []
    dimensions = fetch_dimensions(cbs_id, dim_names, fouten)
    definitions = fetch_definitions(cbs_id, fouten)
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

    # Altijd herberekenen: een oude waarde mag een gewijzigde regelinput niet blokkeren.
    ngv = build_niet_geschikt_voor(entry)
    if ngv:
        entry["niet_geschikt_voor"] = ngv
    else:
        entry.pop("niet_geschikt_voor", None)
    entry.setdefault("samenvatting", build_samenvatting(entry))
    # Alleen een volledig geslaagde afleiding krijgt een stempel; een gedeeltelijke
    # blijft "verouderd" en wordt bij de volgende run opnieuw geprobeerd.
    if not fouten:
        entry["_verrijking"] = verrijking_stempel(entry)

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
    refreshed = 0
    failed = 0

    for idx, entry in enumerate(datasets, 1):
        cbs_id = entry["_cbs_id"]

        if not args.no_skip_existing and is_actueel(existing.get(cbs_id), entry):
            # Afleiding is actueel, maar de bronlaag wordt altijd ververst.
            existing[cbs_id] = ververs_bronvelden(existing[cbs_id], entry)
            refreshed += 1
            continue

        if args.limit is not None and processed >= args.limit:
            break

        if entry.get("_archief") and cbs_id not in existing:
            skipped += 1
            continue

        processed += 1
        print(f"[{idx}/{len(datasets)}] {cbs_id} - {entry.get('bron', '?')[:60]}", end="", flush=True)

        try:
            enriched = enrich_entry(dict(entry))
            bestaand = existing.get(cbs_id)
            if "_verrijking" not in enriched and bestaand and "_verrijking" in bestaand:
                # Gedeeltelijk mislukte refresh: behoud de laatst-goede afleiding.
                enriched = ververs_bronvelden(bestaand, entry)
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
    print(f"\nKlaar: {processed} verrijkt, {refreshed} bronvelden ververst, {skipped} overgeslagen, {failed} mislukt → {args.output}")


if __name__ == "__main__":
    main()
