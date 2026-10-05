"""
Discovery-diff: meldt CBS-tabellen onder onderwijsthema's die nog niet in de
catalogus staan, geclassificeerd op productscope (mbo/hbo/wo). De uitkomst is een
JSON-rapport voor review; er wordt niets automatisch aan de catalogus toegevoegd.

Het rapport scheidt:
  - nieuw_voor_review:   mogelijk mbo/hbo/wo-relevant (inclusief onbekend)
  - uitgesloten:         buiten scope of arbeidsmarkt-only (met motivatie)
  - bestaand:            sectorclassificatie van de bestaande records, actief vs. archief
  - bestaand_niet_gevonden: catalogusitems die de themasweep niet meer vindt
                            (informatief; verdwijnen nooit stilzwijgend)

Gebruik:
  uv run python catalogus/discovery_diff.py
  uv run python catalogus/discovery_diff.py --output pad.json
"""
import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from sector import classificeer_sector, is_toegelaten, ONBEKEND

ROOT = Path(__file__).parent.parent
CATALOG = "https://opendata.cbs.nl/ODataCatalog"
API = "https://opendata.cbs.nl/ODataApi/OData"
CATALOGUS = ROOT / "data/02-prepared/cbs_datasets_ai.json"
DEFAULT_OUTPUT = ROOT / "data/03-output/cbs_discovery.json"
SCHEMA_VERSIE = 1

# Themasweep: zelfde onderwijsthema's als uitbreiden.py, plus extra thema's kunnen hier bij
THEMA_IDS = [
    352, 353, 354, 355, 356, 357, 358, 359, 360, 361, 362, 363, 364, 365,
    366, 367, 368, 369, 370, 371, 372, 373,
    376, 480, 481, 482, 319, 320, 321, 322, 324, 905, 906, 907, 909,
    912, 913, 914, 915, 916, 917, 918, 919, 920, 922, 924, 925, 926, 928, 929, 934,
]
# CBS-miscategorisaties onder onderwijsthema's
UITGESLOTEN_THEMA_NAMEN = {"Toerisme", "Bouwen en wonen", "Hypotheken", "Prijzen"}


def _get(url: str, **params) -> list[dict]:
    params.setdefault("$format", "json")
    r = httpx.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()["value"]


def fetch_sweep(thema_ids: list[int] = THEMA_IDS) -> tuple[dict[str, int], dict[int, str]]:
    """Tables_Themes (JSON) per thema → {tabel-ID: thema-ID}, plus themanamen."""
    namen = {r["ID"]: r["Title"] for r in _get(f"{CATALOG}/Themes")}
    ids: dict[str, int] = {}
    for tid in thema_ids:
        rows = _get(f"{CATALOG}/Tables_Themes", **{"$filter": f"ThemeID eq {tid}", "$select": "TableIdentifier,ThemeID"})
        for r in rows:
            ids.setdefault(r["TableIdentifier"], tid)
    ids = {i: t for i, t in ids.items() if namen.get(t, "") not in UITGESLOTEN_THEMA_NAMEN}
    return ids, namen


def fetch_details(dataset_id: str) -> dict:
    """Titel, frequentie, periode, wijzigingsdatum en dimensietitels van één tabel."""
    info = (_get(f"{API}/{dataset_id}/TableInfos") or [{}])[0]
    props = _get(f"{API}/{dataset_id}/DataProperties")
    return {
        "titel": (info.get("Title") or "").strip(),
        "frequentie": (info.get("Frequency") or "").strip(),
        "periode": (info.get("Period") or "").strip(),
        "modified": str(info.get("Modified") or "")[:10] or None,
        "dimensies": [p["Title"].strip() for p in props if p.get("Type") in ("Dimension", "GeoDimension")],
    }


def _archief(details: dict) -> bool:
    return "stopgezet" in details["frequentie"].lower() or bool(details["modified"] and details["modified"] < "2021-01-01")


