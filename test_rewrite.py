"""
test_rewrite.py
===============
Offline tests voor de deterministische lagen: de code-check (rewrite_checks.py), de
besluiten-parser (besluiten.py) en de CMS-output (rewrite_output.py).
Geen API-key nodig. Draai met `python test_rewrite.py` of `pytest test_rewrite.py`.

De LLM-classificatie in besluiten.py wordt hier bewust NIET getest — die vraagt een
API-call. Wat hier wél gegarandeerd moet kloppen is de laag eronder: de structurele
splitsing van `actie_besluit` en de uitlijning met `actualiteit_actie`.
"""

from __future__ import annotations

import copy

import besluiten as bes
import rewrite_output as uit
import sjabloon
from rewrite_checks import HARD, FLAG, check_rewrite, hard_fails, flags


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _good_rewrite() -> dict:
    """Een concept dat ALLE harde checks haalt (0 hard-fails)."""
    return {
        "overzicht": "Wil je " + " ".join(["data"] * 57) + "?",   # 2 + 57 = 59 woorden
        "inleiding": " ".join(["onderwerp"] * 195),             # 195 woorden
        "modules": {"modules": [
            {"titel": "Module een", "bullets": ["Onderdeel a", "Onderdeel b", "Onderdeel c"]},
            {"titel": "Module twee", "bullets": ["Onderdeel a", "Onderdeel b", "Onderdeel c", "Onderdeel d"]},
            {"titel": "Module drie", "bullets": ["Onderdeel a", "Onderdeel b", "Onderdeel c"]},
            {"titel": "Module vier", "bullets": ["Onderdeel a", "Onderdeel b", "Onderdeel c", "Onderdeel d", "Onderdeel e"]},
        ]},
        "aanpak_invulling": "je datagedreven keuzes maakt",
        "doelgroep": "Deze training is voor iedereen die met data betere keuzes wil maken.",
        "voorkennis": "Specifieke voorkennis voor het volgen van deze training is niet noodzakelijk.",
        "doelen": ["Heldere dashboards te bouwen voor je team",
                   "Ruwe data op te schonen en samen te voegen",
                   "Terugkerende trends te analyseren",
                   "Resultaten te presenteren aan het team"],
        "vervolgstappen_titels": ["Training Power BI"],
        "kortste_omschrijving": "Wil je slimmer met data werken en betere keuzes maken?",
        "nieuwe_titel": "Training Data-analyse",
    }


_CTX = {"catalog_titles": {"Training Power BI", "Training T-SQL"}, "naam": "Training Data-analyse"}


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
    rw["overzicht"] = "Wil je " + " ".join(["data"] * 10) + "?"
    assert "lengte_woorden" in _codes(check_rewrite(rw, _CTX), HARD)


def test_korte_verkeerde_opening():
    rw = _good_rewrite()
    rw["overzicht"] = "Deze training " + " ".join(["data"] * 57) + "."
    assert "opening" in _codes(check_rewrite(rw, _CTX), HARD)


def test_algemene_te_lang():
    rw = _good_rewrite()
    rw["inleiding"] = " ".join(["onderwerp"] * 260)
    assert "lengte_woorden" in _codes(check_rewrite(rw, _CTX), HARD)


def test_programma_te_weinig_modules():
    rw = _good_rewrite()
    rw["modules"]["modules"] = rw["modules"]["modules"][:3]
    assert "modules_aantal" in _codes(check_rewrite(rw, _CTX), HARD)


def test_programma_bullets_buiten_bereik():
    rw = _good_rewrite()
    rw["modules"]["modules"][0]["bullets"] = ["een", "twee"]  # 2 < 3
    assert "bullets_aantal" in _codes(check_rewrite(rw, _CTX), HARD)


def test_programma_bullets_geen_variatie():
    rw = _good_rewrite()
    for m in rw["modules"]["modules"]:
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
    rw["doelen"][0] = "heldere dashboards te bouwen"   # wel te-infinitief, geen hoofdletter
    codes = _codes(check_rewrite(rw, _CTX), HARD)
    assert "hoofdletter" in codes
    assert "geen_te_infinitief" not in codes


