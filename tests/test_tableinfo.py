"""
Tests voor CBS-08: methodiek, afronding, populatie en periodecontext uit TableInfos.
"""
import json
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "catalogus"))

import pytest
import verrijk_tableinfo as vt
import onderwijsdata as od

ROOT = Path(__file__).parent.parent
FIX = Path(__file__).parent / "fixtures" / "contract_v1"

DESCRIPTION = """INHOUDSOPGAVE

1. TOELICHTING

Deze tabel bevat het aantal ingeschreven studenten. Vanaf studiejaar 2015/'16 ook het aantal studenten aan enkele aangewezen instellingen.

Status van de cijfers:
De cijfers tot en met 2024/'25 zijn definitief en 2025/'26 is voorlopig.

Wanneer komen de nieuwe cijfers?
In het eerste kwartaal van 2027 komen de voorlopige cijfers beschikbaar.

2. DEFINITIES EN VERKLARING VAN SYMBOLEN

Definities:

Ingeschrevenen
Ingeschrevenen in het ho. Wie hbo en wo volgt, wordt bij beide meegeteld. Het totaal is daardoor lager dan de som van hbo en wo.

Studiejaar
Een studiejaar loopt van 1 september tot en met 31 augustus.

Verklaring van symbolen:
niets (blanco): het cijfer kan op logische gronden niet voorkomen
* : voorlopige cijfers
De aantallen in de tabel zijn afgerond op 10-tallen. Hierdoor kan de som van de details afwijken van het totaal.

3. KOPPELINGEN NAAR RELEVANTE TABELLEN EN ARTIKELEN

<a href='https://opendata.cbs.nl/#/CBS/nl/dataset/85422NED'>Eerstejaarsstudenten</a>

4. BRONNEN EN METHODEN

<a href='https://www.cbs.nl/methode'>Hoger onderwijs</a>
"""

INFO = {"Description": DESCRIPTION, "ShortDescription": "\nDeze tabel bevat het aantal studenten.",
        "Modified": "2026-04-10T02:00:00", "MetaDataModified": "2026-04-11T02:00:00"}
PERIODEN = [
    {"Key": "2023SJ00", "Title": "2023/'24", "Status": "Definitief"},
    {"Key": "2025SJ00", "Title": "2025/'26", "Status": "Voorlopig", "Description": "Voorlopige cijfers"},
]


@pytest.fixture(scope="module")
def parsed():
    return vt.parse_tableinfo("TEST01NED", INFO, PERIODEN, "2026-10-05")


class TestParser:
    def test_afronding_met_bronpassage_en_grondslag(self, parsed):
        a = parsed["afronding"]
        assert a["status"] == "gevonden" and a["grondslag"] == 10 and a["exact"] is False
        assert a["passages"] == ["De aantallen in de tabel zijn afgerond op 10-tallen."]

    def test_symbolenlijst_vervuilt_afrondingspassage_niet(self, parsed):
        assert all("blanco" not in p for p in parsed["afronding"]["passages"])

    def test_additiviteit_niet_additief_met_passages(self, parsed):
        a = parsed["additiviteit"]
        assert a["status"] == "gevonden" and a["additief"] is False
        assert any("som van hbo en wo" in p for p in a["passages"])

    def test_definities_per_term(self, parsed):
        termen = {d["term"] for d in parsed["definities"]}
        assert termen == {"Ingeschrevenen", "Studiejaar"}

    def test_definitiebreuk_met_passage(self, parsed):
        assert parsed["definitiebreuken"]["status"] == "gevonden"
        assert "2015/'16" in parsed["definitiebreuken"]["passages"][0]

    def test_publicatie(self, parsed):
        assert "voorlopig" in parsed["publicatie"]["status_tekst"]
        assert "2027" in parsed["publicatie"]["volgende_publicatie"]

    def test_relaties_zijn_bronlinks_geen_uitwisselbaarheid(self, parsed):
        assert parsed["relaties"] == [{
            "dataset_id": "cbs:85422NED", "titel": "Eerstejaarsstudenten",
            "url": "https://opendata.cbs.nl/#/CBS/nl/dataset/85422NED", "relatie": "bronlink"}]

    def test_methoden_links(self, parsed):
        assert parsed["methoden"] == [{"titel": "Hoger onderwijs", "url": "https://www.cbs.nl/methode"}]

    def test_bronmeta_apart(self, parsed):
        assert parsed["bronmeta"] == {"modified": "2026-04-10T02:00:00",
                                      "metadata_modified": "2026-04-11T02:00:00",
                                      "gecontroleerd_op": "2026-10-05"}

    def test_verificatie_is_bron(self, parsed):
        assert parsed["verificatie"] == "bron"

    def test_ontbrekende_info_is_niet_vermeld_niet_afwezig(self):
        r = vt.parse_tableinfo("X", {"Description": "", "ShortDescription": ""}, [], "2026-10-05")
        for veld in ("afronding", "additiviteit", "definitiebreuken", "populatie"):
            assert r[veld]["status"] == "niet_vermeld"
        assert r["afronding"].get("exact") is None and r["additiviteit"].get("additief") is None

    @pytest.mark.parametrize("zin, verwacht", [
        ("De cijfers zijn afgerond op 5 duizendtallen.", 5000),
        ("De aantallen zijn afgerond op veelvouden van 10.", 10),
        ("Aantallen zijn afgerond op honderdtallen.", 100),
    ])
    def test_afrondingsgrondslagen(self, zin, verwacht):
        r = vt.parse_tableinfo("X", {"Description": f"1. TOELICHTING\n\n{zin}\n\n2. DEFINITIES\n"}, [], "d")
        assert r["afronding"]["grondslag"] == verwacht

    def test_afgeronde_opleiding_is_geen_afronding(self):
        zin = "Als een opleiding (nog) niet is afgerond, gaat het om het vereiste niveau."
        r = vt.parse_tableinfo("X", {"Description": f"1. TOELICHTING\n\n{zin}\n"}, [], "d")
        assert r["afronding"]["status"] == "niet_vermeld"


