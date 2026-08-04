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
import json
import os
import tempfile
from types import SimpleNamespace

import besluiten as bes
import rewrite_output as uit
import rewrite_trainings as rw
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


def _codes_in(issues, section, severity=None):
    """Codes van één kopje. Nodig waar de fixture elders al hetzelfde signaal geeft --
    `_good_rewrite` bestaat uit herhaalde woorden zonder interpunctie, dus de Inleiding is
    formeel één zin van 195 woorden."""
    return _codes([i for i in issues if i.section == section], severity)


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
    rw["inleiding"] = " ".join(["onderwerp"] * 280)
    assert "lengte_woorden" in _codes(check_rewrite(rw, _CTX), HARD)


# ---------------------------------------------------------------------------
# Lengte is een richtlijn met een vangrail: net eroverheen mag, ver eroverheen niet
# ---------------------------------------------------------------------------

def test_korte_net_buiten_richtlijn_is_flag():
    rw = _good_rewrite()
    rw["overzicht"] = "Wil je " + " ".join(["data"] * 70) + "?"   # 72 woorden
    issues = check_rewrite(rw, _CTX)
    assert "lengte_woorden" not in _codes(issues, HARD)
    assert "lengte_richtlijn" in _codes(issues, FLAG)


def test_korte_buiten_vangrail_is_hard():
    rw = _good_rewrite()
    rw["overzicht"] = "Wil je " + " ".join(["data"] * 100) + "?"
    assert "lengte_woorden" in _codes(check_rewrite(rw, _CTX), HARD)


def test_algemene_net_buiten_richtlijn_is_flag():
    rw = _good_rewrite()
    rw["inleiding"] = " ".join(["onderwerp"] * 228)
    issues = check_rewrite(rw, _CTX)
    assert "lengte_woorden" not in _codes(issues, HARD)
    assert "lengte_richtlijn" in _codes(issues, FLAG)


def test_inleidingsband_schuift_met_dagen():
    """Een training van vijf dagen mag een langere Inleiding hebben dan een van één dag."""
    rw = _good_rewrite()
    rw["inleiding"] = " ".join(["onderwerp"] * 228)
    lang = check_rewrite(rw, dict(_CTX, dagen=5))
    kort = check_rewrite(rw, dict(_CTX, dagen=1))
    assert "lengte_richtlijn" not in _codes(lang, FLAG)   # binnen de band voor 5 dagen
    assert "lengte_richtlijn" in _codes(kort, FLAG)       # te lang voor een eendaagse
    # de vangrail schuift mee, maar niet oneindig
    rw["inleiding"] = " ".join(["onderwerp"] * 265)
    assert "lengte_woorden" not in _codes(check_rewrite(rw, dict(_CTX, dagen=5)), HARD)
    assert "lengte_woorden" in _codes(check_rewrite(rw, dict(_CTX, dagen=2)), HARD)


def test_lange_zin_is_flag_geen_hardfail():
    rw = _good_rewrite()
    # 40 woorden in één zin: ver boven de richtlijn van ±20, dus een signaal -- maar de
    # schrijver hoort er niet voor terug te moeten.
    rw["doelgroep"] = "Deze training is voor " + " ".join(["iedereen"] * 37) + "."
    issues = check_rewrite(rw, _CTX)
    assert "zin_lang" in _codes_in(issues, "doelgroep", FLAG)
    assert "zin_lang" not in _codes(issues, HARD)


def test_zin_boven_de_richtlijn_is_geen_flag():
    """25 woorden is langer dan ±20 en moet gewoon mogen: de richtlijn is geen plafond."""
    rw = _good_rewrite()
    rw["voorkennis"] = "Je werkt " + " ".join(["dagelijks"] * 23) + "."
    assert "zin_lang" not in _codes_in(check_rewrite(rw, _CTX), "voorkennis", FLAG)


def test_zinlengte_kijkt_niet_naar_bullets():
    """Bullets zijn geen zinnen; een lange module-bullet is geen zinlengte-signaal."""
    rw = _good_rewrite()
    rw["modules"]["modules"][0]["bullets"][0] = " ".join(["onderdeel"] * 45)
    assert "zin_lang" not in _codes_in(check_rewrite(rw, _CTX), "modules", FLAG)


def test_overzichtsband_negeert_dagen():
    """Het Overzicht is de aanhaakalinea; die blijft even lang, hoe lang de training ook duurt."""
    rw = _good_rewrite()
    rw["overzicht"] = "Wil je " + " ".join(["data"] * 70) + "?"
    for dagen in (1, 5):
        assert "lengte_richtlijn" in _codes(check_rewrite(rw, dict(_CTX, dagen=dagen)), FLAG)


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


# ---------------------------------------------------------------------------
# Verboden woorden (humanisering_nl.md Sectie D)
# ---------------------------------------------------------------------------

def test_professionals_is_hard_in_elk_kopje():
    """Het verbod gold alleen in doelgroep; het geldt nu overal."""
    rw = _good_rewrite()
    rw["inleiding"] = rw["inleiding"].replace("onderwerp", "professionals", 1)
    assert "professionals" in _codes(check_rewrite(rw, _CTX), HARD)


def test_professionals_in_de_titel_blijft_toegestaan():
    """"Training PHP Professional" bestaat echt; die training moet zichzelf kunnen noemen."""
    rw = _good_rewrite()
    rw["nieuwe_titel"] = "Training PHP Professional"
    ctx = dict(_CTX, naam="Training PHP Professional")
    assert "professionals" not in _codes(check_rewrite(rw, ctx), HARD)


def test_professionals_degradeert_naar_flag_bij_professional_titel():
    rw = _good_rewrite()
    rw["doelgroep"] = "Deze training is voor iedereen die de PHP Professional-stof wil beheersen."
    ctx = dict(_CTX, naam="Training PHP Professional")
    codes = _codes(check_rewrite(rw, ctx))
    assert "professionals" in codes
    assert "professionals" not in _codes(check_rewrite(rw, ctx), HARD)


def test_bezig_met_is_hard():
    rw = _good_rewrite()
    rw["overzicht"] = rw["overzicht"].replace(
        "Wil je", "Wil je weten waarmee je houdt je bezig met data en", 1)
    assert "bezig_met" in _codes(check_rewrite(rw, _CTX), HARD)