def test_doelen_zonder_te_infinitief():
    """De kale infinitief ('Dashboards bouwen') loopt niet door op de vaste introzin."""
    rw = _good_rewrite()
    rw["doelen"][0] = "Dashboards bouwen die de juiste vraag beantwoorden"
    assert "geen_te_infinitief" in _codes(check_rewrite(rw, _CTX), HARD)


def test_doelen_gesplitste_te_infinitief_is_goed():
    """'voor te bereiden' / 'uit te oefenen' zijn geldige te-infinitieven."""
    rw = _good_rewrite()
    rw["doelen"][0] = "Jezelf voor te bereiden op een gesprek met je team"
    rw["doelen"][1] = "Invloed uit te oefenen zonder formele macht"
    assert "geen_te_infinitief" not in _codes(check_rewrite(rw, _CTX), HARD)


def test_doelen_onregelmatige_infinitief_is_goed():
    rw = _good_rewrite()
    rw["doelen"][0] = "Data te zien als basis voor besluitvorming"
    rw["doelen"][1] = "Om te gaan met tegenstrijdige belangen in het team"
    assert "geen_te_infinitief" not in _codes(check_rewrite(rw, _CTX), HARD)


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
    rw["aanpak_invulling"] = "je werkt met [....] in de praktijk"
    assert "placeholder" in _codes(check_rewrite(rw, _CTX), HARD)


def test_html_in_tekst():
    rw = _good_rewrite()
    rw["modules"]["modules"][0]["bullets"][0] = "<p>Onderdeel</p>"
    assert "html" in _codes(check_rewrite(rw, _CTX), HARD)


def test_onbekende_vervolgtraining_titel():
    rw = _good_rewrite()
    rw["vervolgstappen_titels"] = ["Training Bestaat Niet"]
    assert "titel_onbekend" in _codes(check_rewrite(rw, _CTX), HARD)


def test_ontbrekend_kopje():
    rw = _good_rewrite()
    rw["doelgroep"] = ""
    assert "ontbreekt" in _codes(check_rewrite(rw, _CTX), HARD)


# ---------------------------------------------------------------------------
# Soortwoord: niks heet nog een cursus of opleiding
# ---------------------------------------------------------------------------

def test_cursus_in_lopende_tekst_is_hardfail():
    rw = _good_rewrite()
    rw["inleiding"] = "Tijdens deze cursus " + " ".join(["onderwerp"] * 193)
    assert "soortwoord" in _codes(check_rewrite(rw, _CTX), HARD)


def test_opleiding_in_module_bullet_is_hardfail():
    rw = _good_rewrite()
    rw["modules"]["modules"][0]["bullets"][0] = "Opzet van de opleiding"
    assert "soortwoord" in _codes(check_rewrite(rw, _CTX), HARD)


def test_cursus_in_titel_is_hardfail():
    rw = _good_rewrite()
    rw["nieuwe_titel"] = "Cursus XML"
    assert "soortwoord" in _codes(check_rewrite(rw, _CTX), HARD)


def test_examentraining_mag_wel():
    rw = _good_rewrite()
    rw["nieuwe_titel"] = "Examentraining DAMA-DMBOK CDMP"
    assert "soortwoord" not in _codes(check_rewrite(rw, _CTX), HARD)


# ---------------------------------------------------------------------------
# Titelnormalisatie (sjabloon.nieuwe_titel / vervang_soortwoord / vervolgtitel)
# ---------------------------------------------------------------------------

def test_nieuwe_titel_vervangt_verboden_soortwoord():
    assert sjabloon.nieuwe_titel("Opleiding PHP Professional") == "Training PHP Professional"
    assert sjabloon.nieuwe_titel("Cursus XML") == "Training XML"
    assert sjabloon.nieuwe_titel("Gebruikerscursus Sitecore") == "Training Sitecore"


def test_nieuwe_titel_laat_toegestane_soortwoorden_staan():
    for titel in ("Training Linux", "Masterclass PHP", "Workshop Storytelling",
                  "Examentraining CEH"):
        assert sjabloon.nieuwe_titel(titel) == titel


def test_nieuwe_titel_zet_voorvoegsel_bij_titel_zonder_soortwoord():
    assert sjabloon.nieuwe_titel("Excel") == "Training Excel"