class TestPerioden:
    def test_status_komt_uit_bron(self, parsed):
        codes = {c["code"]: c for c in parsed["perioden"]["codes"]}
        assert codes["2025SJ00"]["status"] == "Voorlopig"
        assert codes["2023SJ00"]["status"] == "Definitief"

    def test_hiaten_gedetecteerd(self, parsed):
        assert parsed["perioden"]["hiaten"] == [2024]

    def test_status_wordt_niet_afgeleid_uit_label(self):
        p = vt.parse_perioden([{"Key": "2025SJ00", "Title": "2025/'26 voorlopig", "Status": None}])
        assert p["codes"][0]["status"] is None

    def test_hiaten_onbepaald_bij_gemengde_typen(self):
        p = vt.parse_perioden([{"Key": "2020JJ00", "Title": "2020"}, {"Key": "2021KW01", "Title": "k"}])
        assert p["hiaten"] is None


class TestGeleverdeData:
    def test_alle_datasets_aanwezig_en_wheel_gelijk(self):
        pad = "data/02-prepared/cbs_tableinfo.json"
        bouw = json.loads((ROOT / pad).read_text(encoding="utf-8"))
        wheel = json.loads((ROOT / "src/onderwijsdata" / "data/cbs_tableinfo.json").read_text(encoding="utf-8"))
        assert bouw == wheel
        assert {r["_cbs_id"] for r in bouw} == {r["_cbs_id"] for r in od.catalog()}

    @pytest.mark.parametrize("cbs_id", ["85423NED", "85422NED"])
    def test_voorbeeldtabellen_dragen_afrondingsregel_met_bronpassage(self, cbs_id):
        m = od.get_dataset(cbs_id)["methodiek"]
        assert m["afronding"]["grondslag"] == 10 and m["afronding"]["exact"] is False
        assert "afgerond op 10-tallen" in m["afronding"]["passages"][0]
        assert m["additiviteit"]["additief"] is False

    def test_ho_totaal_is_niet_additief_met_passage(self):
        passages = od.get_dataset("85423NED")["methodiek"]["additiviteit"]["passages"]
        assert any("som van het aantal ingeschrevenen in het hbo en wo" in p for p in passages)

    def test_eerstejaarsdefinitie_raadpleegbaar_voor_datasetkeuze(self):
        d = od.get_dataset("85422NED")["teldefinitie"]
        tekst = d["definities"][0]["tekst"]
        assert d["definities"][0]["term"] == "Eerstejaarsstudenten"
        assert "overstap" in tekst and "twee maal eerstejaarsstudent" in tekst

    def test_periodedetails_geven_voorlopig_uit_bronstatus(self):
        codes = od.get_dataset("85423NED")["perioden"]["codes"]
        laatste = codes["codes"][-1]
        assert (laatste["code"], laatste["status"]) == ("2025SJ00", "Voorlopig")
        assert codes["hiaten"] == []

    def test_bronmeta_apart_van_catalogusbouw(self):
        h = od.get_dataset("85423NED")["herkomst"]
        assert h["laatste_update"] and h["metadata_modified"] and h["gecontroleerd_op"]

    def test_geen_ai_in_bronvelden(self):
        for r in od.catalog_records():
            m = r["methodiek"]
            if m["status"] == "supported":
                assert m["verificatie"] == "bron"

    def test_ontbrekende_tableinfo_is_unknown(self):
        from onderwijsdata import contract
        rec = contract._record({"_cbs_id": "X", "bron": "x", "documentatie": {"url": "u"}})
        assert rec["methodiek"]["status"] == "unknown" and rec["populatie"]["status"] == "unknown"