def test_meeting_is_flag_geen_hard_fail():
    """Vakterm in Scrum/Agile/Teams-trainingen, dus signaleren en niet blokkeren."""
    rw = _good_rewrite()
    rw["doelgroep"] = "Deze training is voor iedereen die elke meeting beter wil voorbereiden."
    assert "meeting" in _codes(check_rewrite(rw, _CTX), FLAG)
    assert "meeting" not in _codes(check_rewrite(rw, _CTX), HARD)


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


def test_doelen_herhalen_in_staat_niet():
    """De introzin zegt al "ben je in staat om"; een bullet mag dat niet dubbelen."""
    rw = _good_rewrite()
    rw["doelen"][0] = "In staat te zijn om heldere dashboards te bouwen"
    assert "dubbel_in_staat" in _codes(check_rewrite(rw, _CTX), FLAG)


def test_doelen_inzicht_constructie_is_goed():
    """"Inzicht te krijgen in ..." is een aanbevolen causale constructie, geen fout."""
    rw = _good_rewrite()
    rw["doelen"][0] = "Inzicht te krijgen in de kosten van je datamodel"
    codes = _codes(check_rewrite(rw, _CTX))
    assert "geen_te_infinitief" not in codes
    assert "dubbel_in_staat" not in codes


def test_doelen_lopen_door_op_de_vaste_introzin():
    """Elke bullet moet als één zin achter sjabloon.DOELEN_INTRO te lezen zijn."""
    rw = _good_rewrite()
    assert sjabloon.DOELEN_INTRO.endswith("om:")
    for bullet in rw["doelen"]:
        zin = f"{sjabloon.DOELEN_INTRO[:-1]} {bullet[0].lower()}{bullet[1:]}"
        assert " te " in zin, zin


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


# ---------------------------------------------------------------------------
# Vervolgtrainingen: taxonomieboom + shortlist
# ---------------------------------------------------------------------------

def _mini_catalog() -> list[dict]:
    """Catalogus met net genoeg structuur om de shortlist te kunnen sturen.

    XSL en JavaScript delen geen woord maar wel een vakgebied; Active Directory deelt
    wél woorden met LDAP maar hangt in een ander domein. Precies de twee gevallen waarop
    keyword-overlap en boom afzonderlijk stukgaan.
    """
    rijen = [
        (1, "Cursus XSL", "Transformeer XML-documenten met stylesheets."),
        (2, "Cursus JavaScript", "Programmeer interactie in de browser."),
        (3, "Cursus Node.js", "Bouw serverapplicaties in de browsertaal."),
        (4, "Cursus LDAP", "Ontsluit centraal opgeslagen directory-gegevens via het netwerk."),
        (5, "Training Active Directory", "Beheer directory-gegevens en gebruikers centraal."),
        (6, "Training 5G Mobiele Communicatie", "Mobiele netwerken van de vijfde generatie."),
        (7, "Training Bloemschikken", "Niets met techniek te maken."),
        (8, "Opleiding C# Professional", "Professioneel programmeren in C-sharp."),
        (9, "Training C++ Professional", "Professioneel programmeren in C-plus-plus."),
        (10, "Training Claude CoWork", "Samenwerken met een AI-assistent."),
    ]
    return rw.catalog_uit_rijen([
        {"product_id": pid, "titel": t, "summary": s} for pid, t, s in rijen])


_MINI_BOOM = {
    "name": "Trainingscatalogus",
    "children": [
        {"name": "Software Development", "children": [
            {"name": "Web Development", "children": [
                {"name": "Cursus XSL"}, {"name": "Cursus JavaScript"}, {"name": "Cursus Node.js"},
            ]},
            {"name": "Programmeertalen", "children": [
                {"name": "Opleiding C# Professional"}, {"name": "Training C++ Professional"},
            ]},
        ]},
        {"name": "Cloud & Infrastructuur", "children": [
            {"name": "Netwerken & Connectiviteit", "children": [
                {"name": "Cursus LDAP"}, {"name": "Training 5G Mobiele Communicatie"},
            ]},
        ]},
        {"name": "Modern Workplace", "children": [
            {"name": "Identity & Access Management", "children": [
                {"name": "Training Active Directory"},
            ]},
        ]},
        # bestaat niet in de catalogus -> mag nooit in de index belanden
        {"name": "Data & Analytics", "children": [
            {"name": "Big Data", "children": [{"name": "Cursus Apache Pig"}]},
        ]},
    ],
}


def _mini_boom(catalog, boom=None):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(boom if boom is not None else _MINI_BOOM, f)
        pad = f.name
    try:
        return rw.load_tree(catalog, pad)
    finally:
        os.unlink(pad)


def test_boom_bevat_alleen_bestaande_catalogustitels():
    """De veiligheidsgarantie: wat hier in staat, overleeft check_vervolgstappen.

    Elke titel buiten de catalogus is een HARD `titel_onbekend`, dus een blad zonder
    catalogusrij (vervallen aanbod als Apache Pig) mag niet als kandidaat naar buiten.
    """
    catalog = _mini_catalog()
    boom = _mini_boom(catalog)
    bekend = {t.strip().lower() for t in rw.catalog_titles(catalog)}
    assert boom["paden"], "de boom moet wél gevuld zijn"
    for sleutel in boom["paden"]:
        assert sleutel in bekend, sleutel
    assert "apache pig" not in boom["paden"]


def test_boom_koppelt_over_interpunctie_heen():
    """"Claude Co-Work" in de boom is dezelfde training als "Claude CoWork"."""
    catalog = _mini_catalog()
    boom = _mini_boom(catalog, {"name": "wortel", "children": [
        {"name": "Artificial Intelligence", "children": [
            {"name": "AI-assistenten", "children": [{"name": "Training Claude Co-Work"}]}]}]})
    assert "claude cowork" in boom["paden"]


def test_boom_slaat_ambigue_titels_over():
    """C# en C++ vallen na het strippen van interpunctie samen; dan liever niets koppelen."""
    catalog = _mini_catalog()
    boom = _mini_boom(catalog, {"name": "wortel", "children": [
        {"name": "Software Development", "children": [
            {"name": "Programmeertalen", "children": [{"name": "Cursus C＃ Professional"}]}]}]})
    # de exacte titel matcht niet en de losse sleutel is ambigu -> geen van beide gekoppeld
    assert "c# professional" not in boom["paden"]
    assert "c++ professional" not in boom["paden"]