def test_vervang_soortwoord_laat_niet_titels_met_rust():
    """In gekopieerde tekst staan regels die geen titel zijn; die niet aanraken."""
    zin = "Trainingen voor specifieke databasesystemen zoals PostgreSQL"
    assert sjabloon.vervang_soortwoord(zin) == zin
    assert sjabloon.vervang_soortwoord("Cursus PowerPoint") == "Training PowerPoint"


def test_vervolgtitel_haalt_het_voorvoegsel_weg():
    """In de Vervolgstappen-lijst is "Training" bij elke regel ruis."""
    assert sjabloon.vervolgtitel("Training Power BI") == "Power BI"
    assert sjabloon.vervolgtitel("Cursus PowerPoint") == "PowerPoint"
    assert sjabloon.vervolgtitel("Opleiding PHP Professional") == "PHP Professional"
    assert sjabloon.vervolgtitel("Power BI") == "Power BI"


def test_vervolgtitel_houdt_een_afwijkende_vorm():
    """Masterclass/workshop/examentraining zeggen iets over de vorm: die blijft staan."""
    for titel in ("Masterclass PHP", "Workshop Storytelling", "Examentraining CEH"):
        assert sjabloon.vervolgtitel(titel) == titel


def test_vervolgtitel_laat_regels_die_geen_titel_zijn_met_rust():
    zin = "Trainingen voor specifieke databasesystemen zoals PostgreSQL"
    assert sjabloon.vervolgtitel(zin) == zin
    # lopende zin met een verboden soortwoord: hooguit dát woord vervangen
    assert sjabloon.vervolgtitel("Cursus voor gevorderden") == "Training voor gevorderden"
    # een hoofdletter erna maakt het wél een titel
    assert sjabloon.vervolgtitel("Training Van Excel naar Power BI") == "Van Excel naar Power BI"


def test_modules_opening_verdubbelt_soortwoord_niet():
    opening = sjabloon.modules_opening("Opleiding PHP Professional")
    assert "de Training PHP Professional" in opening
    assert "Opleiding" not in opening


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
    rw["inleiding"] = "In deze training duiken we in " + " ".join(["onderwerp"] * 186)
    assert "llm_taal" in _codes(check_rewrite(rw, _CTX), FLAG)


def test_marketing_is_flag():
    rw = _good_rewrite()
    rw["overzicht"] = "Wil je deze uniek " + " ".join(["data"] * 55) + "?"
    assert "marketing" in _codes(check_rewrite(rw, _CTX), FLAG)


def test_catalogus_niet_geladen_is_flag():
    rw = _good_rewrite()
    issues = check_rewrite(rw, {"naam": "x"})  # geen catalog_titles
    assert "catalogus_ontbreekt" in _codes(issues, FLAG)


# ---------------------------------------------------------------------------
# Besluiten: structurele splitsing van actie_besluit
#
# De strings hieronder komen letterlijk uit `Nieuwe lijst herschreven en dagen.xlsx`.
# Ze dekken de gevallen waarop de oude regex stukliep: een komma binnen de vrije tekst,
# en een cijfer binnen de vrije tekst ("PHP 8").
# ---------------------------------------------------------------------------

ECHTE_BESLUITEN = [
    ("1,2,3,4", 4),
    ("1,2", 2),
    ("1 niet,2 wel,3 wel,4 wel,5 wel", 5),
    ("1 prima,2 nuanceer,3 niet,4 prima", 4),
    ("1 nee dat is advanced,2 nee,3 in inleiding is dat prima", 3),
    ("1 PHP versie niet benoemen, wel relavante taalfeatures toevoegen,2 prima,3 prima,"
     "4 geen speciefieke frameworks benoemen,5 prima", 5),
    ("1 beide voor zover browser variant nog relevant,2 geen versienummers gebruiken,"
     "3 prima,4 prima", 4),
    ("1 stuk over certificatie vervalt helemaal,2 wel,3 examenstructuur vervalt volledig", 3),
]


def test_besluit_splitst_op_nummer_niet_op_elke_komma():
    for ruw, verwacht in ECHTE_BESLUITEN:
        items = bes.split_besluit(ruw)
        assert len(items) == verwacht, f"{ruw!r} -> {len(items)} i.p.v. {verwacht}"
        assert [nr for nr, _ in items] == list(range(1, verwacht + 1)), ruw


