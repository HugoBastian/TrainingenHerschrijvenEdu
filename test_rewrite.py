"""
test_rewrite.py
===============
Offline tests voor de deterministische code-check (rewrite_checks.py).
Geen API-key nodig. Draai met `python test_rewrite.py` of `pytest test_rewrite.py`.
"""

from __future__ import annotations

import copy

from rewrite_checks import HARD, FLAG, check_rewrite, hard_fails, flags


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _good_rewrite() -> dict:
    """Een concept dat ALLE harde checks haalt (0 hard-fails)."""
    return {
        "korte_omschrijving": "Wil je " + " ".join(["data"] * 57) + "?",   # 2 + 57 = 59 woorden
        "algemene_omschrijving": " ".join(["onderwerp"] * 195),             # 195 woorden
        "programma": {"modules": [
            {"titel": "Module een", "bullets": ["Onderdeel a", "Onderdeel b", "Onderdeel c"]},
            {"titel": "Module twee", "bullets": ["Onderdeel a", "Onderdeel b", "Onderdeel c", "Onderdeel d"]},
            {"titel": "Module drie", "bullets": ["Onderdeel a", "Onderdeel b", "Onderdeel c"]},
            {"titel": "Module vier", "bullets": ["Onderdeel a", "Onderdeel b", "Onderdeel c", "Onderdeel d", "Onderdeel e"]},
        ]},
        "opzet_invulling": "je datagedreven keuzes maakt",
        "doelgroep": "Deze training is voor iedereen die met data betere keuzes wil maken.",
        "voorkennis": "Specifieke voorkennis voor het volgen van deze training is niet noodzakelijk.",
        "doelen": ["Bouwen van heldere dashboards", "Opschonen van ruwe data",
                   "Analyseren van terugkerende trends", "Presenteren van resultaten aan het team"],
        "vervolgtraining_titels": ["Power BI"],
        "kortste_omschrijving": "Wil je slimmer met data werken en betere keuzes maken?",
    }


_CTX = {"catalog_titles": {"Power BI", "T-SQL"}, "naam": "Data-analyse"}


def _codes(issues, severity=None):
    return {i.code for i in issues if severity is None or i.severity == severity}


# ---------------------------------------------------------------------------
# Baseline: het goede concept haalt alle harde checks
# ---------------------------------------------------------------------------

def test_good_rewrite_has_no_hard_fails():
    issues = check_rewrite(_good_rewrite(), _CTX)
    hf = hard_fails(issues)
    assert hf == [], f"onverwachte hard-fails: {[str(i) for i in hf]}"


# ---------------------------------------------------------------------------
# Harde checks per kopje
# ---------------------------------------------------------------------------

def test_korte_te_kort():
    rw = _good_rewrite()
    rw["korte_omschrijving"] = "Wil je " + " ".join(["data"] * 10) + "?"
    assert "lengte_woorden" in _codes(check_rewrite(rw, _CTX), HARD)


def test_korte_verkeerde_opening():
    rw = _good_rewrite()
    rw["korte_omschrijving"] = "Deze training " + " ".join(["data"] * 57) + "."
    assert "opening" in _codes(check_rewrite(rw, _CTX), HARD)


def test_algemene_te_lang():
    rw = _good_rewrite()
    rw["algemene_omschrijving"] = " ".join(["onderwerp"] * 260)
    assert "lengte_woorden" in _codes(check_rewrite(rw, _CTX), HARD)


def test_programma_te_weinig_modules():
    rw = _good_rewrite()
    rw["programma"]["modules"] = rw["programma"]["modules"][:3]
    assert "modules_aantal" in _codes(check_rewrite(rw, _CTX), HARD)


def test_programma_bullets_buiten_bereik():
    rw = _good_rewrite()
    rw["programma"]["modules"][0]["bullets"] = ["een", "twee"]  # 2 < 3
    assert "bullets_aantal" in _codes(check_rewrite(rw, _CTX), HARD)


