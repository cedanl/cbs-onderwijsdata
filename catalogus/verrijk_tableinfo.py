"""
Bewaart methodiek, afronding, definities en periodecontext uit officiële CBS-metadata.

Per dataset worden TableInfos (bronbewijs) en de Perioden-dimensie (code, titel,
status) opgehaald en deterministisch (regels, geen AI) omgezet naar gestructureerde
velden met de letterlijke bronpassage erbij. Wat niet in de bron staat blijft
`niet_vermeld`; er wordt niets afgeleid of aangevuld.

Gebruik:
  uv run python catalogus/verrijk_tableinfo.py
  uv run python catalogus/verrijk_tableinfo.py --limit 5
"""
import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from onderwijsdata import client

ROOT = Path(__file__).parent.parent
CATALOGUS = ROOT / "data/02-prepared/cbs_datasets_ai.json"
OUTPUT = ROOT / "data/02-prepared/cbs_tableinfo.json"

SCHEMA_VERSIE = 1
GEVONDEN, NIET_VERMELD = "gevonden", "niet_vermeld"

_SECTIE = re.compile(r"^\s*(\d)\.\s+([A-ZÉËÏ][A-ZÉËÏ ,&/'-]+)\s*$", re.M)
_LINK = re.compile(r"<a href='([^']+)'>(.*?)</a>")
_DATASET_URL = re.compile(r"/dataset/([0-9A-Za-z]+)$")
_GROOTTE = {"tien": 10, "honderd": 100, "duizend": 1000}
_AFROND_GROOTTE = [
    (re.compile(r"afgerond op (\d+)-tallen", re.I), lambda m: int(m.group(1))),
    (re.compile(r"afgerond op (tien|honderd|duizend)tallen", re.I), lambda m: _GROOTTE[m.group(1).lower()]),
    (re.compile(r"afgerond op (\d+) (tien|honderd|duizend)tallen", re.I), lambda m: int(m.group(1)) * _GROOTTE[m.group(2).lower()]),
    (re.compile(r"veelvouden van (\d+)", re.I), lambda m: int(m.group(1))),
]

_PATRONEN = {
    "afronding": re.compile(
        r"afgerond (op|naar|tot|in)\b|\bafronding\b|veelvouden van \d+|(cijfers|aantallen|getallen|bedragen) (zijn|worden) afgerond", re.I),
    "additiviteit": re.compile(
        r"som van|afwijkt van (het )?totaal|niet overeen(stemmen|komen)|"
        r"(bij beide|dubbel|twee ma+len|slechts één ma+l).{0,60}(geteld|meegeteld)|"
        r"(geteld|meegeteld).{0,60}(slechts één ma+l|bij beide)", re.I),
    "definitiebreuken": re.compile(
        r"\bvanaf (studiejaar |schooljaar |het jaar )?\d{4}.{0,120}\b(ook|meegenomen|gewijzigd|aangepast|anders|definitie)", re.I),
}


def _schoon(tekst: str | None) -> str:
    tekst = _LINK.sub(lambda m: m.group(2), tekst or "")
    return re.sub(r"[ \t]+", " ", tekst.replace("\r", "")).strip()


def _secties(description: str) -> dict[int, str]:
    """Splits de Description in genummerde secties (1 = toelichting, 2 = definities, ...)."""
    kopjes = list(_SECTIE.finditer(description))
    result: dict[int, str] = {}
    for i, m in enumerate(kopjes):
        einde = kopjes[i + 1].start() if i + 1 < len(kopjes) else len(description)
        result[int(m.group(1))] = description[m.end():einde].strip()
    return result


