"""
Gedeelde helperfuncties voor catalogus.py en uitbreiden.py.
"""


def _infer_geo_niveau(bron: str, dims: list[str]) -> list[str]:
    if "RegioS" not in dims:
        return []
    bron_lower = bron.lower()
    if "regiokenmerken" in bron_lower:
        return ["landelijk", "corop", "provincie", "gemeente"]
    if "woonregio" in bron_lower:
        return ["landelijk", "provincie", "gemeente"]
    if "gemeente" in bron_lower:
        return ["gemeente"]
    if "provincie" in bron_lower:
        return ["provincie"]
    return ["landelijk", "provincie"]


_KALENDERJAAR_THEMAS = frozenset({
    "financiering en uitgaven onderwijs",
    "onderwijs en arbeidsmarkt",
    "onderwijsniveau bevolking",
})


def _infer_perioden_formaat(freq: str, theme_name: str) -> list[str]:
    freq_lower = freq.lower()
    if "stopgezet" in freq_lower:
        return []
    if "maand" in freq_lower:
        return ["MM"]
    if "kwartaal" in freq_lower:
        return ["KW"]
    if theme_name.lower() in _KALENDERJAAR_THEMAS:
        return ["JJ"]
    return ["SJ"]