def test_boom_kent_meerdere_takken():
    catalog = _mini_catalog()
    boom = _mini_boom(catalog, {"name": "wortel", "children": [
        {"name": "Software Development", "children": [
            {"name": "Web Development", "children": [{"name": "Cursus XSL"}]}]},
        {"name": "Data & Analytics", "children": [
            {"name": "Dataformaten", "children": [{"name": "Cursus XSL"}]}]}]})
    assert len(boom["paden"]["xsl"]) == 2


def test_shortlist_zonder_boom_is_ongewijzigd():
    """De boom is optioneel; zonder boom moet de oude volgorde er exact uit komen."""
    catalog = _mini_catalog()
    zonder = rw.shortlist_vervolgtrainingen(catalog, "Cursus XSL", "stylesheets en xml", 1)
    met_none = rw.shortlist_vervolgtrainingen(catalog, "Cursus XSL", "stylesheets en xml", 1,
                                              boom=None)
    assert [e["titel"] for e in zonder] == [e["titel"] for e in met_none]
    assert "XSL" not in [e["titel"] for e in zonder], "de training zelf hoort er niet in"


def test_shortlist_haalt_vakgenoot_zonder_woordoverlap_binnen():
    """XSL deelt geen woord met JavaScript, maar wel het subdomein Web Development."""
    catalog = _mini_catalog()
    boom = _mini_boom(catalog)
    zonder = {e["titel"] for e in
              rw.shortlist_vervolgtrainingen(catalog, "Cursus XSL", "stylesheets", 1, n=3)}
    met = {e["titel"] for e in
           rw.shortlist_vervolgtrainingen(catalog, "Cursus XSL", "stylesheets", 1, n=3, boom=boom)}
    assert "JavaScript" not in zonder
    assert "JavaScript" in met
    assert "Bloemschikken" not in met


def test_shortlist_houdt_de_sterkste_keyword_treffer_vast():
    """Een vol subdomein mag de beste treffer uit een ánder domein niet verdringen.

    LDAP hangt onder Netwerken, maar de logische vervolgstap (Active Directory) staat
    onder Identity. Die moet de unie overleven.
    """
    catalog = _mini_catalog()
    boom = _mini_boom(catalog)
    titels = [e["titel"] for e in rw.shortlist_vervolgtrainingen(
        catalog, "Cursus LDAP", "directory-gegevens centraal beheren", 4, n=3, boom=boom)]
    assert "Active Directory" in titels


def test_taxonomie_pad_kiest_de_tak_die_de_bron_deelt():
    """Hangt een kandidaat in meerdere takken, dan telt de tak van de bron."""
    catalog = _mini_catalog()
    boom = _mini_boom(catalog, {"name": "wortel", "children": [
        {"name": "Software Development", "children": [
            {"name": "Web Development", "children": [
                {"name": "Cursus XSL"}, {"name": "Cursus JavaScript"}]}]},
        {"name": "Data & Analytics", "children": [
            {"name": "Dataformaten", "children": [{"name": "Cursus JavaScript"}]}]}]})
    assert rw.taxonomie_pad(boom, "javascript", "xsl") == "Software Development > Web Development"
    assert rw.taxonomie_pad(boom, "javascript", "") in (
        "Software Development > Web Development", "Data & Analytics > Dataformaten")
    assert rw.taxonomie_pad(boom, "bloemschikken", "xsl") == ""


class _StubBlok:
    type = "tool_use"

    def __init__(self, naam, invoer):
        self.name, self.input = naam, invoer


class _StubResp:
    stop_reason = "tool_use"

    def __init__(self, content):
        self.content = content


class _StubMessages:
    def __init__(self, client, groepen):
        self._client, self._groepen = client, groepen

    def create(self, **kw):
        self._client.laatste = kw
        return _StubResp([_StubBlok("submit_vervolgstappen", {"groepen": self._groepen})])


class _StubClient:
    """Client die één vast tool-antwoord teruggeeft; geen netwerk, geen API-key."""

    def __init__(self, groepen):
        self.laatste = None
        self.messages = _StubMessages(self, groepen)


def test_kies_vervolgtrainingen_toont_het_vakgebied_maar_neemt_het_niet_over():
    """Het label stuurt de groepering; komt het terug in een titel, dan wordt het gestript."""
    catalog = _mini_catalog()
    boom = _mini_boom(catalog)
    shortlist = rw.shortlist_vervolgtrainingen(catalog, "Cursus XSL", "stylesheets", 1, boom=boom)
    client = _StubClient([{"intro": "Verdiep je verder:",
                           "titels": ["JavaScript [Software Development > Web Development]"]}])
    groepen = rw.kies_vervolgtrainingen(client, "Training XSL", "stylesheets", "A", shortlist,
                                        boom=boom, oude_titel="Cursus XSL")
    assert "[Software Development > Web Development]" in client.laatste["messages"][0]["content"]
    assert groepen == [{"intro": "Verdiep je verder:", "titels": ["JavaScript"]}]


def test_kies_vervolgtrainingen_weert_verzonnen_titels():
    catalog = _mini_catalog()
    client = _StubClient([{"intro": "Kijk ook naar:", "titels": ["Training Bestaat Niet"]}])
    shortlist = rw.shortlist_vervolgtrainingen(catalog, "Cursus XSL", "stylesheets", 1)
    assert rw.kies_vervolgtrainingen(client, "Training XSL", "x", "A", shortlist) == []


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


# ---------------------------------------------------------------------------
# Scoresheet-kolomnamen: handgemaakte lijsten houden vaak `id`/`name`
# ---------------------------------------------------------------------------

def _scored_df(**kolommen):
    import pandas as pd
    basis = {"actualiteit_actie": ["1. refresh: eerste"], "actie_besluit": ["1 prima"]}
    return pd.DataFrame({**basis, **kolommen})


def test_normaliseer_hernoemt_bronkolomnamen():
    df = bes.normaliseer_scored_kolommen(_scored_df(id=[42], name=["Training XML"]))
    assert "training_id" in df.columns and "titel" in df.columns
    assert df["training_id"].iloc[0] == 42
    assert df["titel"].iloc[0] == "Training XML"


def test_normaliseer_laat_een_scorersheet_met_rust():
    """Staat `training_id` er al, dan wint die -- ook naast een losse `id`-kolom."""
    df = bes.normaliseer_scored_kolommen(
        _scored_df(training_id=[42], titel=["Training XML"], id=[999], name=["iets anders"]))
    assert df["training_id"].iloc[0] == 42
    assert df["titel"].iloc[0] == "Training XML"