def _zinnen(tekst: str) -> list[str]:
    """Zinnen per tekstregel; regels (alinea's) lopen bij CBS niet door in de volgende."""
    zinnen = []
    for regel in _schoon(tekst).split("\n"):
        zinnen += [z.strip() for z in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9'\"(])", regel) if z.strip()]
    return zinnen


def _passages(zinnen: list[str], patroon: re.Pattern) -> list[str]:
    return [z for z in zinnen if patroon.search(z)]


def _definities(sectie2: str) -> list[dict]:
    """Termen uit het blok 'Definities:' (term op eigen regel, daarna de tekst)."""
    blok = re.split(r"Verklaring van symbolen", sectie2, flags=re.I)[0]
    blok = re.sub(r"^\s*Definities:\s*", "", blok.strip(), flags=re.I)
    result = []
    for alinea in re.split(r"\n\s*\n", blok):
        regels = [r.strip() for r in alinea.strip().split("\n") if r.strip()]
        if len(regels) >= 2 and len(regels[0]) < 80 and not regels[0].endswith("."):
            result.append({"term": regels[0], "tekst": _schoon(" ".join(regels[1:]))})
    return result


def _blok(sectie1: str, kop: str) -> str | None:
    m = re.search(rf"{kop}[^\n]*\n(.*?)(?:\n\s*\n[A-Z][^\n]*:|\Z)", sectie1, re.S | re.I)
    return _schoon(m.group(1)) if m and _schoon(m.group(1)) else None


def parse_perioden(rows: list[dict]) -> dict:
    """Volledige periodecodes met officiële status; status wordt nooit afgeleid uit het label."""
    codes = [
        {"code": (r.get("Key") or "").strip(), "titel": (r.get("Title") or "").strip(),
         "status": (r.get("Status") or "").strip() or None,
         "toelichting": (r.get("Description") or "").strip() or None}
        for r in rows
    ]
    jaren = []
    for c in codes:
        m = re.fullmatch(r"(\d{4})(JJ|SJ)00", c["code"])
        if m:
            jaren.append((int(m.group(1)), m.group(2)))
    hiaten = None  # alleen bepaald voor zuivere jaar- of schooljaarreeksen
    if jaren and len(jaren) == len(codes) and len({t for _, t in jaren}) == 1:
        alle = {j for j, _ in jaren}
        hiaten = sorted(set(range(min(alle), max(alle) + 1)) - alle)
    return {"aantal": len(codes), "codes": codes, "hiaten": hiaten}


def parse_tableinfo(cbs_id: str, info: dict, perioden_rows: list[dict], gecontroleerd_op: str) -> dict:
    description = (info.get("Description") or "").replace("\r", "")
    secties = _secties(description)
    toelichting, definitietekst = secties.get(1, ""), secties.get(2, "")
    zinnen = _zinnen(toelichting) + _zinnen(definitietekst)
    kort = _zinnen(info.get("ShortDescription") or "")

    def veld(passages: list[str], **extra) -> dict:
        if not passages:
            return {"status": NIET_VERMELD, "passages": []}
        return {"status": GEVONDEN, "passages": passages, **extra}

    afrond_passages = _passages(zinnen, _PATRONEN["afronding"])
    grondslag = None
    for z in afrond_passages:
        for patroon, waarde in _AFROND_GROOTTE:
            m = patroon.search(z)
            if m:
                grondslag = waarde(m)
                break
        if grondslag:
            break
    afronding = veld(afrond_passages, grondslag=grondslag, exact=False if afrond_passages else None)

    additief_passages = _passages(zinnen, _PATRONEN["additiviteit"])
    additiviteit = veld(additief_passages, additief=False if additief_passages else None)

    publicatie_status = _blok(toelichting, "Status van de cijfers")
    volgende = _blok(toelichting, "Wanneer komen de nieuwe cijfers")

    relaties = []
    for url, titel in _LINK.findall(secties.get(3, "")):
        m = _DATASET_URL.search(url)
        relaties.append({"dataset_id": f"cbs:{m.group(1)}" if m else None, "titel": titel, "url": url,
                         "relatie": "bronlink"})
    methoden = [{"titel": t, "url": u} for u, t in _LINK.findall(secties.get(4, ""))]

    return {
        "_cbs_id": cbs_id,
        "schema_versie": SCHEMA_VERSIE,
        "bronmeta": {
            "modified": info.get("Modified"),
            "metadata_modified": info.get("MetaDataModified"),
            "gecontroleerd_op": gecontroleerd_op,
        },
        "bronbewijs": {
            "bron": "TableInfos",
            "short_description": _schoon(info.get("ShortDescription")),
            "toelichting": _schoon(toelichting),
            "verklaring_symbolen": _schoon(re.split(r"Verklaring van symbolen:?", definitietekst, flags=re.I)[-1])
            if re.search(r"Verklaring van symbolen", definitietekst, re.I) else None,
        },
        "populatie": veld(kort[:1]),
        "definities": _definities(definitietekst),
        "afronding": afronding,
        "additiviteit": additiviteit,
        "definitiebreuken": veld(_passages(zinnen, _PATRONEN["definitiebreuken"])),
        "publicatie": {
            "status": GEVONDEN if publicatie_status or volgende else NIET_VERMELD,
            "status_tekst": publicatie_status,
            "volgende_publicatie": volgende,
        },
        "methoden": methoden,
        "relaties": relaties,
        "perioden": parse_perioden(perioden_rows),
        "verificatie": "bron",
    }


def main():
    p = argparse.ArgumentParser(description="Bewaar TableInfos-methodiek en periodecontext.")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    catalogus = json.loads(CATALOGUS.read_text(encoding="utf-8"))
    bestaand = {}
    if OUTPUT.exists():
        bestaand = {r["_cbs_id"]: r for r in json.loads(OUTPUT.read_text(encoding="utf-8"))}
    vandaag = date.today().isoformat()

    fouten = 0
    for i, rec in enumerate(catalogus[: args.limit], 1):
        cbs_id = rec["_cbs_id"]
        print(f"[{i}/{len(catalogus)}] {cbs_id}", end="", flush=True)
        try:
            info = (client.get(cbs_id, "TableInfos") or [{}])[0]
            try:
                perioden = client.get(cbs_id, "Perioden")
            except Exception:
                perioden = []
            bestaand[cbs_id] = parse_tableinfo(cbs_id, info, perioden, vandaag)
            time.sleep(0.05)
            print(" OK")
        except Exception as e:
            # Mislukte refresh behoudt het laatst-goede record.
            print(f" FOUT: {type(e).__name__}")
            fouten += 1

    ids = [r["_cbs_id"] for r in catalogus]
    uit = [bestaand[i] for i in ids if i in bestaand]
    OUTPUT.write_text(json.dumps(uit, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n{len(uit)} records, {fouten} mislukt → {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
