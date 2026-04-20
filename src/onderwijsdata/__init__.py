__version__ = "0.1.0"

from .client import data, dimension, properties

def catalog(ai=True):
    import json
    from importlib.resources import files
    filename = "cbs_datasets_ai.json" if ai else "cbs_datasets.json"
    return json.loads(files("onderwijsdata.data").joinpath(filename).read_text(encoding="utf-8"))

__all__ = ["data", "dimension", "properties", "catalog", "__version__"]