def test_load_scored_accepteert_id_en_name():
    with tempfile.TemporaryDirectory() as d:
        pad = os.path.join(d, "prio.xlsx")
        _scored_df(id=[42], name=["Training XML"]).to_excel(pad, index=False)
        assert bes._load_scored(pad)["training_id"].iloc[0] == 42
        assert rw._load_scored(pad)["training_id"].iloc[0] == 42


def test_load_scored_faalt_nog_steeds_zonder_id_kolom():
    with tempfile.TemporaryDirectory() as d:
        pad = os.path.join(d, "zonder_id.xlsx")
        _scored_df(willekeurig=["x"]).to_excel(pad, index=False)
        try:
            bes._load_scored(pad)
        except ValueError as e:
            assert "training_id" in str(e)
            return
    raise AssertionError("verwachtte ValueError bij een sheet zonder id-kolom")


# ---------------------------------------------------------------------------
# besluiten.xlsx: meerdere batches naast elkaar
# ---------------------------------------------------------------------------

def test_besluiten_sheet_behoudt_trainingen_buiten_het_scoresheet():
    """Een prioriteitslijst mag de besluiten van de rest van de catalogus niet wissen."""
    import pandas as pd
    with tempfile.TemporaryDirectory() as d:
        besluiten_pad = os.path.join(d, "besluiten.xlsx")
        eerste = os.path.join(d, "batch_a.xlsx")
        tweede = os.path.join(d, "batch_b.xlsx")
        _scored_df(training_id=[1], titel=["Training A"]).to_excel(eerste, index=False)
        _scored_df(id=[2], name=["Training B"]).to_excel(tweede, index=False)

        bes.write_besluiten_sheet(eerste, besluiten_pad, verbose=False)
        bes.write_besluiten_sheet(tweede, besluiten_pad, verbose=False)

        df = pd.read_excel(besluiten_pad)
        assert set(df["training_id"]) == {1, 2}
        assert list(df.columns) == bes.KOLOMMEN
        assert set(bes.load_besluiten(besluiten_pad)) == {1, 2}


def test_besluiten_sheet_ververst_de_eigen_trainingen():
    """Opnieuw draaien op hetzelfde sheet dupliceert niet en neemt de nieuwe actietekst mee."""
    import pandas as pd
    with tempfile.TemporaryDirectory() as d:
        besluiten_pad = os.path.join(d, "besluiten.xlsx")
        scored = os.path.join(d, "batch.xlsx")
        _scored_df(training_id=[1], titel=["Training A"]).to_excel(scored, index=False)
        bes.write_besluiten_sheet(scored, besluiten_pad, verbose=False)

        gewijzigd = _scored_df(training_id=[1], titel=["Training A"])
        gewijzigd["actualiteit_actie"] = ["1. refresh: herzien"]
        gewijzigd.to_excel(scored, index=False)
        bes.write_besluiten_sheet(scored, besluiten_pad, verbose=False)

        df = pd.read_excel(besluiten_pad)
        assert len(df) == 1
        assert df["actie"].iloc[0] == "refresh: herzien"


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
# De kern: het enige veld dat het niveau van de training vastlegt
#
# De reviewer stuurt bij in `kern_reviewer`; de scorer-kern blijft ernaast staan zodat een
# herscoring hem mag verversen. Wie de kern schreef bepaalt hoeveel gezag hij heeft.
# ---------------------------------------------------------------------------

def _briefing(**overrides) -> rw.RewriteBriefing:
    basis = dict(training_id=1, titel="Cursus Big Data Foundation", persona="A", dagen=2,
                 kern="Scorer leest de training als een gestructureerde aanpak.",
                 verdict="redelijk", actualiteit_type="additief",
                 source_text="[intro]\nWe introduceren het CRISP-DM model.")
    return rw.RewriteBriefing(**{**basis, **overrides})


def test_reviewer_kern_wint_van_scorer_kern():
    b = _briefing(kern_reviewer="Introducerend: de deelnemer maakt kennis met het proces.")
    assert b.kern_definitief == "Introducerend: de deelnemer maakt kennis met het proces."
    assert b.kern_van_reviewer


def test_zonder_reviewer_kern_geldt_de_scorer_kern():
    b = _briefing()
    assert b.kern_definitief == b.kern
    assert not b.kern_van_reviewer


def test_lege_reviewer_kern_telt_niet_als_oordeel():
    """Een lege Excel-cel komt binnen als NaN of als spaties; geen van beide is een oordeel."""
    for leeg in ("", "   ", "\n"):
        assert not _briefing(kern_reviewer=leeg).kern_van_reviewer
    assert rw._cel(float("nan")) == ""
    assert rw._cel(None) == ""
    assert rw._cel("  echte tekst  ") == "echte tekst"


def test_gezagsregel_volgt_de_herkomst_van_de_kern():
    """Bij een scorer-kern wint de bron; bij een reviewer-kern wint de kern."""
    scorer = rw.build_writer_user(_briefing())
    assert "lezing van de scorer" in scorer
    assert "wint de BRONTEKST" in scorer

    reviewer = rw.build_writer_user(_briefing(kern_reviewer="Introducerende training."))
    assert "vastgesteld door reviewer" in reviewer
    assert "Hij is leidend" in reviewer
    assert "wint de BRONTEKST" not in reviewer


def test_writer_prompt_zet_de_brontekst_neer_als_de_training_zelf():
    tekst = rw.build_writer_user(_briefing())
    assert "BRONTEKST" in tekst
    assert "Beloof nooit meer dan hier staat." in tekst
    assert tekst.rstrip().endswith("We introduceren het CRISP-DM model.")


def test_judge_krijgt_de_kern_met_herkomst():
    """Zonder de kern kan de judge het niveau niet toetsen -- dat kon hij eerder niet."""
    doc = {"titel": "Training Big Data Foundation", "overzicht": "Wil je iets?"}
    scorer = rw.build_judge_user(_briefing(), doc)
    assert "KERN (lezing van de scorer)" in scorer
    reviewer = rw.build_judge_user(_briefing(kern_reviewer="Introducerend."), doc)
    assert "KERN (vastgesteld door reviewer)" in reviewer
    assert "Introducerend." in reviewer