def test_komma_binnen_vrije_tekst_splitst_niet():
    items = bes.split_besluit(
        "1 PHP versie niet benoemen, wel relavante taalfeatures toevoegen,2 prima")
    assert items[0] == (1, "PHP versie niet benoemen, wel relavante taalfeatures toevoegen")
    assert items[1] == (2, "prima")


def test_cijfer_in_vrije_tekst_wordt_geen_actienummer():
    # de oude regex vond hier "8" van "PHP 8" als actienummer
    items = bes.split_besluit("1 gebruik PHP 8 niet als voorbeeld,2 prima")
    assert [nr for nr, _ in items] == [1, 2]


def test_parse_acties_nummert_de_scorerlijst():
    acties = bes.parse_acties("1. refresh: eerste\n2. refresh: tweede\n3. refresh: derde")
    assert acties == {1: "refresh: eerste", 2: "refresh: tweede", 3: "refresh: derde"}


def test_align_koppelt_annotatie_aan_actie():
    acties = {1: "eerste", 2: "tweede"}
    gekoppeld = bes.align(acties, bes.split_besluit("1 niet,2 prima"))
    assert gekoppeld == [(1, "eerste", "niet"), (2, "tweede", "prima")]


def test_align_faalt_hard_bij_verkeerd_aantal():
    try:
        bes.align({1: "eerste", 2: "tweede"}, bes.split_besluit("1,2,3"))
    except bes.BesluitFout:
        return
    raise AssertionError("verwachtte BesluitFout bij 3 besluiten op 2 acties")


def test_align_faalt_hard_bij_onbekend_nummer():
    try:
        bes.align({1: "eerste"}, bes.split_besluit("7 prima"))
    except bes.BesluitFout:
        return
    raise AssertionError("verwachtte BesluitFout bij een nummer zonder actie")


# ---------------------------------------------------------------------------
# Besluiten: de regel-fastpath (exact-match, buiten het model om)
# ---------------------------------------------------------------------------

def test_fastpath_keurt_kale_goedkeuring_goed():
    for ann in ("", "prima", "wel", "ja", "OK", "Akkoord", "prima."):
        assert bes.regel_label(ann) == bes.DOEN, ann


def test_fastpath_wijst_kale_afwijzing_af():
    for ann in ("niet", "nee", "Nee", "nvt"):
        assert bes.regel_label(ann) == bes.NIET, ann


def test_fastpath_laat_vrije_tekst_aan_het_model():
    # "geen ..." is een VOORWAARDE, geen afwijzing -- de fastpath mag hier niet gokken
    for ann in ("geen specifieke frameworks benoemen", "nee dat is advanced",
                "in inleiding is dat prima", "nuanceer"):
        assert bes.regel_label(ann) is None, ann


# ---------------------------------------------------------------------------
# Besluiten: wat de schrijver te zien krijgt
# ---------------------------------------------------------------------------

def _besluit(besluit, voorwaarde=""):
    return bes.Besluit(1, "T", 1, "refresh: doe iets", voorwaarde, besluit,
                       voorwaarde, bes.BRON_LLM)


def test_voorwaarde_gaat_altijd_mee_naar_de_schrijver():
    # ook bij `doen`: het label bepaalt of de actie doorgaat, niet of de reviewer
    # gehoord wordt -- anders verdampt de aantekening bij een net-verkeerd label
    for label, kop in ((bes.MITS, "VOORWAARDE"), (bes.DOEN, "VOORWAARDE"), (bes.NIET, "REDEN")):
        tekst = _besluit(label, "alleen in de inleiding").als_instructie()
        assert "alleen in de inleiding" in tekst
        assert kop in tekst


def test_kale_goedkeuring_voegt_niets_toe():
    assert _besluit(bes.DOEN).als_instructie() == "refresh: doe iets"


def test_splits_scheidt_goedgekeurd_van_afgewezen():
    goed, afgewezen = bes.splits([_besluit(bes.DOEN), _besluit(bes.MITS), _besluit(bes.NIET)])
    assert len(goed) == 2 and len(afgewezen) == 1


# ---------------------------------------------------------------------------
# Output: document -> CMS-content
# ---------------------------------------------------------------------------

