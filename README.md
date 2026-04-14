# onderwijsdata

Python client voor publieke Nederlandse onderwijsdata via de [CBS OData API](https://opendata.cbs.nl/ODataCatalog/).

## Installatie

```bash
pip install onderwijsdata                  # alleen client
pip install onderwijsdata[analyse]         # + pandas en matplotlib
pip install onderwijsdata[catalogus]       # + anthropic (voor catalogus_ai.py)
```

## Gebruik

```python
from onderwijsdata import data, dimension, properties

# Dimensiewaarden ophalen
geslacht = dimension("85423NED", "Geslacht")

# Data ophalen met filter
rows = data("85380NED", **{
    "$filter": "Onderwijssoort eq 'A041920' and trim(Regiokenmerken) eq 'GM0363'"
})

# Kolommen en meetwaarden bekijken
props = properties("85353NED")
```

## Structuur

```
src/onderwijsdata/    Python package (CBS OData client)
catalogus/            Catalogus builder + AI-verrijking
data/02-prepared/     Verrijkte CBS catalogus (JSON)
voorbeelden/          Voorbeeldanalyses + gegenereerde plots
docs/                 GitHub Pages catalogussite
```

## Catalogus opbouwen

```bash
uv run python catalogus/catalogus.py
uv run python catalogus/catalogus_ai.py submit
uv run python catalogus/catalogus_ai.py collect
```

## Links

- [Catalogussite](https://cedanl.github.io/cbs-onderwijsdata)
- [CBS OData API](https://opendata.cbs.nl/ODataCatalog/)