def test_judge_krijgt_de_brontekst_en_alle_feiten():
    """§2 van de beoordelingsspec vraagt te herleiden tot "de brontekst" -- die kreeg hij niet.

    Zonder de bron toetste de judge feitgetrouwheid tegen de samenvatting van de scorer: een
    claim die de brontekst tegensprak maar `bruikbaar` niet, kwam er ongehinderd doorheen. Dat
    weegt zwaarder sinds de kern het niveau draagt: zwijgt de kern over een aspect, dan had de
    judge niets om op terug te vallen.
    """
    doc = {"titel": "Training Big Data Foundation", "overzicht": "Wil je iets?"}
    tekst = rw.build_judge_user(
        _briefing(bruikbaar=["CRISP-DM 6 fasen"], strippen=["verouderde prijsinfo"],
                  gaten=["voorkennis niet beschreven"]),
        doc)
    assert "We introduceren het CRISP-DM model." in tekst
    # strippen en gaten stonden wél in de spec beloofd, maar gingen niet mee
    assert "verouderde prijsinfo" in tekst
    assert "voorkennis niet beschreven" in tekst
    # het concept staat achteraan: de bron is naslag, het concept is wat hij beoordeelt
    assert tekst.index("BRONTEKST") < tekst.index("CONCEPT — dit is wat je beoordeelt")


def test_judge_mag_de_bron_niet_als_vormnorm_gebruiken():
    """Zonder deze regel rekent de judge het concept af op het niet volgen van de bronstructuur
    -- en juist daarvan afwijken is het hele punt van herschrijven."""
    tekst = rw.build_judge_user(_briefing(), {"titel": "T", "overzicht": "Wil je iets?"})
    assert "Reken het concept NIET af op vorm" in tekst
    assert "FEITGETROUWHEID" in tekst and "NIVEAU" in tekst


def test_goedgekeurde_actualisering_gaat_voor_de_brontekst():
    """De bron is de maatstaf voor claims -- behalve voor wat de reviewer heeft goedgekeurd.

    Een actualisering voegt per definitie iets toe dat niet in de bron staat of haalt iets weg; dat is waarom
    hij bestaat. Zonder deze uitzondering is elke goedgekeurde actie een "verzonnen feit" en
    draait de review precies het werk terug dat de reviewer in de sessie deed. Geldt aan
    beide kanten: de schrijver mag er geen laten liggen, de judge mag er geen afkeuren.
    """
    actie = bes.Besluit(1, "T", 1, "Voeg GA4 toe (bron noemt alleen Universal Analytics)",
                        "prima, mits als voorbeeld", "mits", "alleen als voorbeeld",
                        "handmatig")
    b = _briefing(goedgekeurd=[actie])

    schrijver = rw.build_writer_user(b)
    assert "Eén uitzondering, en die gaat vóór" in schrijver
    # de uitzondering staat ná "Beloof nooit meer dan hier staat", anders leest de schrijver
    # dat verbod als het laatste woord en laat hij de actie liggen
    assert schrijver.index("Beloof nooit meer dan hier staat") < schrijver.index("uitzondering")

    judge = rw.build_judge_user(b, {"titel": "T", "overzicht": "Wil je iets?"})
    assert "UITZONDERING, en die gaat vóór allebei" in judge
    assert "nooit af als ongegrond" in judge
    # de voorwaarde is de enige grens, en die gaat mee naar allebei
    assert "alleen als voorbeeld" in schrijver and "alleen als voorbeeld" in judge


def test_judge_oordeel_met_verkeerd_gevormd_blok_loopt_niet_stuk():
    """Het tool-schema dwingt de vorm niet af: één meetrun leverde `feitgetrouw` als string.

    Ongefilterd loopt dat verderop stuk op `judgment["feitgetrouw"].get("thin")` -- midden in
    een batch, ná de dure schrijfcall.
    """
    kapot = {"verdict": "approved", "feitgetrouw": "alles klopt", "judge_confidence": "high"}
    rw._call_tool, echt = (lambda *a, **k: kapot), rw._call_tool
    try:
        out = rw.judge_document(None, _briefing(), {"titel": "T"})
    finally:
        rw._call_tool = echt
    assert out["verdict"] == "approved"          # de rest van het oordeel blijft bruikbaar
    assert out["feitgetrouw"] == {}
    assert "feitgetrouw" in out["judge_vorm"]    # maar het misgaan blijft zichtbaar
    assert out.get("feitgetrouw", {}).get("thin", False) is False


def test_build_briefing_leest_de_reviewerkolom():
    scored = {"training_id": 7, "kern": "scorer-kern", "kern_reviewer": "reviewer-kern",
              "verdict": "rijk", "vermoedelijk_persona": "B"}
    b = rw.build_briefing(scored, {"days": 3, "intro": "tekst"}, "Cursus X")
    assert b.kern_definitief == "reviewer-kern" and b.kern_van_reviewer
    # ontbreekt de kolom (oud scoresheet), dan valt hij stil terug op de scorer-kern
    zonder = rw.build_briefing({k: v for k, v in scored.items() if k != "kern_reviewer"},
                               {"days": 3, "intro": "tekst"}, "Cursus X")
    assert zonder.kern_definitief == "scorer-kern" and not zonder.kern_van_reviewer


def test_reviewtabblad_zet_de_brontekst_naast_de_nieuwe_tekst():
    """De mens kon alleen de nieuwe tekst lezen, dus niet zien of een claim de bron dekt."""
    res = rw.RewriteResult(1, "Training Big Data Foundation", rw.APPROVED,
                           oude_titel="Cursus Big Data Foundation")
    rij = rw._review_rij(res, {}, {"intro": "We introduceren het CRISP-DM model."})
    assert "We introduceren het CRISP-DM model." in rij["brontekst"]
    # de kolom staat achteraan, naast de kopjes -- niet tussen de statusvelden
    assert list(rij)[-1] == "brontekst"
    # zonder bron (overgenomen zonder bronrij) blijft de cel gewoon leeg
    assert rw._review_rij(res, {})["brontekst"] == ""


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
# Artefacten op schijf: <id>.json + <id>.md
# ---------------------------------------------------------------------------

def _resultaat(document=None) -> rw.RewriteResult:
    return rw.RewriteResult(5, "Training XML", rw.APPROVED, document=document)


