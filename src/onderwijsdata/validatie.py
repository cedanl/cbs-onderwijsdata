"""Details- en validatielaag boven properties/dimension/definitions/data.

Offline cataloguscheck eerst; live detailcheck (gecachet) alleen met ``live=True``.
Een titel die lijkt op een andere titel is nooit bewijs: ambiguïteit leidt tot een
verduidelijkingsvraag, ontbrekende kennis tot ``unknown`` en tijdelijke bronuitval
nooit tot "dataset bestaat niet".
"""
import difflib

from . import contract
from .contract import SUPPORTED, UNSUPPORTED, UNKNOWN

# Periodetypes en hun CBS-formaatcode; prognosejaar heeft geen eigen code.
PERIODE_FORMAAT = {"jaar": "JJ", "schooljaar": "SJ", "kwartaal": "KW", "maand": "MM"}
GEO_NIVEAUS = ("landelijk", "corop", "provincie", "gemeente", "landsdeel")


def _check(veld: str, status: str, reden: str | None = None, **extra) -> dict:
    return {"veld": veld, "status": status, "reden": reden, **extra}


def _suggesties(gezocht: str, opties: list[str], n: int = 3) -> list[str]:
    return difflib.get_close_matches(gezocht, opties, n=n, cutoff=0.5)


def _samenvatten(checks: list[dict]) -> str:
    statussen = {c["status"] for c in checks}
    if UNSUPPORTED in statussen:
        return UNSUPPORTED
    if UNKNOWN in statussen:
        return UNKNOWN
    return SUPPORTED


def _check_sector(rec: dict, sector: str) -> dict:
    sector = sector.lower()
    if sector not in contract.SECTOREN:
        return _check("sector", UNSUPPORTED, f"{sector!r} valt buiten mbo/hbo/wo", fout="ongeldige_sector",
                      herstel=list(contract.SECTOREN))
    status = rec["scopeprofiel"][sector]
    if status == UNSUPPORTED:
        return _check("sector", UNSUPPORTED, f"tabel dekt {sector} niet ({rec['onderwijssectoren']})")
    if status == UNKNOWN:
        return _check("sector", UNKNOWN, "sectordeel niet aantoonbaar apart selecteerbaar (gemengde of brede tabel)")
    secs = {s.lower() for s in rec["onderwijssectoren"]}
    if sector in ("hbo", "wo") and {"hbo", "wo"} <= secs:
        if not any("onderwijssoort" in d.lower() for d in rec["dimensies"]):
            return _check("sector", UNKNOWN, "HO-label bewijst geen aparte hbo/wo-cijfers; geen uitsplitsende dimensie bekend")
    return _check("sector", SUPPORTED)


def _check_geografie(rec: dict, niveau: str) -> dict:
    geo = rec["geografie"]
    if geo["status"] == UNSUPPORTED:
        return _check("geografie", UNSUPPORTED, "tabel heeft geen regiodimensie", fout="niet_ondersteund_niveau")
    if geo["status"] == UNKNOWN:
        return _check("geografie", UNKNOWN, geo.get("reden"))
    if niveau not in geo["niveaus"]:
        return _check("geografie", UNSUPPORTED, f"niveau {niveau!r} niet beschikbaar", fout="niet_ondersteund_niveau",
                      herstel=geo["niveaus"])
    return _check("geografie", SUPPORTED)


def _check_periode(rec: dict, periode: dict) -> dict:
    soort = periode.get("type")
    formaat = rec["perioden"]["formaat"]
    if soort not in PERIODE_FORMAAT:
        reden = ("prognosejaar heeft geen conversieregel naar de tabelperiode" if soort == "prognosejaar"
                 else f"onbekend periodetype {soort!r}")
        return _check("periode", UNSUPPORTED if soort == "prognosejaar" else UNKNOWN, reden, fout="periodetype")
    if not formaat:
        return _check("periode", UNKNOWN, "periodeformaat van de tabel onbekend")
    if PERIODE_FORMAAT[soort] not in formaat:
        return _check("periode", UNSUPPORTED,
                      f"{soort} ({PERIODE_FORMAAT[soort]}) is niet gelijk aan tabelperiode {formaat}; geen conversieregel",
                      fout="periodetype", herstel=formaat)
    return _check("periode", SUPPORTED)


def _check_meetwaarden(rec: dict, gevraagd: list[str]) -> list[dict]:
    per_key = {m["key"]: m for m in rec["meetwaarden"]}
    titels = {m["title"]: m["key"] for m in rec["meetwaarden"] if m["title"]}
    checks, eenheden = [], set()
    for mw in gevraagd:
        if mw in per_key:
            m = per_key[mw]
            eenheden.add(m["unit"])
            checks.append(_check("meetwaarde", SUPPORTED, key=mw, eenheid=m["unit"], titel=m["title"]))
        elif mw in titels:
            checks.append(_check("meetwaarde", UNSUPPORTED, "titel is geen sleutel; gebruik de officiële key",
                                 fout="titel_i_p_v_key", herstel=[titels[mw]]))
        else:
            checks.append(_check("meetwaarde", UNSUPPORTED, f"{mw!r} bestaat niet in deze tabel",
                                 fout="ongeldige_meetwaarde",
                                 herstel=_suggesties(mw, list(per_key) + list(titels))))
    if len(eenheden) > 1:
        checks.append(_check("meetwaarden_eenheid", UNKNOWN,
                             f"verschillende eenheden {sorted(eenheden)}: niet optellen of vergelijken zonder definitie",
                             fout="definitieverschil"))
    return checks


