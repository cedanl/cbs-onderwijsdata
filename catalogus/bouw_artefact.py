"""
Valideert de catalogusbestanden in data/02-prepared en publiceert ze als één
consistent artefact naar het package (src/onderwijsdata/data) en de site (docs/).

Alleen gevalideerde bestanden worden gepubliceerd. Faalt de validatie, dan blijft
het laatst-goede artefact staan en eindigt het script met een foutmelding.
Het manifest bevat bewust geen commit en geen bouwtijdstip: de uitkomst is
deterministisch, en de chat legt zelf zijn geïnstalleerde commit vast.

Gebruik:
  uv run python catalogus/bouw_artefact.py            # valideer + publiceer
  uv run python catalogus/bouw_artefact.py --check    # alleen valideren (CI)
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from onderwijsdata import __version__, contract

SCHEMA_VERSIE = 1
MANIFEST = "cbs_manifest.json"
# bestand in data/02-prepared → bestanden in het package
PACKAGE_BESTANDEN = (
    "cbs_datasets.json", "cbs_datasets_ai.json", "cbs_datasets_enriched.json", "cbs_tableinfo.json",
)
# De site toont de AI-catalogus als data.json
SITE_BESTANDEN = {"cbs_datasets_ai.json": "data.json", MANIFEST: "catalogus_manifest.json"}


def _sha(pad: Path) -> str:
    return hashlib.sha256(pad.read_bytes()).hexdigest()


def _laad(pad: Path) -> list[dict]:
    return json.loads(pad.read_text(encoding="utf-8"))


def valideer(prepared: Path) -> tuple[list[str], list[str]]:
    """Retourneert (fouten, waarschuwingen). Fouten blokkeren publicatie."""
    fouten: list[str] = []
    waarschuwingen: list[str] = []
    data: dict[str, list[dict]] = {}
    for naam in PACKAGE_BESTANDEN:
        pad = prepared / naam
        if not pad.exists():
            fouten.append(f"{naam}: ontbreekt")
            continue
        try:
            data[naam] = _laad(pad)
        except json.JSONDecodeError as e:
            fouten.append(f"{naam}: ongeldige JSON ({e})")
    if fouten:
        return fouten, waarschuwingen

    for naam, recs in data.items():
        if not recs:
            fouten.append(f"{naam}: leeg")
        ids = [r.get("_cbs_id") for r in recs]
        if len(ids) != len(set(ids)):
            fouten.append(f"{naam}: dubbele _cbs_id's")
    if fouten:
        return fouten, waarschuwingen

    id_sets = {naam: {r["_cbs_id"] for r in recs} for naam, recs in data.items()}
    basis = id_sets["cbs_datasets_ai.json"]
    for naam, ids in id_sets.items():
        if ids != basis:
            fouten.append(f"{naam}: ID's wijken af van cbs_datasets_ai.json "
                          f"(+{len(ids - basis)}/-{len(basis - ids)})")

    ai = {r["_cbs_id"]: r for r in data["cbs_datasets_ai.json"]}
    info = {r["_cbs_id"]: r for r in data["cbs_tableinfo.json"]}
    for r in data["cbs_datasets_enriched.json"]:
        bron = ai.get(r["_cbs_id"], {})
        for veld in ("_laatste_update", "_archief", "_meetwaarden", "_dimensies"):
            if r.get(veld) != bron.get(veld):
                fouten.append(f"enriched {r['_cbs_id']}: {veld} volgt de bron niet")
    for r in data["cbs_datasets_enriched.json"]:
        for key in r.get("_meetwaarden", []):
            if key not in r.get("_meetwaarden_details", {}):
                fouten.append(f"enriched {r['_cbs_id']}: meetwaarde {key} zonder details")
        contract_fouten = contract.valideer_record(contract._record(r, info.get(r["_cbs_id"])))
        fouten += [f"contract {r['_cbs_id']}: {f}" for f in contract_fouten]

    zonder_stempel = sum(1 for r in data["cbs_datasets_enriched.json"] if "_verrijking" not in r)
    if zonder_stempel:
        waarschuwingen.append(f"{zonder_stempel} enriched-records met onvolledige afleiding (geen _verrijking)")
    return fouten, waarschuwingen


def bouw_manifest(prepared: Path, waarschuwingen: list[str] | None = None) -> dict:
    data = {naam: _laad(prepared / naam) for naam in PACKAGE_BESTANDEN}
    info = {r["_cbs_id"]: r for r in data["cbs_tableinfo.json"]}
    records = [contract._record(r, info.get(r["_cbs_id"])) for r in data["cbs_datasets_enriched.json"]]
    bestanden = {
        naam: {"sha256": _sha(prepared / naam), "records": len(data[naam])} for naam in PACKAGE_BESTANDEN
    }
    revisie = hashlib.sha256(
        "".join(f"{n}:{b['sha256']}" for n, b in sorted(bestanden.items())).encode()
    ).hexdigest()
    ai = data["cbs_datasets_ai.json"]
    return {
        "schema_version": SCHEMA_VERSIE,
        "generator": {"naam": "catalogus/bouw_artefact.py", "package_versie": __version__},
        "catalogusrevisie": revisie[:16],
        "bestanden": bestanden,
        "aantallen": {
            "breed": len(ai),
            "per_scopeprofiel": {
                **{s: sum(1 for r in records if r["scopeprofiel"][s] == contract.SUPPORTED) for s in contract.SECTOREN},
                "review": sum(1 for r in records
                              if any(v == contract.UNKNOWN for v in r["scopeprofiel"].values())
                              and not any(v == contract.SUPPORTED for v in r["scopeprofiel"].values())),
            },
        },
        # Drie aparte vragen: wat dekt de data, wat veranderde in de bron, wanneer is voor het laatst gecontroleerd.
        "datadekking": {
            "gearchiveerd": sum(1 for r in ai if r.get("_archief")),
            "actueel": sum(1 for r in ai if not r.get("_archief")),
            "met_tableinfo": len(info),
            "met_periodecodes": sum(1 for i in info.values() if i["perioden"]["aantal"]),
        },
        "bronwijziging": {
            "laatste_update_max": max((r.get("_laatste_update") or "" for r in ai), default="") or None,
            "metadata_modified_max": max((i["bronmeta"].get("metadata_modified") or "" for i in info.values()),
                                         default="") or None,
        },
        "laatste_succesvolle_controle": max((i["bronmeta"].get("gecontroleerd_op") or "" for i in info.values()),
                                            default="") or None,
        "metadataherkomst": {
            "bronmetadata": "CBS TableInfos/DataProperties/Perioden",
            "afgeleid": "scopeprofiel, geografie (deterministisch)",
            "ai_annotaties": "doel, samenvatting, voorbeeldvragen, tags (cbs_datasets_ai.json)",
        },
        "waarschuwingen": waarschuwingen or [],
    }


def _schrijf_atomair(pad: Path, inhoud: bytes) -> None:
    pad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pad.with_name(pad.name + ".tmp")
    tmp.write_bytes(inhoud)
    tmp.replace(pad)


def publiceer(prepared: Path, package_dir: Path, docs_dir: Path | None = None) -> dict:
    """Valideer, schrijf het manifest en kopieer alles naar package (en site).

    Gooit ``ValueError`` bij validatiefouten; er wordt dan niets overschreven.
    """
    fouten, waarschuwingen = valideer(prepared)
    if fouten:
        raise ValueError("artefact ongeldig; laatst-goede versie blijft staan:\n  " + "\n  ".join(fouten[:20]))
    manifest = bouw_manifest(prepared, waarschuwingen)
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (prepared / MANIFEST).write_bytes(manifest_bytes)

    for naam in PACKAGE_BESTANDEN:
        _schrijf_atomair(package_dir / naam, (prepared / naam).read_bytes())
    _schrijf_atomair(package_dir / MANIFEST, manifest_bytes)
    if docs_dir is not None:
        for bron, doel in SITE_BESTANDEN.items():
            _schrijf_atomair(docs_dir / doel, (prepared / bron).read_bytes())
    return manifest


def main():
    p = argparse.ArgumentParser(description="Valideer en publiceer het catalogusartefact.")
    p.add_argument("--check", action="store_true", help="alleen valideren, niets schrijven")
    p.add_argument("--no-docs", action="store_true", help="site (docs/) niet bijwerken")
    args = p.parse_args()
    prepared = ROOT / "data/02-prepared"

    if args.check:
        fouten, waarschuwingen = valideer(prepared)
        for w in waarschuwingen:
            print(f"WAARSCHUWING: {w}")
        for f in fouten:
            print(f"FOUT: {f}")
        sys.exit(1 if fouten else 0)

    try:
        manifest = publiceer(prepared, ROOT / "src/onderwijsdata/data", None if args.no_docs else ROOT / "docs")
    except ValueError as e:
        print(f"FOUT: {e}", file=sys.stderr)
        sys.exit(1)
    for w in manifest["waarschuwingen"]:
        print(f"WAARSCHUWING: {w}")
    print(f"Gepubliceerd: revisie {manifest['catalogusrevisie']}, {manifest['aantallen']['breed']} datasets")


if __name__ == "__main__":
    main()