def test_artefacten_schrijven_json_en_markdown_naast_elkaar():
    doc = _document()
    with tempfile.TemporaryDirectory() as d:
        paden = rw.schrijf_training_artefacten(d, 5, _resultaat(doc), {"days": 3})
        assert sorted(os.listdir(d)) == ["5.json", "5.md"]
        with open(paden["md"], encoding="utf-8") as f:
            md = f.read()
        # identiek aan wat het notebook onder de cel toont
        assert md == uit.render_markdown(doc, "Training XML")
        with open(paden["json"], encoding="utf-8") as f:
            assert json.load(f)["content"] == {"days": 3}


def test_zonder_document_geen_markdown_en_een_oude_md_gaat_weg():
    """Een .md van een vorige run mag niet bij een nieuwere JSON blijven liggen."""
    with tempfile.TemporaryDirectory() as d:
        rw.schrijf_training_artefacten(d, 5, _resultaat(_document()), {})
        res = rw.RewriteResult(5, "Training XML", "error", reden="schrijver faalde")
        paden = rw.schrijf_training_artefacten(d, 5, res, {})
        assert paden["md"] is None
        assert os.listdir(d) == ["5.json"]


def test_json_default_zet_numpy_scalars_om():
    import numpy as np
    assert rw._json_default(np.int64(2347)) == 2347
    assert isinstance(rw._json_default(np.int64(2347)), int)
    assert rw._json_default(np.bool_(True)) is True


def test_json_default_laat_echt_onserialiseerbare_objecten_falen():
    try:
        rw._json_default(object())
    except TypeError as e:
        assert "not JSON serializable" in str(e)
        return
    raise AssertionError("verwachtte TypeError voor een gewoon object")


def test_artefacten_verdragen_een_numpy_training_id():
    """Regressie: training_id komt uit een DataFrame en is dus numpy.int64, geen int."""
    import numpy as np
    res = rw.RewriteResult(np.int64(2347), "Training XML", rw.APPROVED, document=_document())
    res.thin = np.bool_(True)
    with tempfile.TemporaryDirectory() as d:
        paden = rw.schrijf_training_artefacten(d, res.training_id, res, {"days": np.int64(3)})
        assert sorted(os.listdir(d)) == ["2347.json", "2347.md"]
        with open(paden["json"], encoding="utf-8") as f:
            opgeslagen = json.load(f)
        assert opgeslagen["training_id"] == 2347
        assert type(opgeslagen["training_id"]) is int
        assert opgeslagen["thin"] is True
        assert opgeslagen["content"]["days"] == 3


def test_mislukt_schrijven_laat_de_vorige_versie_staan():
    """Een fout halverwege mag geen half artefact achterlaten (dat was de crash van cel 5)."""
    with tempfile.TemporaryDirectory() as d:
        pad = os.path.join(d, "5.json")
        with open(pad, "w", encoding="utf-8") as f:
            f.write('{"heel": "bestand"}')

        def stuk(f):
            f.write('{"half":')
            raise ValueError("simuleert een serialisatiefout")

        try:
            rw._schrijf_atomisch(pad, stuk)
        except ValueError:
            pass
        else:
            raise AssertionError("verwachtte dat de fout doorgegeven werd")

        with open(pad, encoding="utf-8") as f:
            assert json.load(f) == {"heel": "bestand"}
        assert os.listdir(d) == ["5.json"], "tmp-bestand niet opgeruimd"


def test_bewaar_training_zet_de_artefacten_in_trainingen():
    with tempfile.TemporaryDirectory() as d:
        paden = rw.bewaar_training(d, _resultaat(_document()), {"days": 7})
        assert paden["json"] == os.path.join(d, "trainingen", "5.json")
        with open(paden["json"], encoding="utf-8") as f:
            # dezelfde CMS-content als de batch schrijft, inclusief `days` uit de bron
            assert json.load(f)["content"]["days"] == 7


# ---------------------------------------------------------------------------
# De mate van aanpassing: twee assen (herschrijfniveau + actualiseringen)
# ---------------------------------------------------------------------------

def _content(**overrides) -> dict:
    """Bestaande CMS-content die aan het format voldoet, als vertrekpunt voor de scan."""
    basis = {
        "days": 2,
        "summary": "Wil je " + " ".join(["data"] * 57) + "?",
        "intro": "<p>" + " ".join(["onderwerp"] * 195) + "</p>",
        "modules": ("<p>opening</p><ul>"
                    "<li>Module een<ul><li>Punt a</li><li>Punt b</li><li>Punt c</li></ul></li>"
                    "<li>Module twee<ul><li>Punt a</li><li>Punt b</li><li>Punt c</li>"
                    "<li>Punt d</li></ul></li>"
                    "<li>Module drie<ul><li>Punt a</li><li>Punt b</li><li>Punt c</li></ul></li>"
                    "<li>Module vier<ul><li>Punt a</li><li>Punt b</li><li>Punt c</li>"
                    "<li>Punt d</li><li>Punt e</li></ul></li>"
                    "</ul>"),
        "target_audience": "<p>Deze training is voor iedereen die met data keuzes wil maken.</p>",
        "prior_knowledge": "<p>Specifieke voorkennis is niet noodzakelijk.</p>",
        "objectives": ("<p>Na deze training ben je in staat om:</p><ul>"
                       "<li>Datasets te ordenen en te controleren</li>"
                       "<li>Analyses te vertalen naar keuzes</li>"
                       "<li>Resultaten te presenteren aan je team</li>"
                       "<li>Vraagstukken gestructureerd te benaderen</li></ul>"),
        "summary_edudex": "Wil je slimmer met data werken en betere keuzes maken?",
        "setup": "<p>De training is praktijkgericht.</p>",
        "follow_up": "<p>Vervolg</p>",
        "certification": "<p>Certificaat</p>",
    }
    return {**basis, **overrides}


def test_modus_ladder_loopt_van_licht_naar_zwaar():
    assert rw.MODI == ("overnemen", "stijl", "format", "volledig")
    assert rw.MODUS_RANG["overnemen"] < rw.MODUS_RANG["stijl"] < rw.MODUS_RANG["format"]
    assert rw.hoogste_modus("stijl", "format") == "format"
    assert rw.hoogste_modus("volledig", "overnemen") == "volledig"
    # onbekende waarden vallen veilig terug, niet naar het lichtste niveau
    assert rw.normaliseer_modus("onzin") == rw.MODUS_DEFAULT == "volledig"
    assert rw.normaliseer_modus(float("nan")) == "volledig"
    assert rw.normaliseer_modus("  STIJL ") == "stijl"


