__version__ = "0.1.1"

from .client import data, dimension, properties, definitions

def catalog(ai=True):
    import json
    from importlib.resources import files
    data_dir = files("onderwijsdata.data")
    if ai:
        try:
            return json.loads(data_dir.joinpath("cbs_datasets_enriched.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
    filename = "cbs_datasets_ai.json" if ai else "cbs_datasets.json"
    return json.loads(data_dir.joinpath(filename).read_text(encoding="utf-8"))

__all__ = ["data", "dimension", "properties", "definitions", "catalog", "__version__"]