def _document() -> dict:
    return {
        "overzicht": "Wil je slimmer werken?",
        "inleiding": "Eerste alinea.\n\nTweede alinea.",
        "modules": {"opening": sjabloon.modules_opening("Cursus XML"),
                    "modules": [{"titel": "M1", "bullets": ["a", "b"]}]},
        "doelgroep": "Deze training is voor iedereen.",
        "voorkennis": sjabloon.VOORKENNIS_FALLBACK,
        "aanpak": sjabloon.AANPAK_ALINEA_1.format(invulling="je dit toepast")
                  + "\n\n" + sjabloon.AANPAK_ALINEA_2,
        "doelen": {"intro": sjabloon.DOELEN_INTRO, "bullets": ["Doen van dingen"]},
        "vervolgstappen": {"alineas": [sjabloon.VERVOLG_ALINEA_1, sjabloon.VERVOLG_ALINEA_2],
                           "titels": ["Power BI"], "afsluiter": sjabloon.VERVOLG_AFSLUITER},
        "kortste_omschrijving": "Wil je dit leren?",
        "certificatie": sjabloon.CERTIFICATIE,
    }


def test_content_heeft_dezelfde_sleutels_als_de_bron():
    bron = {"days": 3, "intro": "", "setup": "", "modules": "", "summary": "",
            "follow_up": "", "objectives": "", "certification": "", "summary_edudex": "",
            "prior_knowledge": "", "target_audience": ""}
    content = uit.document_to_content(_document(), bron)
    assert set(content) == set(bron), set(bron) ^ set(content)


def test_days_wordt_ongewijzigd_overgenomen():
    content = uit.document_to_content(_document(), {"days": 7})
    assert content["days"] == 7


def test_summary_is_platte_tekst_geen_html():
    content = uit.document_to_content(_document(), {})
    assert "<" not in content["summary"] and "<" not in content["summary_edudex"]


def test_inleiding_krijgt_het_bedrijfstrainingblok_als_kop_3():
    content = uit.document_to_content(_document(), {})
    assert f"<h3>{sjabloon.BEDRIJFSTRAINING_KOP}</h3>" in content["intro"]
    assert sjabloon.BEDRIJFSTRAINING_TEKST in content["intro"]


def test_modules_gebruikt_geneste_lijsten():
    content = uit.document_to_content(_document(), {})
    assert content["modules"].count("<ul>") == 2   # buitenste + één module
    assert "<h3>" not in content["modules"]


def test_geen_placeholder_of_oplnaam_in_de_output():
    content = uit.document_to_content(_document(), {})
    samen = " ".join(v for v in content.values() if isinstance(v, str))
    assert "{{ oplnaam }}" not in samen and "[" not in samen


def test_vervolgstappen_met_groepen_krijgt_per_groep_een_eigen_intro():
    doc = _document()
    doc["vervolgstappen"]["groepen"] = [
        {"intro": "Wil je je verder verdiepen:", "titels": ["Training Power BI"]},
        {"intro": "Wil je juist verbreden:", "titels": ["Training T-SQL", "Training Python"]},
    ]
    html = uit.document_to_content(doc, {})["follow_up"]
    assert "Wil je je verder verdiepen:" in html and "Wil je juist verbreden:" in html
    assert html.count("<ul>") == 2
    # de vaste aankondiging hoort er dan NIET meer boven te staan
    assert sjabloon.VERVOLG_LIJST_INTRO not in html


def test_vervolgstappen_zonder_groepen_valt_terug_op_een_vlakke_lijst():
    html = uit.document_to_content(_document(), {})["follow_up"]
    assert sjabloon.VERVOLG_LIJST_INTRO in html
    assert html.count("<ul>") == 1


def test_soortwoord_wordt_niet_verdubbeld():
    assert "de Training Opleiding" not in sjabloon.modules_opening("Opleiding PHP Professional")
    assert sjabloon.modules_opening("Cursus XML").startswith("Tijdens de Training XML")
    assert sjabloon.modules_opening("Photoshop").startswith("Tijdens de Training Photoshop")
    assert sjabloon.modules_opening("Masterclass C#").startswith("Tijdens de Masterclass C#")


def test_markdown_heeft_kop_1_2_en_3():
    md = uit.render_markdown(_document(), "Cursus XML")
    assert md.startswith("# Cursus XML")
    for kopje in sjabloon.KOPJES:
        assert f"## {kopje.kop}" in md, kopje.kop
    assert f"### **{sjabloon.BEDRIJFSTRAINING_KOP}**" in md


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
