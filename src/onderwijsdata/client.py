import httpx

BASE_API  = "https://opendata.cbs.nl/ODataApi/OData"
BASE_FEED = "https://opendata.cbs.nl/ODataFeed/OData"


def get(dataset_id: str, endpoint: str, **params):
    params.setdefault("$format", "json")
    url = f"{BASE_API}/{dataset_id}/{endpoint}"
    r = httpx.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()["value"]


def _strip(rows):
    return [
        {k: v.strip() if isinstance(v, str) else v for k, v in row.items()}
        for row in rows
    ]


def data(dataset_id: str, **params):
    """Fetch TypedDataSet via ODataFeed (supports $filter + pagination)."""
    rows = []
    skip = params.pop("$skip", 0)
    page = params.pop("$top", None)
    single_page = page is not None

    while True:
        r = httpx.get(
            f"{BASE_FEED}/{dataset_id}/TypedDataSet",
            params={"$format": "json", "$top": page or 10000, "$skip": skip, **params},
            timeout=60,
        )
        r.raise_for_status()
        batch = r.json()["value"]
        rows.extend(batch)
        if single_page or len(batch) < (page or 10000):
            break
        skip += (page or 10000)
    return _strip(rows)


def properties(dataset_id: str):
    return get(dataset_id, "DataProperties")


def dimension(dataset_id: str, dim: str):
    """Returns a Key→Title dict for a dimension."""
    rows = get(dataset_id, dim)
    return {r["Key"].strip(): r["Title"].strip() for r in rows}
