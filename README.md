# onderwijsdata

Python package voor het ophalen en analyseren van publieke Nederlandse onderwijsdata via de [CBS OData API](https://opendata.cbs.nl/ODataCatalog/).

Gebouwd als proof of concept voor de [CEDA](https://github.com/cedanl) data coalitie — zie `IDEE.md` voor de productvisie.

## Installatie

```bash
uv sync
```

## Gebruik

```python
from onderwijsdata import client

# Dimensies ophalen
geslacht = client.dimension("85423NED", "Geslacht")

# Data ophalen met filter
rows = client.data("85380NED", **{
    "$filter": "Onderwijssoort eq 'A041920' and trim(Regiokenmerken) eq 'GM0363'"
})
```

### Catalogus opbouwen

```bash
uv run python catalogus/catalogus.py        # Haalt 68 CBS onderwijsdatasets op
uv run python catalogus/catalogus_ai.py submit   # Verrijkt met AI (vereist ANTHROPIC_API_KEY)
uv run python catalogus/catalogus_ai.py collect
```

Output: `data/02-prepared/cbs_datasets_ai.json` — 68 datasets met samenvatting, tags en voorbeeldvragen.

### Voorbeeldanalyses

```bash
uv run python voorbeelden/ho_ingeschrevenen.py   # HO ingeschrevenen 85423NED
uv run python voorbeelden/mbo_deelnemers.py      # MBO deelnemers 85353NED
```

Plots worden opgeslagen in `voorbeelden/output/`.

## Structuur

```
src/onderwijsdata/    Python package (CBS OData client)
catalogus/            Catalogus builder + AI-verrijking
data/02-prepared/     Verrijkte CBS catalogus (JSON)
voorbeelden/          Voorbeeldanalyses + gegenereerde plots
```

## CBS OData API

- Datasets: `https://opendata.cbs.nl/ODataApi/OData/{id}`
- Gefilterde data: `https://opendata.cbs.nl/ODataFeed/OData/{id}/TypedDataSet`
- Catalogus: `https://opendata.cbs.nl/ODataCatalog/`

Bekende eigenaardigheden: dimensiecodes hebben trailing spaces (gebruik `trim()`), geneste OR-filters worden niet ondersteund.