def _check_dimensies(rec: dict, dims: dict[str, str], live: bool) -> list[dict]:
    from httpx import HTTPError
    checks = []
    bekend = rec["dimensies"]
    for dim, code in dims.items():
        if dim not in bekend:
            checks.append(_check("dimensie", UNSUPPORTED, f"{dim!r} is geen dimensie van deze tabel",
                                 fout="ongeldige_dimensie", herstel=_suggesties(dim, bekend)))
            continue
        if not live:
            checks.append(_check("dimensiecode", UNKNOWN, "codes alleen live te verifiëren", dimensie=dim))
            continue
        try:
            waarden = contract.dimensie_waarden(rec["dataset_id"], dim)
        except HTTPError as e:
            checks.append(_check("dimensiecode", UNKNOWN, f"bron tijdelijk niet bereikbaar ({type(e).__name__})",
                                 fout="bron_niet_bereikbaar", dimensie=dim))
            continue
        if code in waarden:
            checks.append(_check("dimensiecode", SUPPORTED, dimensie=dim, code=code, titel=waarden[code]))
        else:
            checks.append(_check("dimensiecode", UNSUPPORTED, f"{code!r} is geen geldige code van {dim}",
                                 fout="ongeldige_code", dimensie=dim,
                                 herstel=[f"{k} ({t})" for k, t in waarden.items() if t in _suggesties(code, list(waarden.values()))][:3]))
    return checks


def validate_selection(dataset_id: str, requirements: dict, live: bool = False) -> dict:
    """Controleer of een selectie op deze tabel uitvoerbaar is.

    ``requirements`` (alle optioneel): ``sector`` ('mbo'|'hbo'|'wo'), ``geografie``
    (niveau), ``periode`` ({'type': 'jaar'|'schooljaar'|'kwartaal'|'maand'|'prognosejaar'}),
    ``meetwaarden`` (lijst keys), ``dimensies`` ({dimensie: code}).

    Retourneert ``{"dataset_id", "status", "checks"}`` met status
    supported/unsupported/unknown. Onbekende dataset-ID: ``DatasetNietGevonden``.
    """
    rec = contract.get_dataset(dataset_id)
    checks: list[dict] = []
    if "sector" in requirements:
        checks.append(_check_sector(rec, requirements["sector"]))
    if "geografie" in requirements:
        checks.append(_check_geografie(rec, requirements["geografie"]))
    if "periode" in requirements:
        checks.append(_check_periode(rec, requirements["periode"]))
    if "meetwaarden" in requirements:
        checks.extend(_check_meetwaarden(rec, requirements["meetwaarden"]))
    if "dimensies" in requirements:
        checks.extend(_check_dimensies(rec, requirements["dimensies"], live))
    if rec["archief"]["gearchiveerd"]:
        checks.append(_check("archief", SUPPORTED, "tabel is gearchiveerd; historische data", waarschuwing=True))
    return {"dataset_id": rec["dataset_id"], "status": _samenvatten(checks), "checks": checks}


def resolve_dimensiewaarde(dataset_id: str, dimensie: str, tekst: str) -> dict:
    """Koppel vrije tekst aan een geverifieerde dimensiecode (live, gecachet).

    Alleen exacte code- of titelmatch (hoofdletterongevoelig). Meerdere treffers
    of geen treffer leidt tot een verduidelijkingsvraag, nooit tot de eerste match.
    """
    from httpx import HTTPError
    try:
        waarden = contract.dimensie_waarden(dataset_id, dimensie)
    except HTTPError as e:
        return {"status": UNKNOWN, "fout": "bron_niet_bereikbaar", "reden": type(e).__name__}
    t = tekst.strip().lower()
    treffers = [(k, v) for k, v in waarden.items() if k.lower() == t or v.lower() == t]
    if len(treffers) == 1:
        return {"status": SUPPORTED, "code": treffers[0][0], "titel": treffers[0][1]}
    if treffers:
        return {"status": UNKNOWN, "fout": "ambigu", "opties": [{"code": k, "titel": v} for k, v in treffers]}
    opties = [{"code": k, "titel": v} for k, v in waarden.items() if v in _suggesties(tekst, list(waarden.values()))]
    return {"status": UNKNOWN, "fout": "geen_exacte_match", "opties": opties}


def prepare_query(dataset_id: str, requirements: dict) -> dict:
    """Bouw ``$select``/``$filter`` alleen uit geverifieerde sleutels en codes (live).

    Retourneert ``{"status", "query", "checks"}``; ``query`` is ``None`` tenzij alles
    `supported` is. Een vrije NL-vraag wordt hier nooit automatisch een query.
    """
    result = validate_selection(dataset_id, requirements, live=True)
    result["query"] = None
    if result["status"] != SUPPORTED:
        return result
    rec = contract.get_dataset(dataset_id)
    query: dict[str, str] = {}
    keys = [c["key"] for c in result["checks"] if c["veld"] == "meetwaarde"]
    if keys:
        query["$select"] = ",".join(keys)
    delen = []
    for c in result["checks"]:
        if c["veld"] == "dimensiecode":
            sleutel = contract.dimensie_sleutel(rec["dataset_id"], c["dimensie"]) or c["dimensie"]
            delen.append(f"trim({sleutel}) eq '{c['code']}'")
    if delen:
        query["$filter"] = " and ".join(delen)
    result["query"] = query
    return result