def test_reviewer_modus_wint_van_het_voorstel():
    """Gezag volgt herkomst, net als bij de kern."""
    assert _briefing(modus_voorstel="format").modus == "format"
    assert _briefing(modus_voorstel="format", modus_reviewer="stijl").modus == "stijl"
    assert not _briefing(modus_voorstel="format").modus_van_reviewer
    assert _briefing(modus_reviewer="stijl").modus_van_reviewer


def test_herschreven_kolom_blijft_werken_zonder_de_nieuwe_kolommen():
    """Een scoresheet van vóór deze schaal moet zich exact gedragen als voorheen.

    `herschreven=1` betekende de facto al een modus ("niet aanraken"); zonder die terugval
    zou elke bestaande sheet ineens alles opnieuw laten schrijven.
    """
    assert _briefing(herschreven=True).modus == "overnemen"
    assert _briefing(herschreven=False).modus == "volledig"
    # een expliciet besluit gaat er wél overheen
    assert _briefing(herschreven=True, modus_reviewer="format").modus == "format"


def test_actualisering_verschuift_de_modus_niet():
    """As 2 staat los van as 1 -- dat is de kern van het ontwerp.

    Een goedgekeurde actie is een lokale toevoeging. Zou hij de modus ophogen, dan wordt de
    wijziging groter dan de reviewer vroeg: één toegevoegd onderwerp zou de hele training
    opnieuw laten schrijven.
    """
    actie = bes.Besluit(1, "T", 1, "Voeg GA4 toe", "ja", "doen", "", "handmatig")
    assert _briefing(modus_reviewer="stijl", goedgekeurd=[actie]).modus == "stijl"
    assert _briefing(herschreven=True, goedgekeurd=[actie]).modus == "overnemen"


def test_modules_parser_leest_beide_nestingsvormen():
    """De CMS-vorm zet de sub-lijst náást de titel-<li>, de renderer zet hem erin."""
    naast = ("<ul><li>Module een</li><ul><li>Punt a</li><li>Punt b</li></ul>"
             "<li>Module twee</li><ul><li>Punt c</li></ul></ul>")
    erin = ("<ul><li>Module een<ul><li>Punt a</li><li>Punt b</li></ul></li>"
            "<li>Module twee<ul><li>Punt c</li></ul></li></ul>")
    for html in (naast, erin):
        mods = rw._modules_uit_ul(html)
        assert [m["titel"] for m in mods] == ["Module een", "Module twee"], html
        assert [len(m["bullets"]) for m in mods] == [2, 1], html


def test_modules_in_h3_vorm_gelden_niet_als_leesbaar():
    """Onbekend is niet hetzelfde als conform: bij twijfel schat de scan omhoog."""
    h3 = ("<p>opening</p><h3>Module een</h3><p>Uitleg over de module.</p>"
          "<h3>Module twee</h3><p>Nog wat uitleg.</p>")
    assert not rw.modules_leesbaar(rw._modules_uit_ul(h3))
    assert rw.scan_vorm(_content(modules=h3), "Training X")["ondergrens"] == "format"


def test_scan_stelt_nooit_overnemen_voor():
    """De checks kunnen non-conformiteit bewijzen, conformiteit niet.

    Een tekst die elke regex haalt kan nog steeds het stijlregister negeren. Die conclusie
    mag daarom alleen van `schat_modus` of van een mens komen, nooit van de scan.
    """
    schoon = rw.scan_vorm(_content(), "Training Data")
    assert schoon["harde_issues"] == [], schoon["harde_issues"]
    assert schoon["ondergrens"] == "stijl"
    assert "niet door code vast te stellen" in schoon["reden"]


def test_scan_scheidt_structuur_van_formulering():
    """Ontbrekende kopjes en verkeerde aantallen -> format; verkeerde zinnen -> stijl."""
    # alleen de formulering deugt niet: de openingszin ontbreekt
    stijl = rw.scan_vorm(_content(summary="Deze training gaat over data."), "Training Data")
    assert stijl["ondergrens"] == "stijl"

    # een leeg verplicht kopje is structuur
    leeg = rw.scan_vorm(_content(objectives=""), "Training Data")
    assert leeg["ondergrens"] == "format"
    assert "doelen" in leeg["lege_kopjes"]

    # een verkeerd aantal sub-bullets ook
    krap = _content(modules="<ul><li>M1<ul><li>a</li></ul></li><li>M2<ul><li>b</li></ul></li></ul>")
    assert rw.scan_vorm(krap, "Training Data")["ondergrens"] == "format"


def test_scan_zonder_bron_of_met_onbruikbaar_verdict_gaat_naar_volledig():
    assert rw.scan_vorm({}, "T")["ondergrens"] == "volledig"
    assert rw.scan_vorm(_content(), "T", verdict="onbruikbaar")["ondergrens"] == "volledig"


def test_schat_modus_valt_terug_op_de_ondergrens_zonder_client():
    """Levert het model niets, dan blijft staan wat de checks al hebben weerlegd."""
    uitkomst = rw.schat_modus(None, _content(objectives=""), "Training Data")
    assert uitkomst["modus"] == uitkomst["ondergrens"] == "format"


def test_schat_modus_mag_niet_onder_de_ondergrens_zakken():
    """Een te licht voorstel wordt opgehoogd, met de reden erbij."""
    def _client(modus):
        """Minimale stub die één submit_modus-tooluse teruggeeft."""
        blok = SimpleNamespace(type="tool_use", name="submit_modus",
                               input={"modus": modus, "reden": "ziet er goed uit"})
        resp = SimpleNamespace(content=[blok], stop_reason="tool_use")
        return SimpleNamespace(messages=SimpleNamespace(create=lambda **_: resp))

    leeg = _content(objectives="")          # ondergrens = format
    op = rw.schat_modus(_client("overnemen"), leeg, "Training Data")
    assert op["modus"] == "format", op
    assert "opgehoogd naar de ondergrens" in op["reden"]
    # een zwaarder voorstel mag wél blijven staan
    zwaar = rw.schat_modus(_client("volledig"), leeg, "Training Data")
    assert zwaar["modus"] == "volledig"