def test_programma_bullets_geen_variatie():
    rw = _good_rewrite()
    for m in rw["programma"]["modules"]:
        m["bullets"] = ["a", "b", "c"]  # overal 3
    assert "bullets_variatie" in _codes(check_rewrite(rw, _CTX), HARD)


def test_doelgroep_professionals():
    rw = _good_rewrite()
    rw["doelgroep"] = "Deze training is voor professionals die met data werken."
    assert "professionals" in _codes(check_rewrite(rw, _CTX), HARD)


def test_doelgroep_verkeerde_opening():
    rw = _good_rewrite()
    rw["doelgroep"] = "Voor iedereen die met data werkt."
    assert "opening" in _codes(check_rewrite(rw, _CTX), HARD)


def test_doelen_verkeerd_aantal():
    rw = _good_rewrite()
    rw["doelen"] = rw["doelen"][:2]
    assert "aantal" in _codes(check_rewrite(rw, _CTX), HARD)


def test_doelen_kleine_letter():
    rw = _good_rewrite()
    rw["doelen"][0] = "bouwen van dashboards"
    assert "hoofdletter" in _codes(check_rewrite(rw, _CTX), HARD)


def test_kortste_te_lang():
    rw = _good_rewrite()
    rw["kortste_omschrijving"] = "Wil je " + "x" * 250 + "?"
    assert "lengte_tekens" in _codes(check_rewrite(rw, _CTX), HARD)


def test_kortste_verkeerde_opening():
    rw = _good_rewrite()
    rw["kortste_omschrijving"] = "Leer slimmer werken met data en tools."
    assert "opening" in _codes(check_rewrite(rw, _CTX), HARD)


# ---------------------------------------------------------------------------
# Generieke harde checks: placeholders, HTML, onbekende catalogus-titel
# ---------------------------------------------------------------------------

def test_placeholder_blijft_staan():
    rw = _good_rewrite()
    rw["opzet_invulling"] = "je werkt met [....] in de praktijk"
    assert "placeholder" in _codes(check_rewrite(rw, _CTX), HARD)


def test_html_in_tekst():
    rw = _good_rewrite()
    rw["programma"]["modules"][0]["bullets"][0] = "<p>Onderdeel</p>"
    assert "html" in _codes(check_rewrite(rw, _CTX), HARD)


def test_onbekende_vervolgtraining_titel():
    rw = _good_rewrite()
    rw["vervolgtraining_titels"] = ["Niet Bestaande Cursus"]
    assert "titel_onbekend" in _codes(check_rewrite(rw, _CTX), HARD)


def test_ontbrekend_kopje():
    rw = _good_rewrite()
    rw["doelgroep"] = ""
    assert "ontbreekt" in _codes(check_rewrite(rw, _CTX), HARD)


# ---------------------------------------------------------------------------
# Flags (mogen GEEN hard-fail zijn)
# ---------------------------------------------------------------------------

def test_u_vorm_is_flag_geen_hardfail():
    rw = _good_rewrite()
    rw["doelgroep"] = "Deze training is voor iedereen die uw data wil benutten."
    issues = check_rewrite(rw, _CTX)
    assert "u_vorm" in _codes(issues, FLAG)
    assert "u_vorm" not in _codes(issues, HARD)


def test_llm_frase_is_flag():
    rw = _good_rewrite()
    rw["algemene_omschrijving"] = "In deze training duiken we in " + " ".join(["onderwerp"] * 186)
    assert "llm_taal" in _codes(check_rewrite(rw, _CTX), FLAG)


def test_marketing_is_flag():
    rw = _good_rewrite()
    rw["korte_omschrijving"] = "Wil je deze uniek " + " ".join(["data"] * 55) + "?"
    assert "marketing" in _codes(check_rewrite(rw, _CTX), FLAG)


def test_catalogus_niet_geladen_is_flag():
    rw = _good_rewrite()
    issues = check_rewrite(rw, {"naam": "x"})  # geen catalog_titles
    assert "catalogus_ontbreekt" in _codes(issues, FLAG)


# ---------------------------------------------------------------------------
# Runner (zonder pytest)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
