"""
Haalt alle CBS onderwijsdatasets op en exporteert ze als data.json
compatibel met cedanl/overzicht-landelijke-databronnen.
"""
import json
import httpx

CATALOG = "https://opendata.cbs.nl/ODataCatalog"
API     = "https://opendata.cbs.nl/ODataApi/OData"

# Onderwijsthema IDs in de CBS catalogus
ONDERWIJS_THEMES = [
    352, 353, 354, 355, 356, 357, 358,
    359, 360, 361, 362, 363, 364, 365,
    366, 367, 368, 369, 370, 371, 372, 373,
]

# CBS sectie → categorie mapping (naar data.json conventies)
SECTIE_MAP = {
    "Primair onderwijs":              ("Primair Onderwijs",              "Studentgegevens & Onderwijs"),
    "Voortgezet onderwijs":           ("Voortgezet Onderwijs",           "Studentgegevens & Onderwijs"),
    "Middelbaar beroepsonderwijs":    ("Inschrijvingen en Studievoortgang", "Studentgegevens & Onderwijs"),
    "Hoger onderwijs":                ("Inschrijvingen en Studievoortgang", "Studentgegevens & Onderwijs"),
    "Financiering en uitgaven onderwijs": ("Financiële gegevens",        "Institutionele Data"),
    "Onderwijs en arbeidsmarkt":      ("Alumni en arbeidsmarkt",         "Studentgegevens & Onderwijs"),
    "Onderwijsniveau bevolking":      ("Algemene data overzichten",      "Algemene Data Overzichten"),
}

ONDERWIJSTYPE_MAP = {
    "Primair onderwijs":              ["PO"],
    "Voortgezet onderwijs":           ["VO"],
    "Middelbaar beroepsonderwijs":    ["MBO"],
    "Hoger onderwijs":                ["HBO", "WO"],
    "Financiering en uitgaven onderwijs": ["Allen"],
    "Onderwijs en arbeidsmarkt":      ["Allen"],
    "Onderwijsniveau bevolking":      ["Allen"],
}


def get(url, **params):
    params.setdefault("$format", "json")
    r = httpx.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("value", r.json())


def fetch_dataset_ids():
    """Haal alle unieke dataset-IDs op via onderwijsthema's."""
    ids_per_theme = {}
    for theme_id in ONDERWIJS_THEMES:
        rows = get(f"{CATALOG}/Tables_Themes", **{"$filter": f"ThemeID eq {theme_id}", "$select": "TableIdentifier,ThemeID"})
        for row in rows:
            ids_per_theme.setdefault(row["TableIdentifier"], theme_id)
    print(f"  {len(ids_per_theme)} unieke datasets gevonden")
    return ids_per_theme


def fetch_theme_names():
    """Haal thenaam op per theme ID."""
    rows = get(f"{CATALOG}/Themes")
    return {r["ID"]: r["Title"] for r in rows}


def fetch_table_info(dataset_id):
    """Haal metadata op voor één dataset."""
    rows = get(f"{API}/{dataset_id}/TableInfos")
    return rows[0] if rows else {}


def fetch_properties(dataset_id):
    """Haal dimensies en meetwaarden op."""
    rows = get(f"{API}/{dataset_id}/DataProperties")
    dims    = [r["Title"] for r in rows if r.get("Type") == "Dimension"]
    topics  = [r["Title"] for r in rows if r.get("Type") == "Topic"]
    return dims, topics


def to_data_json_entry(dataset_id, info, dims, topics, theme_name):
    """Converteer CBS metadata naar data.json formaat."""
    title   = info.get("Title", "").strip()
    period  = info.get("RecordCount", "")
    freq    = info.get("Frequency", "").strip() or "Jaarlijks"
    shortdesc = info.get("ShortDescription", "").strip()

    # Periode string ophalen
    period_str = info.get("Period", "").strip()

    # Doel: titel + dimensies als context
    doel_parts = [title]
    if dims:
        doel_parts.append(f"Uitgesplitst naar: {', '.join(dims[:4])}.")
    if topics:
        doel_parts.append(f"Meetwaarden: {', '.join(topics[:3])}.")
    doel = " ".join(doel_parts)

    categorie, sectie = SECTIE_MAP.get(theme_name, ("Algemene data overzichten", "Algemene Data Overzichten"))
    onderwijstype     = ONDERWIJSTYPE_MAP.get(theme_name, ["Allen"])

    return {
        "leverancier": "CBS",
        "bron": title,
        "periode": period_str,
        "onderwijstype": onderwijstype,
        "doel": doel,
        "frequentie": freq,
        "categorie": categorie,
        "sectie": sectie,
        "documentatie": {
            "tekst": f"CBS Open Data {dataset_id}",
            "url": f"https://opendata.cbs.nl/ODataApi/OData/{dataset_id}",
        },
        "repositories": [],
        "publieke_producten": [],
        "_cbs_id": dataset_id,
        "_thema": theme_name,
    }


def main():
    print("Stap 1: Dataset IDs ophalen...")
    ids_per_theme = fetch_dataset_ids()

    print("Stap 2: Themanamen ophalen...")
    theme_names = fetch_theme_names()

    entries = []
    total = len(ids_per_theme)

    print(f"Stap 3: Metadata ophalen voor {total} datasets...")
    for i, (dataset_id, theme_id) in enumerate(ids_per_theme.items(), 1):
        theme_name = theme_names.get(theme_id, "Onbekend")
        print(f"  [{i}/{total}] {dataset_id} ({theme_name})")
        try:
            info        = fetch_table_info(dataset_id)
            dims, topics = fetch_properties(dataset_id)
            entry       = to_data_json_entry(dataset_id, info, dims, topics, theme_name)
            entries.append(entry)
        except Exception as e:
            print(f"    FOUT: {e}")

    output = "data/02-prepared/cbs_datasets.json"
    with open(output, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    print(f"\nKlaar: {len(entries)} entries opgeslagen in {output}")
    print("Dit bestand is compatibel met cedanl/overzicht-landelijke-databronnen/docs/data.json")


if __name__ == "__main__":
    main()