def test_behoudende_modus_vervangt_de_brontekst_door_de_huidige_versie():
    """In stijl/format is de bestaande tekst het uitgangspunt, niet de brontekst.

    Twee keer dezelfde content meesturen kost tokens en geeft het model twee versies van de
    waarheid -- waarvan `source_text` de minst complete is (`setup`, `follow_up`,
    `summary_edudex` en `certification` ontbreken daar).
    """
    b = _briefing(modus_reviewer="stijl", huidige_content=_content())
    tekst = rw.build_writer_user(b)
    assert "HUIDIGE VERSIE" in tekst
    assert "BRONTEKST —" not in tekst
    assert "We introduceren het CRISP-DM model." not in tekst   # de source_text
    assert "redacteur, geen auteur" in tekst
    # de kopjes die build_source_text overslaat staan er nu wél in
    assert "Kortste omschrijving" in tekst

    # volledig blijft ongewijzigd: brontekst, geen huidige versie
    vol = rw.build_writer_user(_briefing(modus_reviewer="volledig", huidige_content=_content()))
    assert "BRONTEKST" in vol and "HUIDIGE VERSIE" not in vol
    assert vol.rstrip().endswith("We introduceren het CRISP-DM model.")


def test_zonder_bestaande_content_blijft_de_brontekst_staan():
    """Niets te behouden -> geen behoud-opdracht, ongeacht de modus."""
    tekst = rw.build_writer_user(_briefing(modus_reviewer="stijl", huidige_content={}))
    assert "BRONTEKST" in tekst and "HUIDIGE VERSIE" not in tekst


def test_actualiseer_alinea_verschijnt_alleen_met_goedgekeurde_acties():
    """Zonder deze alinea leest het model 'verander niets' als een verbod op de actie."""
    actie = bes.Besluit(1, "T", 1, "Voeg GA4 toe", "ja", "doen", "", "handmatig")
    met = rw.build_writer_user(_briefing(modus_reviewer="stijl", huidige_content=_content(),
                                         goedgekeurd=[actie]))
    assert "staan LOS van deze opdracht" in met
    zonder = rw.build_writer_user(_briefing(modus_reviewer="stijl", huidige_content=_content()))
    assert "staan LOS van deze opdracht" not in zonder


def test_judge_weet_in_welke_modus_hij_oordeelt():
    """Anders keurt hij een bewust conservatieve tekst af als 'te weinig herschreven'."""
    doc = {"titel": "T", "overzicht": "Wil je iets?"}
    stijl = rw.build_judge_user(_briefing(modus_reviewer="stijl", huidige_content=_content()), doc)
    assert "bewust ALLEEN bijgewerkt" in stijl
    assert "drift is hier een fail" in stijl
    # de bestaande instructies blijven staan; er komt alleen een as bij
    assert "Reken het concept NIET af op vorm" in stijl
    assert "FEITGETROUWHEID" in stijl and "NIVEAU" in stijl

    vol = rw.build_judge_user(_briefing(modus_reviewer="volledig"), doc)
    assert "bewust ALLEEN bijgewerkt" not in vol


def test_guidance_van_de_reviewer_gaat_achter_die_van_de_scorer():
    b = _briefing(rewrite_guidance="Leg de nadruk op governance.",
                  guidance_reviewer="Modules 3 en 4 samenvoegen.")
    assert b.guidance_definitief.index("governance") < b.guidance_definitief.index("samenvoegen")
    tekst = rw.build_writer_user(b)
    assert "Modules 3 en 4 samenvoegen." in tekst
    assert "AANWIJZING VAN DE REVIEWER" in tekst
    # zonder reviewer-aanwijzing verandert er niets aan de bestaande regel
    assert "AANWIJZING VAN DE REVIEWER" not in rw.build_writer_user(_briefing())


def test_actualisatie_tool_heeft_geen_verplichte_kopjes():
    """Het model levert alleen wat verandert; de rest blijft byte-voor-byte staan."""
    tool = rw.build_actualisatie_tool()
    assert tool["input_schema"]["required"] == []
    assert set(rw.ACTUALISEERBARE_KOPJES) <= set(tool["input_schema"]["properties"])
    # de veldbeschrijvingen komen uit SUBMIT_REWRITE, zodat ze niet uiteen lopen
    assert (tool["input_schema"]["properties"]["overzicht"]
            is rw.SUBMIT_REWRITE["input_schema"]["properties"]["overzicht"])


def test_render_veld_raakt_alleen_het_eigen_cms_veld():
    sleutel, waarde = uit.render_veld("doelgroep", "Deze training is voor analisten.")
    assert sleutel == "target_audience"
    assert waarde == "<p>Deze training is voor analisten.</p>"
    sleutel, waarde = uit.render_veld("overzicht", "Wil je iets?")
    assert sleutel == "summary" and waarde == "Wil je iets?"      # platte tekst, geen HTML


def test_overnemen_zonder_acties_doet_geen_enkele_call():
    """Het goedkope pad moet gratis blijven als er niets te actualiseren valt."""
    b = _briefing(titel="Cursus SQL", huidige_content=_content(), herschreven=True)
    res, content = rw.neem_over(b, client=None)
    assert res.status == rw.OVERGENOMEN
    assert res.modus == "overnemen"
    assert res.titel == "Training SQL"           # alleen de titelnormalisatie
    assert content["summary"] == _content()["summary"]
    assert res.toegepaste_acties == []


def test_overnemen_meldt_het_wanneer_een_actie_niet_kon_worden_doorgevoerd():
    """Stilzwijgend laten vallen is precies de fout die deze schaal moest repareren."""
    actie = bes.Besluit(1, "T", 1, "Voeg GA4 toe", "ja", "doen", "", "handmatig")
    b = _briefing(titel="Training SQL", huidige_content=_content(), herschreven=True,
                  goedgekeurd=[actie])
    res, _ = rw.neem_over(b, client=None)     # geen client -> niets doorgevoerd
    assert any("geen wijziging" in f for f in res.flags), res.flags
    assert res.toegepaste_acties == ["1. Voeg GA4 toe"]


def test_resultaat_legt_vast_onder_welk_regime_het_tot_stand_kwam():
    """Zonder modus en spec-versie is `approved` een status zonder betekenis."""
    b = _briefing(huidige_content=_content(), herschreven=True)
    res, content = rw.neem_over(b, client=None)
    rij = rw._review_rij(res, content)
    assert rij["modus"] == "overnemen"
    assert "modus_voorstel" in rij and "spec_versie" in rij
    assert len(rw.spec_versie()) == 12
    # de vingerafdruk verandert mee met de spec, niet met de code
    assert rw.spec_versie() == rw.spec_versie()


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
