"""
Tests voor CBS-04: validate_selection / resolve_dimensiewaarde / prepare_query.
Offline; CBS-client wordt gestubd.
"""
import httpx
import pytest

import onderwijsdata as od
from onderwijsdata import client, contract, validatie


def _status(res, veld):
    return [c["status"] for c in res["checks"] if c["veld"] == veld]


@pytest.fixture
def nep_cbs(monkeypatch):
    waarden = {"Geslacht": {"T001038": "Totaal mannen en vrouwen", "3000": "Mannen", "4000": "Vrouwen"},
               "Onderwijssoort": {"A": "Hbo", "B": "Hbo bachelor", "C": "Wo"}}
    state = {"down": False}

    def definitions(cbs_id):
        return {k: {"title": k, "type": "Dimension"} for k in waarden}

    def dimension(cbs_id, key):
        if state["down"]:
            raise httpx.ConnectTimeout("timeout")
        return waarden[key]

    monkeypatch.setattr(client, "definitions", definitions)
    monkeypatch.setattr(client, "dimension", dimension)
    contract._dimensie_cache.cache_clear()
    yield state
    contract._dimensie_cache.cache_clear()


class TestSector:
    def test_zuivere_ho_tabel_voor_wo(self):
        assert od.validate_selection("85423NED", {"sector": "wo"})["status"] == "supported"

    def test_mbo_op_ho_tabel_is_unsupported(self):
        assert od.validate_selection("85423NED", {"sector": "mbo"})["status"] == "unsupported"

    def test_brede_tabel_is_unknown_niet_supported(self):
        res = od.validate_selection("85702NED", {"sector": "mbo"})
        assert res["status"] == "unknown"

    def test_ongeldige_sector_geeft_herstelopties(self):
        res = od.validate_selection("85423NED", {"sector": "vo"})
        assert res["checks"][0]["fout"] == "ongeldige_sector"
        assert res["checks"][0]["herstel"] == ["mbo", "hbo", "wo"]

    def test_ho_label_zonder_uitsplitsing_is_geen_bewijs(self, monkeypatch):
        rec = od.get_dataset("85423NED")
        rec["dimensies"] = ["Geslacht", "Perioden"]
        monkeypatch.setattr(contract, "get_dataset", lambda *_a, **_k: rec)
        assert _status(od.validate_selection("85423NED", {"sector": "wo"}), "sector") == ["unknown"]


class TestGeografieEnPeriode:
    def test_gemeente_niet_ondersteund_zonder_regiodimensie(self):
        res = od.validate_selection("85423NED", {"geografie": "gemeente"})
        assert res["status"] == "unsupported"

    def test_gemeente_wel_ondersteund_met_niveaus(self):
        rec = next(r for r in od.catalog_records() if "gemeente" in (r["geografie"].get("niveaus") or []))
        assert od.validate_selection(rec["dataset_id"], {"geografie": "gemeente"})["status"] == "supported"
        res = od.validate_selection(rec["dataset_id"], {"geografie": "wijk"})
        assert res["status"] == "unsupported" and res["checks"][0]["herstel"]

    def test_jaar_is_niet_schooljaar(self):
        res = od.validate_selection("85423NED", {"periode": {"type": "jaar"}})
        assert res["status"] == "unsupported" and "conversieregel" in res["checks"][0]["reden"]

    def test_schooljaar_op_schooljaartabel(self):
        assert od.validate_selection("85423NED", {"periode": {"type": "schooljaar"}})["status"] == "supported"

    def test_prognosejaar_nooit_gelijkgesteld(self):
        assert od.validate_selection("85423NED", {"periode": {"type": "prognosejaar"}})["status"] == "unsupported"

    def test_kwartaal_op_jaartabel(self):
        assert od.validate_selection("85423NED", {"periode": {"type": "kwartaal"}})["status"] == "unsupported"