def bouw_rapport(catalogus: list[dict], sweep: dict[str, int], themanamen: dict[int, str], details: dict[str, dict]) -> dict:
    """Pure functie: combineert catalogus, themasweep en tabeldetails tot het rapport."""
    in_catalogus = {r["_cbs_id"].upper(): r for r in catalogus}
    nieuw, uitgesloten = [], []
    for tid in sorted(i for i in sweep if i.upper() not in in_catalogus):
        d = details.get(tid)
        thema = themanamen.get(sweep[tid], "")
        if d is None:  # details niet op te halen: een thema alleen is geen bewijs
            d = {"titel": "", "frequentie": "", "periode": "", "modified": None, "dimensies": []}
            kl = {"classificatie": ONBEKEND, "sectoren": [], "arbeidsmarkt_only": False,
                  "motivatie": "tabeldetails niet op te halen; review nodig"}
        else:
            kl = classificeer_sector(d["titel"], thema, d["dimensies"])
        item = {"dataset_id": f"cbs:{tid}", "titel": d["titel"], "thema": thema, "periode": d["periode"],
                "gearchiveerd": _archief(d), "classificatie": kl["classificatie"], "sectoren": kl["sectoren"],
                "motivatie": kl["motivatie"], "arbeidsmarkt_only": kl["arbeidsmarkt_only"]}
        if is_toegelaten(kl["classificatie"]) or kl["classificatie"] == ONBEKEND:
            item["advies"] = "review"
            nieuw.append(item)
        else:
            item["advies"] = "niet_toevoegen"
            uitgesloten.append(item)

    bestaand, per_klasse = [], {}
    for r in catalogus:
        kl = classificeer_sector(r.get("bron", ""), r.get("_thema", ""), r.get("_dimensies", []))
        gecureerd = r.get("onderwijstype", [])
        # Regels en curatie spreken elkaar tegen: curatie zegt zuiver mbo/hbo/wo, regels zeggen buiten scope.
        afwijkend = (kl["classificatie"] == "buiten_scope"
                     and bool(gecureerd) and {t.upper() for t in gecureerd} <= {"MBO", "HBO", "WO"})
        bestaand.append({"dataset_id": f"cbs:{r['_cbs_id']}", "classificatie": kl["classificatie"],
                         "gecureerd_onderwijstype": gecureerd, "gearchiveerd": bool(r.get("_archief")),
                         "motivatie": kl["motivatie"], "afwijkend_van_curatie": afwijkend})
        sleutel = "historisch" if r.get("_archief") else "actief"
        per_klasse.setdefault(kl["classificatie"], {"actief": 0, "historisch": 0})[sleutel] += 1

    return {
        "schema_version": SCHEMA_VERSIE,
        "thema_ids": sorted(set(sweep.values())),
        "samenvatting": {
            "gevonden_in_sweep": len(sweep),
            "al_in_catalogus": len(sweep) - len(nieuw) - len(uitgesloten),
            "nieuw_voor_review": len(nieuw),
            "uitgesloten": len(uitgesloten),
            "uitgesloten_arbeidsmarkt_only": sum(1 for u in uitgesloten if u["arbeidsmarkt_only"]),
            "nieuw_voor_review_actief": sum(1 for n in nieuw if not n["gearchiveerd"]),
            "nieuw_voor_review_historisch": sum(1 for n in nieuw if n["gearchiveerd"]),
            "bestaand_per_classificatie": per_klasse,
            "bestaand_afwijkend_van_curatie": sum(1 for b in bestaand if b["afwijkend_van_curatie"]),
        },
        "nieuw_voor_review": nieuw,
        "uitgesloten": uitgesloten,
        "bestaand": bestaand,
        "bestaand_niet_gevonden": sorted(f"cbs:{i}" for i in (r["_cbs_id"] for r in catalogus) if i.upper() not in {s.upper() for s in sweep}),
    }


def main():
    p = argparse.ArgumentParser(description="Meld nieuwe CBS-tabellen onder onderwijsthema's ter review.")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--fail-on-new", action="store_true", help="exit 3 als er nieuwe tabellen voor review zijn")
    args = p.parse_args()

    catalogus = json.loads(CATALOGUS.read_text(encoding="utf-8"))
    bekend = {r["_cbs_id"].upper() for r in catalogus}
    print("Themasweep via Tables_Themes...")
    sweep, namen = fetch_sweep()
    nieuw_ids = [i for i in sweep if i.upper() not in bekend]
    print(f"{len(sweep)} tabellen in sweep, {len(nieuw_ids)} niet in catalogus; details ophalen...")
    details = {}
    for n, tid in enumerate(nieuw_ids, 1):
        try:
            details[tid] = fetch_details(tid)
        except Exception as e:
            print(f"  [{n}/{len(nieuw_ids)}] {tid} FOUT: {type(e).__name__}")
    rapport = bouw_rapport(catalogus, sweep, namen, details)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    s = rapport["samenvatting"]
    print(f"Review: {s['nieuw_voor_review']} (actief {s['nieuw_voor_review_actief']}), "
          f"uitgesloten: {s['uitgesloten']} → {out}")
    if args.fail_on_new and s["nieuw_voor_review"]:
        sys.exit(3)


if __name__ == "__main__":
    main()