class TestMeetwaarden:
    def test_titel_i_p_v_key_wordt_afgekeurd_met_herstel(self):
        res = od.validate_selection("85423NED", {"meetwaarden": ["Totaal ingeschrevenen"]})
        c = res["checks"][0]
        assert c["fout"] == "titel_i_p_v_key" and c["herstel"] == ["TotaalIngeschrevenen_1"]

    def test_onbekende_meetwaarde_geeft_suggestie(self):
        c = od.validate_selection("85423NED", {"meetwaarden": ["TotaalIngeschreven_1"]})["checks"][0]
        assert c["fout"] == "ongeldige_meetwaarde" and "TotaalIngeschrevenen_1" in c["herstel"]

    def test_eenheid_zichtbaar(self):
        c = od.validate_selection("85423NED", {"meetwaarden": ["TotaalIngeschrevenen_1"]})["checks"][0]
        assert c["eenheid"] == "aantal"

    def test_verschillende_eenheden_zijn_definitieverschil(self, monkeypatch):
        rec = od.get_dataset("85423NED")
        rec["meetwaarden"] = [
            {"key": "A_1", "title": "A", "unit": "aantal", "datatype": "Long", "decimals": 0},
            {"key": "B_1", "title": "B", "unit": "%", "datatype": "Double", "decimals": 1},
        ]
        monkeypatch.setattr(contract, "get_dataset", lambda *_a, **_k: rec)
        res = od.validate_selection("85423NED", {"meetwaarden": ["A_1", "B_1"]})
        assert res["status"] == "unknown"
        assert any(c.get("fout") == "definitieverschil" for c in res["checks"])


class TestDimensies:
    def test_ongeldige_dimensie_getypeerd_met_herstel(self):
        c = od.validate_selection("85423NED", {"dimensies": {"Geslachtt": "x"}})["checks"][0]
        assert c["fout"] == "ongeldige_dimensie" and "Geslacht" in c["herstel"]

    def test_code_offline_is_unknown(self):
        res = od.validate_selection("85423NED", {"dimensies": {"Geslacht": "T001038"}})
        assert res["status"] == "unknown"

    def test_geldige_code_live(self, nep_cbs):
        res = od.validate_selection("85423NED", {"dimensies": {"Geslacht": "T001038"}}, live=True)
        assert res["status"] == "supported"

    def test_ongeldige_code_geeft_herstelopties(self, nep_cbs):
        c = od.validate_selection("85423NED", {"dimensies": {"Geslacht": "3001"}}, live=True)["checks"][0]
        assert c["fout"] == "ongeldige_code" and c["status"] == "unsupported"

    def test_bronuitval_is_unknown_niet_bestaat_niet(self, nep_cbs):
        nep_cbs["down"] = True
        c = od.validate_selection("85423NED", {"dimensies": {"Geslacht": "T001038"}}, live=True)["checks"][0]
        assert c["status"] == "unknown" and c["fout"] == "bron_niet_bereikbaar"


class TestResolve:
    def test_exacte_titel(self, nep_cbs):
        assert od.resolve_dimensiewaarde("85423NED", "Geslacht", "mannen")["code"] == "3000"

    def test_ambiguiteit_vraagt_verduidelijking_geen_eerste_match(self, nep_cbs, monkeypatch):
        monkeypatch.setattr(client, "dimension", lambda *_a: {"1": "Totaal", "2": "Totaal"})
        res = od.resolve_dimensiewaarde("85423NED", "Geslacht", "totaal")
        assert res["fout"] == "ambigu" and len(res["opties"]) == 2

    def test_geen_exacte_match_geeft_opties(self, nep_cbs):
        res = od.resolve_dimensiewaarde("85423NED", "Onderwijssoort", "hb")
        assert res["status"] == "unknown" and res["fout"] == "geen_exacte_match"

    def test_bronuitval(self, nep_cbs):
        nep_cbs["down"] = True
        assert od.resolve_dimensiewaarde("85423NED", "Geslacht", "mannen")["fout"] == "bron_niet_bereikbaar"


class TestPrepareQuery:
    def test_geverifieerde_query(self, nep_cbs):
        res = od.prepare_query("85423NED", {
            "sector": "hbo", "periode": {"type": "schooljaar"},
            "meetwaarden": ["TotaalIngeschrevenen_1"], "dimensies": {"Geslacht": "3000"},
        })
        assert res["query"] == {"$select": "TotaalIngeschrevenen_1", "$filter": "trim(Geslacht) eq '3000'"}

    def test_geen_query_bij_niet_supported(self, nep_cbs):
        res = od.prepare_query("85423NED", {"meetwaarden": ["Totaal ingeschrevenen"]})
        assert res["query"] is None and res["status"] == "unsupported"

    def test_geen_query_bij_unknown(self, nep_cbs):
        assert od.prepare_query("85702NED", {"sector": "mbo"})["query"] is None


def test_capability_aan():
    assert od.catalog_manifest()["capabilities"]["validate_selection"] is True


def test_onbekende_dataset_geeft_fout():
    with pytest.raises(contract.DatasetNietGevonden):
        od.validate_selection("99999NED", {})
