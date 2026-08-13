"""
rewrite_trainings.py
====================
Herschrijft trainingen naar de nieuwe stijl (tien kopjes), op basis van de brontekst,
het score-oordeel en de besluiten van de reviewer. Hybride opzet, spiegelbeeld van
score_trainings.py:

  - Python assembleert de vaste sjabloon-secties uit `sjabloon.py` (Modules-openingszin,
    Aanpak-alinea's, bedrijfstrainingblok, Voorkennis-fallback, Vervolgstappen-boilerplate,
    Certificatie) en de catalogus-titels.
  - De LLM schrijft ALLEEN de generatieve secties via het tool `submit_rewrite`.
  - Een deterministische code-check (rewrite_checks.py) bewaakt lengte/format/placeholders.
  - Een judge-LLM oordeelt inhoudelijk (feitgetrouwheid, persona, per-sectie) en routeert.
  - `rewrite_output.py` zet het resultaat om naar de CMS-`content`-JSON.

De briefing krijgt alleen wat de reviewer heeft goedgekeurd (via besluiten.xlsx), plus een
expliciete NIET DOEN-lijst. `actualiteit_specifiek` en `actualiteit_samenvatting` blijven er
bewust buiten: dat is onderbouwing van de scorer, geen besluit.

Ontwerpprincipe (zelfde DNA als de scorer): "LLM schrijft/oordeelt, Python assembleert
en beslist". Gecachete spec-prefix, gestructureerde tool-output, append/skip/resume-harness.

Gebruik:
    python rewrite_trainings.py --scored scoresheet.xlsx --source bronsheet.xlsx \
        --besluiten besluiten.xlsx --out-dir herschreven --limit 5

De Vervolgstappen komen uit `vervolgtraining.json` (779 trainingen, ~89k tokens). Die
catalogus gaat nooit naar de API: Python maakt een shortlist van ~30 kandidaten en één
goedkope call kiest en groepeert daaruit. De code-check bewaakt dat elke titel echt bestaat.

De shortlist combineert twee signalen: IDF-keywordoverlap en het vakgebied uit
`vervolgtrainingen_tree.json` (domein > subdomein > onderwerp). Los van elkaar falen ze
allebei -- keywords bieden XSL "Interieurdesign met Vectorworks" aan, de boom hangt LDAP
onder Netwerken en mist Active Directory -- samen dekken ze elkaars gaten af. Het vakgebied
gaat als label mee naar het model, dat de twee groepen langs die grenzen legt.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Iterable

# Deze herschrijf-module hergebruikt de content-ingestie van de scorer
# (parse_content / build_source_text / extract_days / make_client / read_input) zodat
# schrijver en scorer EXACT dezelfde brontekst zien. Ook `orden_kolommen` komt daar vandaan:
# de kolomvolgorde van het gedeelde reviewsheet hoort bij de partij die dat sheet schrijft, en
# beide projecten schrijven erin. score_trainings.py leeft in het
# scoring-project (een aparte map). Standaard zoeken we het als zustermap onder
# .../Eduvision/; override met de omgevingsvariabele SCORE_TRAININGEN_DIR.
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_SCORE_DIR = os.path.normpath(
    os.path.join(_HERE, "..", "Trainingen Scoren", "TrainingenScorenEdu"))
_SCORE_DIR = os.environ.get("SCORE_TRAININGEN_DIR", _DEFAULT_SCORE_DIR)
if _SCORE_DIR and _SCORE_DIR not in sys.path:
    sys.path.insert(0, _SCORE_DIR)
try:
    from score_trainings import (
        parse_content, build_source_text, extract_days, make_client as _kale_client,
        orden_kolommen, read_input as read_source_input,
    )
except ModuleNotFoundError as e:  # pragma: no cover
    raise ModuleNotFoundError(
        "Kon score_trainings.py niet vinden. Zet de omgevingsvariabele "
        f"SCORE_TRAININGEN_DIR naar de map met score_trainings.py (geprobeerd: {_SCORE_DIR})."
    ) from e

import besluiten as bes
import drive_upload
import rewrite_checks as checks
import rewrite_output as uit
import sjabloon

# ---------------------------------------------------------------------------
# 1. CONFIG (tune-knoppen bovenaan, net als de scorer)
# ---------------------------------------------------------------------------

MODEL = "claude-opus-4-8"          # generatie profiteert van Opus; makkelijk te wisselen
KLEIN_MODEL = "claude-haiku-4-5"   # keuze uit een shortlist; geen generatie
MAX_TOKENS = 16000
THINKING = {"type": "adaptive"}    # adaptieve thinking voor schrijf-/oordeelskwaliteit

# LET OP: dit telt SCHRIJVERSpogingen, niet judge-revisies. Een onvolledige `submit_rewrite`
# en een HARD-check verbruiken er ook een, zónder dat de judge eraan te pas komt -- een
# training die één keer een harde check laat vallen houdt er dus minder judge-rondes over dan
# dit getal suggereert. Stond op 2; over batch 1 bleven 5 van de 46 hangen op "needs-revision
# na max revisies" (87, 129, 279, 283, 300), en bij vier daarvan stond er nog één of twee
# concrete, lokale correcties open (één woord, één zin, twee modules samenvoegen). De vijfde
# (279) was geen revisieprobleem maar de titelbug in `render_markdown`. Of 3 het juiste getal
# is leest de volgende batch af aan `rondes` in `<id>.json`: dezelfde klacht drie keer betekent
# dat een ronde erbij helpt, elke ronde een andere betekent dat hij niet convergeert.
MAX_REVISIONS = 3
N_SHORTLIST = 30                   # kandidaten die Python uit de catalogus voorselecteert
N_VERVOLG = 6                      # vervolgtrainingen die uiteindelijk in de tekst komen
N_VERVOLG_MIN = 3                  # daaronder is een lijst met twee groep-intro's niet zinnig

# Grenzen aan de tijd, en het onderscheid tussen deze twee is het hele punt.
#
# De SDK-defaults (600 s, 2 retries) lezen als een limiet per call maar zijn dat niet: bij
# `messages.stream` geldt de timeout per stukje dat over de lijn komt, en een ReadTimeout
# MIDDEN in een stream gaat buiten de retry-laag van de SDK om (die dekt alleen het openen van
# de request). Eén training doet tot 24 modelcalls -- MAX_REVISIONS+1 rondes maal schrijver
# plus judge, elk intern tot 3 keer -- dus zonder eigen grens is er geen bovengrens. Training
# 47 draaide 81 minuten voordat hij alsnog op een ReadTimeout sneuvelde.
#
# - `LEES_TIMEOUT` is een STILTE-limiet, geen duur. Drie minuten zonder één byte is een dode
#   verbinding en geen langzaam model: de thinking-blokken streamen mee, ook als hun tekst
#   leeg is. Ga niet lager zonder te meten.
# - `TIJDSBUDGET` is de vangrail en het enige echte plafond: gemeten over de hele training en
#   bewaakt bij élk stream-event, dus een call die eroverheen loopt breekt af binnen één event
#   in plaats van pas als het model klaar is. Verstrijkt hij, dan sneuvelt DEZE training
#   (`error`-rij, en `bouw_wachtrij` plant error-rijen bij de volgende run gewoon opnieuw in)
#   en loopt de batch door.
#
# 25 minuten is ~8x een typische training en een schatting, want tot deze ronde legden we de
# duur nergens vast. De kolom `seconden` in het review-tabblad is er om dat getal te vervangen
# door een meting.
LEES_TIMEOUT = 180.0               # seconden stilte binnen één stream
VERBIND_TIMEOUT = 10.0             # seconden voor de handshake
MAX_RETRIES = 4                    # 2 was de default, en training 5 sneuvelde erna op
                                   # `overloaded_error`; deze retries backoffen en falen snel
TIJDSBUDGET = 25 * 60              # seconden per training
# En dit is de herkansing die `MAX_RETRIES` NIET geeft: die zit op de SDK en dekt alleen het
# openen van de request, terwijl deze fouten zich juist middenin een stream aandienen. Training
# 369 (Data Warehouse Concept) sneuvelde na 381 s op een kale `httpx.ReadTimeout` -- normale
# duur (p50 is 301 s, p90 547 s) en nog 1119 s budget over, maar `_call_tool` had er niets
# tegenover te zetten en de hele training was weg. Eén herkansing kost hoogstens één call
# opnieuw; `_bewaak_tijd` staat ervoor, dus `TIJDSBUDGET` blijft het plafond.
#
# De naam zegt netwerk, maar het gaat om alles wat de SDK-retry per constructie mist, en dat is
# sinds training 2560 méér dan een dode lijn: een serverfout die als `error`-EVENT in een
# lopende stream binnenkomt draagt de status 200 van die stream, dus ook daar heeft `MAX_RETRIES`
# niets te retryen. `_mag_herkansen` is de scheidsrechter.
NETWERK_HERKANSINGEN = 1           # extra pogingen bij een fout die MIDDEN in een stream valt

# Afkoeling tussen twee trainingen, na een fout die naar een storing ruikt. Hier stond niets:
# `rewrite_file` begint de volgende training milliseconden na de vorige, dus een storing die
# minuten duurt neemt ze allemaal mee. Dat is de vorm die batch 4 liet zien -- 2410 (909,5 s)
# en 2412 (429,4 s) sneuvelden allebei op een ReadTimeout, en bij allebei viel óók de directe
# herkansing van `_stream_bericht` om. Die storingen leven dus aantoonbaar langer dan één
# volledige call, en dan is de volgende training de volgende die omvalt.
#
# 60 s om te beginnen: dat is 20% van een mediane training (301 s) en verwaarloosbaar tegen de
# 429 tot 1571 s die een mislukte training kost. Verdubbelen per opeenvolgende storing -- een
# afkoeling die niet hielp is het bewijs dat de storing langer duurt dan gedacht -- en terug op
# nul zodra er weer één training slaagt. Het plafond staat op 8 minuten: bij vier storingen op
# rij is de batch toch verloren, en dan is wachten goedkoper dan trainingen verbranden.
AFKOELING_START = 60.0             # na de eerste storing
AFKOELING_MAX = 480.0              # plafond na verdubbelen

# Taxonomie-bonus bij de shortlist. Keyword-overlap en boomburen falen op verschillende
# manieren -- LDAP vindt via keywords "Active Directory" (raak) maar via de boom 5G en
# breedband (mis), XSL andersom. Daarom een unie: de bonus telt bij de IDF-score op, hoog
# genoeg dat een vakgenoot zonder één gedeeld woord alsnog de lijst haalt.
#
# De IDF-score is per training niet vergelijkbaar (de hoogste treffer loopt van 15 tot 67),
# dus een vaste bonus zou bij de ene training alles omgooien en bij de andere niets doen.
# Daarom schalen we de keyword-score eerst naar 0..1 en drukken we de bonus uit in diezelfde
# eenheid: 0.60 betekent "telt zwaarder dan een keyword-treffer op 60% van de beste".
BONUS_SUBDOMEIN = 0.60             # zelfde subdomein: het naaste vakgebied
BONUS_DOMEIN = 0.20                # zelfde domein, ander subdomein: verbredend
BONUS_EXTRA_TAK = 0.15             # per extra tak die kandidaat en bron delen

# Plekken die de boom niet mag inpikken. Een groot subdomein levert zo veel vakgenoten dat
# ze de hele shortlist vullen: LDAP heeft er 16 en verdrong daarmee Active Directory, juist
# de beste vervolgstap (die onder Identity hangt, niet onder Netwerken). Deze plekken gaan
# op pure keyword-sterkte, zodat de unie ook echt een unie blijft.
N_KEYWORD_GARANTIE = 12

# specs + catalogus liggen naast dit script (resolven onafhankelijk van de CWD)
SCHRIJFSPEC = os.path.join(_HERE, "schrijfspec_herschrijven_v1.md")
HUMANISERING = os.path.join(_HERE, "humanisering_nl.md")
STIJLREGISTER = os.path.join(_HERE, "stijlregister_nl.md")
CORRECTIES = os.path.join(_HERE, "correcties_nl.md")
BEOORDELINGSSPEC = os.path.join(_HERE, "beoordelingsspec_herschrijven_v1.md")
TEMPLATE_PATH = os.path.join(_HERE, "Template trainingen nieuwe opbouw.md")
CATALOG_PATH = os.path.join(_HERE, "vervolgtraining.json")
TREE_PATH = os.path.join(_HERE, "vervolgtrainingen_tree.json")

# statussen voor routing
APPROVED = "approved"
NEEDS_REVISION = "needs-revision"
HUMAN_QUEUE = "human-queue"
OVERGENOMEN = "overgenomen"   # stond al in de nieuwe stijl; ongewijzigd doorgezet

# ---------------------------------------------------------------------------
# 1b. DE MATE VAN AANPASSING — twee assen
# ---------------------------------------------------------------------------
#
# AS 1: het herschrijfniveau. Hoeveel van de bestaande tekst mag veranderen? Elk niveau mag
# alles wat het niveau eronder mag; `volledig` is het gedrag van vóór deze schaal.
#
#   overnemen  niets, behalve titel en vervolgtitels          (was: herschreven=1)
#   stijl      + de formulering, naar de ACTUELE schrijfregels
#   format     + de structuur en de ontbrekende kopjes
#   volledig   + de opbouw, vanaf nul uit de brontekst
#
# AS 2: de actualiseringen. Die staan hier BEWUST buiten. Een goedgekeurde actie is een
# lokale toevoeging; een training daarom naar een hoger niveau tillen maakt de wijziging
# groter dan de reviewer vroeg. Goedgekeurde acties worden op elk niveau doorgevoerd,
# inclusief `overnemen` -- zie `neem_over`, dat daarvoor de besluiten moet kennen.
MODI = ("overnemen", "stijl", "format", "volledig")
MODUS_RANG = {m: i for i, m in enumerate(MODI)}
MODUS_DEFAULT = "volledig"   # onbekend of leeg -> veilig aan de vrije kant


def normaliseer_modus(waarde: Any, default: str = MODUS_DEFAULT) -> str:
    """Cel- of modelwaarde -> een geldige modus. Onbekende waarde valt terug op `default`."""
    tekst = _cel(waarde).lower()
    return tekst if tekst in MODUS_RANG else default


def hoogste_modus(*modi: str) -> str:
    """De meest ingrijpende van de gegeven modi; gebruikt om een ondergrens te leggen."""
    geldig = [m for m in modi if m in MODUS_RANG]
    return max(geldig, key=lambda m: MODUS_RANG[m]) if geldig else MODUS_DEFAULT


def normaliseer_modules_nb(waarde: Any) -> str:
    """Cel- of modelwaarde -> "stabiel" of "actueel". Alles onbekends wordt "stabiel".

    De default is bewust de terughoudende variant: een onterecht voorbehoud onder de modules
    doet meer kwaad dan een ontbrekend voorbehoud, want het suggereert dat wij zelf niet
    weten wat we geven. Een lege cel betekent dus "stabiel" en niet "nog niet beslist".
    """
    tekst = _cel(waarde).lower()
    return tekst if tekst in sjabloon.MODULES_NB_VARIANTEN else sjabloon.MODULES_NB_DEFAULT


def spec_versie() -> str:
    """Korte vingerafdruk van de vijf bestanden die samen "de regels" zijn.

    Staat per training in de output. Zodra de spec verandert is "welke goedgekeurde
    trainingen dateren van vóór de huidige regels" precies de vraag die bepaalt wie er een
    `stijl`-ronde in moet -- met deze hash is dat een filter op het reviewtabblad, zonder is
    het een gok op bestandsdatums.
    """
    import hashlib
    h = hashlib.sha256()
    for pad in (SCHRIJFSPEC, HUMANISERING, STIJLREGISTER, CORRECTIES, TEMPLATE_PATH):
        try:
            with open(pad, "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"<ontbreekt>")
        h.update(b"\0")
    return h.hexdigest()[:12]

# De vaste sjabloonteksten en de kopstructuur staan in sjabloon.py, afgeleid van
# `Template trainingen nieuwe opbouw.md`. Eén bron, zodat spec, schrijver, judge en
# CMS-output niet uit elkaar lopen.


# ---------------------------------------------------------------------------
# 3. CATALOGUS (Kopje 8) — laden + eenvoudige retrieval
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]{3,}")


def _tokens(*parts: str) -> set[str]:
    text = " ".join(p for p in parts if p).lower()
    return set(_TOKEN_RE.findall(text))


def load_catalog(path: str = CATALOG_PATH) -> list[dict]:
    """Catalogus -> lijst van {product_id, titel, omschrijving}.

    Het echte bestand is een dict gesleuteld op product-id-string:
        {"5": {"product_id": 5, "titel": "Opleiding PHP Professional", "summary": "..."}, ...}
    Een platte lijst wordt ook geaccepteerd. De titels gaan door `sjabloon.vervolgtitel`:
    geen "Cursus PowerPoint", maar ook geen "Training PowerPoint" -- in een lijst onder het
    kopje Vervolgstappen is dat voorvoegsel bij elke regel ruis. Een masterclass of workshop
    houdt zijn soortwoord wel. De brontitel blijft bewaard onder `bron_titel`.

    Ontbreekt het bestand, dan lege lijst (de code-check flagt dat).
    """
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        rijen = list(data.get("trainingen", data.values()))
    else:
        rijen = data
    return catalog_uit_rijen(rijen)


def catalog_uit_rijen(rijen: list[dict]) -> list[dict]:
    """Normaliseert ruwe catalogusrijen. Los van `load_catalog` zodat tests en notebook
    een catalogus kunnen samenstellen zonder bestand op schijf."""
    catalog = []
    for rij in rijen:
        if not isinstance(rij, dict):
            continue
        bron_titel = str(rij.get("titel") or rij.get("title") or "").strip()
        if not bron_titel:
            continue
        catalog.append({
            "product_id": rij.get("product_id", rij.get("id")),
            "titel": sjabloon.vervolgtitel(bron_titel),
            "bron_titel": bron_titel,
            "omschrijving": str(rij.get("omschrijving") or rij.get("summary") or "").strip(),
        })
    return catalog


def catalog_titles(catalog: list[dict]) -> set[str]:
    return {str(e.get("titel", "")).strip() for e in catalog if e.get("titel")}


def _sleutel(titel: str) -> str:
    """Genormaliseerde titel: kleine letters, zonder soortwoord."""
    return sjabloon.vervolgtitel(titel).strip().lower()


def _losse_sleutel(titel: str) -> str:
    """Als `_sleutel`, maar zonder spaties en interpunctie.

    Boom en catalogus schrijven dezelfde training soms nét anders: "Claude Co-Work" vs
    "Claude CoWork", "Timemanagement" vs "Time management", "IT-auditing" vs "IT auditing".
    Alles weglaten wat geen letter of cijfer is vangt die drie vormen in één regel.
    Botst wel: "C# Professional" en "C++ Professional" worden hier allebei "cprofessional",
    dus deze sleutel telt alleen als hij naar precies één catalogusrij wijst.
    """
    return re.sub(r"[^a-z0-9]+", "", _sleutel(titel))


def _boomblaadjes(node: dict, pad: tuple[str, ...] = ()) -> Any:
    """Loopt een tak af en levert (pad, bladnaam) per blad.

    Wordt op de kinderen van de wortel aangeroepen, zodat "Trainingscatalogus" niet in
    elk pad terugkomt: pad[0] is het domein, pad[1] het subdomein.
    """
    naam = str(node.get("name") or "")
    kinderen = node.get("children")
    if not kinderen:
        yield pad, naam
        return
    for kind in kinderen:
        if isinstance(kind, dict):
            yield from _boomblaadjes(kind, pad + (naam,))


def load_tree(catalog: list[dict], path: str = TREE_PATH) -> dict:
    """Taxonomieboom -> index voor de shortlist. Geen bestand = lege index (huidig gedrag).

    De boom deelt het aanbod in drie lagen in (domein > subdomein > onderwerp) en hangt een
    training desgewenst in meerdere takken. Bladnamen zijn brontitels ("Cursus Wordpress"),
    dus ze moeten door dezelfde normalisatie als de catalogus voordat ze matchen.

    Bladeren zonder catalogusrij vallen af. Dat is geen detail maar de veiligheidsgarantie
    van deze index: `check_vervolgstappen` rekent elke titel buiten de catalogus af als HARD
    `titel_onbekend`, dus wat hier niet resolvet mag nooit als kandidaat naar buiten.

    Levert {"paden": {titelsleutel: [pad, ...]}}: per training de takken waarin hij hangt.
    """
    if not catalog or not os.path.exists(path):
        return {"paden": {}}
    with open(path, encoding="utf-8") as f:
        boom = json.load(f)

    exact = {_sleutel(e["bron_titel"]) for e in catalog}
    # Op de losse sleutel tellen we distinct titels, niet rijen: twee catalogusrijen die op
    # dezelfde titel normaliseren zijn geen ambiguïteit, twee verschillende titels wel.
    los: dict[str, set[str]] = {}
    for sleutel in exact:
        los.setdefault(_losse_sleutel(sleutel), set()).add(sleutel)

    takken = boom.get("children") if isinstance(boom, dict) else boom
    paden: dict[str, list[tuple[str, ...]]] = {}
    for tak in takken or []:
        if not isinstance(tak, dict):
            continue
        for pad, naam in _boomblaadjes(tak):
            if not pad:
                continue
            sleutel = _sleutel(naam)
            if sleutel not in exact:
                kandidaten = los.get(_losse_sleutel(naam), set())
                if len(kandidaten) != 1:   # onbekend, of ambigu (C# vs C++): overslaan
                    continue
                sleutel = next(iter(kandidaten))
            if pad not in paden.setdefault(sleutel, []):
                paden[sleutel].append(pad)
    return {"paden": paden}


def _tak_bonus(boom: dict, bron_sleutel: str, kandidaat_sleutel: str) -> float:
    """Hoe dicht staat de kandidaat bij de bron in de boom?

    Zelfde subdomein weegt zwaarder dan zelfde domein, en delen ze meerdere takken, dan
    telt dat mee -- een training die in drie takken naast de bron hangt is zelden toeval.
    """
    bron_paden = boom["paden"].get(bron_sleutel) or []
    kand_paden = boom["paden"].get(kandidaat_sleutel) or []
    if not bron_paden or not kand_paden:
        return 0.0
    beste, gedeeld = 0.0, 0
    for bp in bron_paden:
        for kp in kand_paden:
            if len(bp) >= 2 and len(kp) >= 2 and bp[:2] == kp[:2]:
                beste = max(beste, BONUS_SUBDOMEIN)
                gedeeld += 1
            elif bp[:1] == kp[:1]:
                beste = max(beste, BONUS_DOMEIN)
                gedeeld += 1
    return beste + BONUS_EXTRA_TAK * max(gedeeld - 1, 0) if beste else 0.0


def taxonomie_pad(boom: dict, titel_sleutel: str, bron_sleutel: str = "") -> str:
    """Het pad als label voor de prompt, bv. "ERP & CRM > CRM & Marketing Platforms".

    Hangt de kandidaat in meerdere takken, dan wint de tak die de bron deelt: dat is het
    vakgebied waarlangs het model straks moet groeperen.
    """
    paden = boom["paden"].get(titel_sleutel) or []
    if not paden:
        return ""
    bron_paden = boom["paden"].get(bron_sleutel) or []
    def rang(pad: tuple[str, ...]) -> tuple[int, int]:
        for bp in bron_paden:
            if len(bp) >= 2 and len(pad) >= 2 and bp[:2] == pad[:2]:
                return (0, len(pad))
        for bp in bron_paden:
            if bp[:1] == pad[:1]:
                return (1, len(pad))
        return (2, len(pad))
    return " > ".join(min(paden, key=rang)[:2])


def _idf(catalog: list[dict]) -> dict[str, float]:
    """Inverse document frequency over de catalogus.

    Zonder dit domineren woorden die overal staan ("training", "je", "data") de overlap.
    """
    import math
    doc_freq: dict[str, int] = {}
    for entry in catalog:
        for token in _tokens(entry["titel"], entry["omschrijving"]):
            doc_freq[token] = doc_freq.get(token, 0) + 1
    n = max(len(catalog), 1)
    return {t: math.log(n / (1 + f)) for t, f in doc_freq.items()}


def shortlist_vervolgtrainingen(catalog: list[dict], titel: str, kern: str,
                                training_id: Any = None,
                                n: int = N_SHORTLIST,
                                boom: dict | None = None) -> list[dict]:
    """Trap 1: IDF-gewogen keyword-overlap, verrijkt met de taxonomieboom. Nul API-kosten.

    Keyword-overlap alleen is puur lexicaal en gaat de mist in zodra een training zijn
    vakgenoten geen woord deelt: XSL kreeg zo "Interieurdesign met Vectorworks" aangeboden.
    De boom vult dat aan met echte vakgenoten. Andersom vindt de boom niet alles -- LDAP
    hangt onder Netwerken, terwijl de beste vervolgstap (Active Directory) onder Identity
    staat. Vandaar de unie: beide leveren kandidaten, de score bepaalt de volgorde.

    Sluit de training zelf uit op `product_id` én op titel -- elke gescoorde training staat
    ook in de catalogus, dus zonder die filter beveelt een training zichzelf aan.
    """
    if not catalog:
        return []
    idf = _idf(catalog)
    want = _tokens(titel, kern)
    eigen = str(training_id) if training_id is not None else None
    eigen_sleutel = _sleutel(titel)

    ruw = []
    for entry in catalog:
        if eigen is not None and str(entry.get("product_id")) == eigen:
            continue
        sleutel = _sleutel(entry["bron_titel"])
        if sleutel == eigen_sleutel:
            continue
        overlap = want & _tokens(entry["titel"], entry["omschrijving"])
        score = sum(idf.get(t, 0.0) for t in overlap)
        bonus = _tak_bonus(boom, eigen_sleutel, sleutel) if boom else 0.0
        if score > 0 or bonus > 0:
            ruw.append((score, bonus, entry))

    # Keyword-score naar 0..1 zodat de bonus in dezelfde eenheid meetelt; zonder boom is
    # dat een monotone transformatie en blijft de volgorde exact zoals hij was.
    hoogste = max((s for s, _, _ in ruw), default=0.0) or 1.0
    scored = [(s / hoogste + b, s, e) for s, b, e in ruw]
    scored.sort(key=lambda t: -t[0])
    if not boom:
        return [e for _, _, e in scored[:n]]

    # Eerst de sterkste keyword-treffers vastzetten, dan de rest op de gemengde score.
    # Andersom zou een volle tak de shortlist monopoliseren.
    vast = sorted(scored, key=lambda t: -t[1])[:N_KEYWORD_GARANTIE]
    gekozen_ids = {id(e) for _, _, e in vast}
    keuze = list(vast)
    for treffer in scored:
        if len(keuze) >= n:
            break
        if id(treffer[2]) not in gekozen_ids:
            keuze.append(treffer)
            gekozen_ids.add(id(treffer[2]))
    keuze.sort(key=lambda t: -t[0])
    return [e for _, _, e in keuze[:n]]


SUBMIT_VERVOLGSTAPPEN = {
    "name": "submit_vervolgstappen",
    "description": "Kies uit de aangeboden kandidaten de vervolgtrainingen die logisch "
                   "aansluiten, en verdeel ze over één of twee groepen met elk een korte "
                   "inleidende zin. Kies ALLEEN uit de aangeboden titels.",
    "input_schema": {
        "type": "object",
        "properties": {
            "groepen": {
                "type": "array",
                "description": "Eén of twee groepen; samen 3-6 titels, en elke groep minstens "
                               "twee titels.",
                "items": {
                    "type": "object",
                    "properties": {
                        "intro": {"type": "string",
                            "description": "Eén zin die de groep aankondigt en eindigt op een "
                                           "dubbele punt, bv. 'Wil je je verder verdiepen in "
                                           "datamodellering, dan sluiten deze trainingen aan:'."},
                        "titels": {"type": "array", "items": {"type": "string"},
                            "description": "Letterlijke titels uit de aangeboden kandidatenlijst."},
                    },
                    "required": ["intro", "titels"],
                },
            },
        },
        "required": ["groepen"],
    },
}

KIES_VERVOLG_SYSTEM = """\
Je kiest vervolgtrainingen. Je krijgt een training (titel, kern, doelgroep-niveau) en een
lijst kandidaat-trainingen uit de catalogus, met hun omschrijving.

Kies er 3 tot 6 die een deelnemer ná deze training logisch zou volgen: verdiepend op
hetzelfde onderwerp, of verbredend naar een aangrenzend onderwerp. Laat kandidaten liggen
die alleen een woord delen maar inhoudelijk niets met de training te maken hebben; liever
drie rake dan zes vage.

Verdeel ze over één of twee groepen met elk een korte inleidende zin in de 'je'-vorm, die
eindigt op een dubbele punt. Twee groepen alleen als er echt twee richtingen zijn
(bijvoorbeeld verdiepen versus verbreden). Elke groep bevat minstens TWEE titels: een
introzin die één training aankondigt leest als een fout. Past er maar één training in een
tweede richting, maak er dan één groep van.

Gebruik geen liggend streepje (em-dash of en-dash) in de introzin; een komma of een punt doet
het werk.

Achter een kandidaat staat tussen blokhaken zijn vakgebied, bijvoorbeeld
[ERP & CRM > CRM & Marketing Platforms]. Gebruik dat om de groepen langs échte
vakgrenzen te leggen: kandidaten uit hetzelfde vakgebied als de training verdiepen,
kandidaten uit een ander vakgebied verbreden. Het is een hulpmiddel, geen verplichting --
past alles in één richting, maak dan één groep. Noem het vakgebied nooit in de intro en
neem de blokhaken nooit in een titel over.

Neem titels LETTERLIJK over uit de kandidatenlijst, zonder het deel tussen blokhaken.
Verzin er nooit een bij en pas ze niet aan. Roep tot slot het tool
`submit_vervolgstappen` aan.
"""


def kies_vervolgtrainingen(client, titel: str, kern: str, persona: str,
                           shortlist: list[dict], boom: dict | None = None,
                           oude_titel: str = "") -> list[dict]:
    """Trap 2: één goedkope call kiest en groepeert uit de shortlist.

    De catalogus zelf gaat nooit naar de API -- alleen deze kandidaten. Staat er een boom
    bij, dan krijgt elke kandidaat zijn vakgebied als label mee, zodat het model de twee
    groepen langs echte vakgrenzen legt in plaats van op gevoel. Levert [{intro, titels}];
    bij twijfel een lege lijst, dan valt de code terug op de shortlist.
    """
    if not shortlist or client is None:
        return []
    toegestaan = {e["titel"] for e in shortlist}
    bron_sleutel = _sleutel(oude_titel or titel)
    regels = []
    for e in shortlist:
        label = taxonomie_pad(boom, _sleutel(e["bron_titel"]), bron_sleutel) if boom else ""
        kop = f"{e['titel']} [{label}]" if label else e["titel"]
        regels.append(f"- {kop}: {(e['omschrijving'] or '(geen omschrijving)')[:220]}")
    kandidaten = "\n".join(regels)
    user_text = (f"Training: {titel}\nPersona: {persona}\nKern: {kern}\n\n"
                 f"Kandidaten:\n{kandidaten}")
    out = _call_tool(client, KIES_VERVOLG_SYSTEM, user_text, [SUBMIT_VERVOLGSTAPPEN],
                     "submit_vervolgstappen", max_tokens=2000, model=KLEIN_MODEL,
                     thinking=None)
    if not isinstance(out, dict):
        return []

    # Het model mag alleen kiezen, niet verzinnen: alles buiten de shortlist valt af.
    # Neemt het het vakgebied-label toch mee, dan strippen we dat eerst; anders sneuvelt
    # een verder correcte keuze stilletjes. Eerst de titel zoals gegeven, dan pas gestript,
    # zodat een catalogustitel die zelf op blokhaken eindigt niet wordt afgeknipt.
    groepen, gezien = [], set()
    for groep in out.get("groepen") or []:
        if not isinstance(groep, dict):
            continue
        titels = []
        for t in groep.get("titels") or []:
            if not isinstance(t, str):
                continue
            gekozen = t.strip()
            if gekozen not in toegestaan:
                gekozen = re.sub(r"\s*\[[^\]]*\]$", "", gekozen).strip()
            if gekozen in toegestaan and gekozen not in gezien:
                titels.append(gekozen)
                gezien.add(gekozen)
        if titels:
            groepen.append({"intro": _schoon_intro(str(groep.get("intro") or "").strip()),
                            "titels": titels[:N_VERVOLG]})
    return snoei_groepen(groepen[:2])


# Liggende streepjes horen ook hier niet, maar de intro's komen niet van de schrijver: een
# hard fail zou de schrijver terugsturen voor tekst die hij niet geschreven heeft. Eén zin
# met een bijstelling erin verdraagt een komma prima, dus dit repareert de code zelf.
_INTRO_DASH_RE = re.compile("\\s*[\\u2014\\u2013]\\s*")


def _schoon_intro(intro: str) -> str:
    return _INTRO_DASH_RE.sub(", ", intro)


def snoei_groepen(groepen: list[dict]) -> list[dict]:
    """Haalt groepen weg die te weinig titels aankondigen.

    Een introzin die één training aankondigt leest als een fout: hij belooft een richting en
    levert één bullet (reviewronde 4, training 2347). De titel gaat mee weg en niet over naar
    de andere groep -- die groep heeft zijn eigen intro, en een training die daar inhoudelijk
    niet onder valt maakt de intro onwaar. Liever één titel minder dan een intro die niet
    klopt.

    Blijven er te weinig titels over voor een gegroepeerde lijst, dan vervallen de groepen
    helemaal: `render_vervolgstappen` valt dan terug op één lijst onder de vaste
    aankondiging, wat nog steeds een goede Vervolgstappen oplevert.
    """
    gesnoeid = uit.bruikbare_groepen(groepen)
    if sum(len(g["titels"]) for g in gesnoeid) < N_VERVOLG_MIN:
        return []
    return gesnoeid


# ---------------------------------------------------------------------------
# 4. INFO-PASSING: scorer-rij + brontekst -> RewriteBriefing
# ---------------------------------------------------------------------------

def _cel(val: Any) -> str:
    """Excel-cel -> tekst, met een lege cel als lege string.

    `str(val or "")` volstaat hier niet: pandas levert een lege cel als `float('nan')`, en dat
    is truthy -- je houdt dan de tekst "nan" over. Voor `kern_reviewer` is dat het verschil
    tussen "geen reviewer heeft hiernaar gekeken" en "een mens heeft dit vastgesteld".
    """
    if val is None or (isinstance(val, float) and val != val):
        return ""
    tekst = str(val).strip()
    return "" if tekst.lower() == "nan" else tekst


def _split_pipe(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if val is None or (isinstance(val, float)):
        return []
    return [p.strip() for p in str(val).split(" | ") if p.strip()]


def bepaal_dagen(source_content: dict, scored_dagen: Any = None) -> int | None:
    """Aantal dagen: de bron-JSON is gezaghebbend, daarna pas de schatting van de scorer.

    `extract_days` in het scoringsproject zocht tot augustus 2026 alleen op de sleutel "dagen",
    terwijl hij in de bron "days" heet; daardoor viel dit altijd terug op de scorer-schatting.
    Dat is daar inmiddels gerepareerd, maar deze functie blijft staan: hij houdt de herschrijver
    onafhankelijk van welke versie van `score_trainings.py` er in de zusterrepo ligt.
    """
    for kandidaat in (source_content.get("days"), source_content.get("dagen")):
        if kandidaat is None or kandidaat == "":
            continue
        try:
            return int(float(kandidaat))
        except (ValueError, TypeError):
            continue
    return extract_days(source_content, scored_dagen)


@dataclass
class RewriteBriefing:
    training_id: Any
    titel: str
    persona: str
    dagen: int | None
    kern: str
    verdict: str
    actualiteit_type: str
    source_text: str
    bruikbaar: list[str] = field(default_factory=list)
    strippen: list[str] = field(default_factory=list)
    gaten: list[str] = field(default_factory=list)
    goedgekeurd: list[bes.Besluit] = field(default_factory=list)
    afgewezen: list[bes.Besluit] = field(default_factory=list)
    # kolom `rewrite_guidance`: de vrije aanwijzing. De scorer vult hem, de reviewer stelt hem bij
    # in dezelfde cel -- dit is het enige scorer-veld dat letterlijk naar de schrijver gaat, dus
    # ook het enige dat de reviewer niet alleen naleest maar aanpast.
    rewrite_guidance: str = ""
    menselijke_input_nodig: bool = False
    kern_reviewer: str = ""
    modus_reviewer: str = ""      # kolom `modus_reviewer`: het besluit van een mens
    modus_voorstel: str = ""      # kolom `modus_voorstel`: uit scan_vorm + schat_modus
    # kolom `guidance_reviewer`: legacy. Tot augustus 2026 de aparte reviewerkolom, aangemaakt door
    # `modus_voorstellen`. Die stap komt ná de scoor-review, dus het reviewteam kwam er nooit bij;
    # sindsdien staat de vrije aanwijzing in `rewrite_guidance`. Blijft gelezen zodat bestaande
    # sheets (`scoresheet_met_modus.xlsx`, oudere batches) niets verliezen.
    guidance_reviewer: str = ""
    huidige_content: dict = field(default_factory=dict)   # de bestaande CMS-content
    herschreven: bool = False     # kolom `herschreven`: stond al in de nieuwe stijl
    modules_nb_reviewer: str = ""  # kolom `modules_nb_reviewer`: besluit van een mens
    modules_nb_voorstel: str = ""  # kolom `modules_nb_voorstel`: uit schat_modus

    @property
    def thin(self) -> bool:
        return self.verdict in ("dun", "redelijk")

    @property
    def modus(self) -> str:
        """De herschrijfmodus die geldt. Gezag volgt herkomst, net als bij de kern.

        Volgorde: het besluit van de reviewer, anders het voorstel uit de scan, anders de
        oude `herschreven`-kolom (die was de facto al een modus: 1 betekende "niet
        aanraken"), anders volledig herschrijven. Zo blijft een scoresheet zonder de nieuwe
        kolommen zich exact gedragen als voorheen.

        LET OP -- goedgekeurde actualiseringen verschuiven deze modus BEWUST niet. Een
        goedgekeurde actie is een lokale toevoeging; de training daarom integraal opnieuw
        laten schrijven maakt de wijziging groter dan de reviewer vroeg. Actualiseringen
        lopen op elk niveau mee, inclusief `overnemen` (zie `neem_over`).
        """
        val = normaliseer_modus(self.modus_reviewer, default="")
        if val not in MODUS_RANG:
            val = normaliseer_modus(self.modus_voorstel, default="")
        if val not in MODUS_RANG:
            val = "overnemen" if self.herschreven else MODUS_DEFAULT
        return val

    @property
    def modus_van_reviewer(self) -> bool:
        return normaliseer_modus(self.modus_reviewer, default="") in MODUS_RANG

    @property
    def modules_nb(self) -> str:
        """Welke NB onder het kopje Modules komt: "stabiel" (default) of "actueel".

        Zelfde gezagsvolgorde als de modus -- reviewer, dan voorstel, dan default -- maar
        een andere as: dit zegt niets over de kwaliteit van de tekst en alles over het
        onderwerp. Een lege cel is "stabiel" en niet "onbeslist"; een scoresheet zonder deze
        kolommen gedraagt zich dus als voorheen, met de terughoudende variant.
        """
        val = _cel(self.modules_nb_reviewer).lower()
        if val not in sjabloon.MODULES_NB_VARIANTEN:
            val = _cel(self.modules_nb_voorstel).lower()
        return normaliseer_modules_nb(val)

    @property
    def behoudt_tekst(self) -> bool:
        """Modi waarin de bestaande tekst het uitgangspunt is in plaats van de brontekst."""
        return self.modus in ("stijl", "format")

    @property
    def guidance_definitief(self) -> str:
        """De vrije aanwijzing die de schrijver krijgt.

        `rewrite_guidance` is één kolom voor scorer én reviewer: de scorer zegt waar het
        bronmateriaal het beste landt, de reviewer stelt dat bij in dezelfde cel. Er valt daar
        dus niets meer te labelen -- wat er staat is de aanwijzing.

        `guidance_reviewer` is de legacy-kolom uit de tijd dat dit twee velden waren. Staat hij
        gevuld (een sheet van voor augustus 2026), dan gaat hij nog steeds mee, achteraan en met
        het label erbij: de reviewer had daar het laatste woord over de scorer-tekst.
        """
        delen = []
        if self.rewrite_guidance.strip():
            delen.append(self.rewrite_guidance.strip())
        if self.guidance_reviewer.strip():
            delen.append(f"AANWIJZING VAN DE REVIEWER (gaat vóór het bovenstaande): "
                         f"{self.guidance_reviewer.strip()}")
        return "\n".join(delen)

    @property
    def kern_van_reviewer(self) -> bool:
        """Heeft een mens de kern zelf geschreven of bijgesteld?"""
        return bool(self.kern_reviewer.strip())

    @property
    def kern_definitief(self) -> str:
        """De kern die de schrijver krijgt: die van de reviewer als die er is.

        De kern is het enige veld dat het niveau van de training vastlegt (introducerend,
        toepassend, verdiepend), en dus het veld waar de reviewer op bijstuurt. De scorer-kern
        blijft ernaast staan, zodat een herscoring hem mag verversen zonder het oordeel van de
        mens te overschrijven -- zie `_behoud_kern_reviewer` in score_trainings.py.
        """
        return self.kern_reviewer.strip() or self.kern

    @property
    def nieuwe_titel(self) -> str:
        """De titel in de nieuwe stijl; niks heet nog een opleiding of cursus."""
        return sjabloon.nieuwe_titel(self.titel)

    @property
    def reviewer_besloten(self) -> bool:
        """Heeft de reviewer de actielijst daadwerkelijk ingevuld?

        `besluiten.build_besluiten` schrijft bij een lege `actie_besluit`-cel records weg
        met een lege `besluit_ruw`. Staat er ergens wél een annotatie, dan heeft een mens
        naar deze training gekeken en is de menselijke poort al gepasseerd.
        """
        return any(b.besluit_ruw.strip() for b in self.goedgekeurd + self.afgewezen)

    @property
    def route_out(self) -> str | None:
        """Harde routes die NIET de auto-herschrijving in gaan.

        Structurele actualiteitsbreuken en `menselijke_input_nodig` vragen een beslissing
        van een mens -- maar als de reviewer die al genomen heeft (besluiten ingevuld), is
        er niets meer om op te wachten en gaat de training gewoon mee.
        """
        if self.verdict == "onbruikbaar":
            return "verdict onbruikbaar — te weinig bron"
        if self.reviewer_besloten:
            return None
        if self.actualiteit_type == "structureel":
            return "structurele actualiteitsbreuk — nog geen reviewer-besluit"
        if self.menselijke_input_nodig:
            return "menselijke_input_nodig — nog geen reviewer-besluit"
        return None


def build_briefing(scored: dict, source_content: dict, naam: str,
                   besluiten: list[bes.Besluit] | None = None) -> RewriteBriefing:
    """Scorer-rij + brontekst + reviewer-besluiten -> alles wat de schrijver krijgt.

    LET OP — `actualiteit_specifiek` en `actualiteit_samenvatting` gaan hier BEWUST niet in.
    Dat zijn de onderbouwing van de scorer, geen besluit. Alleen wat in `actie_besluit` is
    goedgekeurd (via besluiten.xlsx) mag worden doorgevoerd; zouden die velden meegaan, dan
    kan het model alsnog een afgewezen actualisering oppikken.
    """
    goedgekeurd, afgewezen = bes.splits(besluiten or [])
    return RewriteBriefing(
        training_id=scored.get("training_id"),
        titel=naam,
        persona=str(scored.get("vermoedelijk_persona", "") or "").strip() or "B",
        dagen=bepaal_dagen(source_content, scored.get("aantal_dagen_bron")),
        kern=str(scored.get("kern", "") or ""),
        verdict=str(scored.get("verdict", "") or ""),
        actualiteit_type=str(scored.get("actualiteit_type", "") or "none"),
        source_text=build_source_text(source_content, naam),
        bruikbaar=_split_pipe(scored.get("bruikbaar")),
        strippen=_split_pipe(scored.get("strippen")),
        gaten=_split_pipe(scored.get("gaten")),
        goedgekeurd=goedgekeurd,
        afgewezen=afgewezen,
        rewrite_guidance=str(scored.get("rewrite_guidance", "") or ""),
        menselijke_input_nodig=bool(scored.get("menselijke_input_nodig")),
        kern_reviewer=_cel(scored.get("kern_reviewer")),
        modus_reviewer=_cel(scored.get("modus_reviewer")),
        modus_voorstel=_cel(scored.get("modus_voorstel")),
        modules_nb_reviewer=_cel(scored.get("modules_nb_reviewer")),
        modules_nb_voorstel=_cel(scored.get("modules_nb_voorstel")),
        guidance_reviewer=_cel(scored.get("guidance_reviewer")),
        huidige_content=dict(source_content or {}),
        herschreven=str(scored.get("herschreven", "")).strip() in ("1", "1.0", "True", "true"),
    )


# ---------------------------------------------------------------------------
# 5. HET SCHRIJF-TOOL (dwingt de generatieve secties af)
# ---------------------------------------------------------------------------

SUBMIT_REWRITE = {
    "name": "submit_rewrite",
    "description": "Lever de herschreven, generatieve kopjes. De code voegt de vaste "
                   "sjabloonteksten (Modules-openingszin, Aanpak-alinea's, het "
                   "bedrijfstrainingblok, Vervolgstappen, Certificatie en de "
                   "catalogus-titels) zelf in. Schrijf in 'je'-vorm, geen marketingtaal.",
    "input_schema": {
        "type": "object",
        "properties": {
            "overzicht": {"type": "string",
                "description": "Kopje Overzicht. Één alinea, richtlijn 55-80 woorden, begint met "
                               "'Wil je …'. Geen bullets. Lengte is hier geen doel op zich: liever "
                               "wat langer en een complete introductie in de materie, dan kort door "
                               "de bocht. Blijft een wezenlijk onderwerp van de training buiten "
                               "beeld, dan is het te kort. De TWEEDE zin (die de openingsvraag "
                               "beantwoordt) begint met 'In deze training leer je …', of met "
                               "'Tijdens deze training …' waar 'leer je' niet past. Nooit een kaal "
                               "'Je leert …' of 'Je werkt met …': dan staat de zin los van de vraag "
                               "erboven. Twee dingen die verder het vaakst misgaan: "
                               "(1) de openingsvraag dekt maar één deelaspect in plaats van het "
                               "zwaartepunt van de training; (2) de werkwoorden staan aan de "
                               "onderkant: 'begrippen kunnen plaatsen', 'gerichter meepraten', "
                               "'ervaren hoe X in elkaar zit'. Kies binnen dezelfde scope het "
                               "sterkste ware werkwoord: 'de opbouw van X doorgronden', 'een stevige "
                               "basis leggen in X'. De slotzin staat in de in-staat-vorm ('Hierdoor "
                               "ben je in staat om …'), niet in een kaal 'Hierdoor kun je …'."},
            "inleiding": {"type": "string",
                "description": "Kopje Inleiding. Richtlijn 180-210 woorden (bij een training van "
                               "4 dagen of meer mag het richting 230), verdiepend op Overzicht. "
                               "Ook hier telt de formulering zwaarder dan het exacte aantal. "
                               "De zin die de openingsvraag beantwoordt noemt de training ('Tijdens "
                               "deze training …'), zonder de duur. Maak 'je' het onderwerp: niet "
                               "'welke techniek bij welk vraagstuk hoort' maar 'met welke technieken "
                               "je die data omzet in bruikbare informatie'. Slotzin in de "
                               "in-staat-vorm, net als bij Overzicht. "
                               "Schrijf NIET het bedrijfstrainingblok; dat plaatst de code."},
            "modules": {
                "type": "object",
                "description": "Kopje Modules. Kies 4 modules bij 1 dag, 5 bij 2-3 dagen en 6 bij "
                               "4 dagen of meer. Wijk daarvan af als de stof dat vraagt, maar ga "
                               "nooit boven 6 (8 bij 4 dagen of meer) of onder 4 (5 bij 4 dagen "
                               "of meer). Een programma van zes of zeven modules met overal vijf "
                               "sub-bullets leest als een inhoudsopgave, niet als een training: "
                               "voeg liever twee verwante onderwerpen samen dan dat je ze uit "
                               "elkaar trekt. Per module 3-6 sub-bullets, aantal moet variëren.",
                "properties": {
                    "modules": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "titel": {"type": "string"},
                                "bullets": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["titel", "bullets"],
                        },
                    },
                },
                "required": ["modules"],
            },
            "doelgroep": {"type": "string",
                "description": "Kopje Doelgroep. Één zin, begint met 'Deze training is bedoeld voor "
                               "…'. Geen functietitels/'professionals'. Gericht op wat iemand wil "
                               "bereiken, niet op wie iemand is; dat scheelt ook dubbeling met "
                               "Voorkennis, dat er pal onder staat."},
            "voorkennis": {"type": "string",
                "description": "Kopje Voorkennis. Compact: één zin waar dat kan, twee als er een "
                               "voorbehoud of een contactzin bij hoort. Die contactzin luidt 'neem "
                               "DAN gerust contact met ons op'. Herhaal niet wat de Doelgroep al "
                               "zegt: staat daar 'iedereen die al in JavaScript ontwikkelt', dan "
                               "voegt 'ervaring met JavaScript is vereist' niets toe. Noem hier de "
                               "concrete voorwaarde. Laat leeg als geen voorkennis nodig is (code "
                               "plaatst de fallbackzin)."},
            "aanpak_invulling": {"type": "string",
                "description": "Kopje Aanpak. Alleen de [.....]-invulling: één woord of enkele "
                               "woorden. De code plakt jouw tekst achter de vaste zin '... maak "
                               "je je de materie stap voor stap eigen en ervaar je hoe ' en zet "
                               "er zelf een punt achter. Begin dus NIET met 'hoe', 'dat' of "
                               "'wat', en schrijf geen hele zin. Goed: 'je datamodellen opzet en "
                               "beoordeelt'. Fout: 'hoe je datamodellen opzet'."},
            "doelen": {"type": "array", "items": {"type": "string"},
                "description": "Kopje Doelen. 4-5 doelen in de infinitief MET 'te', aansluitend op de "
                               "vaste introzin 'Na deze training ben je in staat om:', dus "
                               "'Dashboards te bouwen die de juiste vraag beantwoorden', niet "
                               "'Dashboards bouwen'. Herhaal 'in staat' niet; dat staat al in de "
                               "introzin. Hoofdletter aan het begin, zonder de introzin; begint een "
                               "doel met een term die zijn eigen schrijfwijze heeft ('2D-tekeningen "
                               "te maken', 'iOS-apps te bouwen'), houd die dan aan en verdraai de "
                               "term niet om aan de hoofdletter te komen. Een "
                               "vergrotende trap ('scherper', 'gerichter') mag de belofte op maat "
                               "houden, maar vervangt geen sterk werkwoord: 'de opbouw van X "
                               "scherper te doorgronden', niet 'gerichter mee te praten over X'."},
            "kortste_omschrijving": {"type": "string",
                "description": "Kopje Kortste omschrijving. Maximaal 200 tekens, als enige lengte "
                               "een harde grens (Edudex kapt langere tekst af). Begint met "
                               "'Wil je …'. De zin daarna begint met 'Na deze training …' ('Na deze "
                               "training weet je hoe je …'); dit fragment staat vaak los van de rest "
                               "van de pagina, dus het moment waarop de opbrengst er is hoort in de "
                               "zin zelf. Past dat niet binnen de 200 tekens, dan gaat de grens "
                               "voor. Ingedikte versie van Overzicht."},
            "nieuwe_titel": {"type": "string",
                "description": "Optioneel. De code maakt zelf al een titel in de nieuwe stijl "
                               "('Cursus XML' -> 'Training XML'). Lever hier alleen iets als dat "
                               "mechanische resultaat krom loopt. Nooit 'cursus' of 'opleiding'."},
            "notities": {"type": "string",
                "description": "Optioneel: signaleer 'thin' (dunne bron, veel geconstrueerd) of een "
                               "structurele twijfel. Meld hier ook wanneer de kern en de brontekst "
                               "elkaar tegenspreken over wat de training doet of op welk niveau. "
                               "Begin die melding met 'kern-conflict:' en zeg wat elk van beide zegt."},
        },
        "required": ["overzicht", "inleiding", "modules", "doelgroep",
                     "aanpak_invulling", "doelen", "kortste_omschrijving"],
    },
}

SUBMIT_JUDGMENT = {
    "name": "submit_judgment",
    "description": "Lever het inhoudelijke oordeel over het concept. De code-check op lengte/"
                   "format is al gedaan; oordeel over feitgetrouwheid, persona/toon en per sectie.",
    "input_schema": {
        "type": "object",
        "properties": {
            "feitgetrouw": {
                "type": "object",
                "properties": {
                    "pass": {"type": "boolean"},
                    "problemen": {"type": "array", "items": {"type": "string"}},
                    "thin": {"type": "boolean"},
                },
                "required": ["pass", "problemen", "thin"],
            },
            "persona_toon": {
                "type": "object",
                "properties": {"pass": {"type": "boolean"}, "reden": {"type": "string"}},
                "required": ["pass", "reden"],
            },
            "verdict": {"type": "string", "enum": [APPROVED, NEEDS_REVISION, HUMAN_QUEUE]},
            "revisie_notities": {"type": "array", "items": {"type": "string"},
                "description": "Bij needs-revision: per kopje één concrete, atomaire instructie."},
            "human_reden": {"type": "string"},
            "judge_confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["feitgetrouw", "persona_toon", "verdict"],
    },
}


# ---------------------------------------------------------------------------
# 6. PROMPTS (gecachete spec-prefix + korte werkinstructie)
# ---------------------------------------------------------------------------

def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


GOUD_DIR = os.path.join(_HERE, "herschreven", "goud")
GOUD_V2_DIR = os.path.join(_HERE, "herschreven", "goud_v2")

# ---------------------------------------------------------------------------
# FEW-SHOT
# ---------------------------------------------------------------------------
#
# Het oude corpus van 78 kan geen few-shot meer zijn. Het dateert van vóór Templatev2 en
# vóór de eerste stijlronde: 77 van de 78 openen met "Deze training is voor" in plaats van
# "is bedoeld voor", geen enkele demonstreert het lerende aspect uit Sectie 0.15, en er haalt
# er nu nul élke harde check (`checks_over_goud()`). Als voorbeeld zou het precies de vormen
# tonen die de spec inmiddels verbiedt.
#
# Wat er nu staat komt uit `herschreven/goud_v2/selectie.json`, en dat manifest wordt
# geschreven door `promoveer_naar_goud()`: die functie draait de checks over de eigen
# herschreven output en kopieert wat slaagt naar de goudmap. Zo is de few-shot altijd
# materiaal dat mét de huidige spec is gegenereerd, en niet een lijst die je met de hand
# moet bijhouden.
#
# De fallback hieronder is het handwerk uit reviewronde 3 (`bouw_goud_v2.py`): vier
# gerepareerde trainingen. `herschreven/` staat in .gitignore, dus na een verse checkout is
# er geen manifest en is dit wat er overblijft.
#
# Vaste selectie, geen wisselende: de hele system-prefix gaat als één blok met
# `cache_control: ephemeral` mee, dus een prefix die per training verschilt maakt de
# prompt-cache waardeloos. Vier voorbeelden in plaats van twee, zodat geen enkel voorbeeld
# in zijn eentje de vorm bepaalt.
_GOUD_V2_FALLBACK = ("v2_php", "v2_datamodeling", "v2_bigdata", "v2_jsdesignpatterns")
GOUD_SELECTIE_BESTAND = "selectie.json"
GOUD_N = 4                         # voorbeelden in de gecachete system-prefix


def goud_bestanden(goud_dir: str) -> list[str]:
    """De trainingen in een goudmap. Het selectie-manifest is er geen."""
    import glob
    return sorted(p for p in glob.glob(os.path.join(goud_dir, "*.json"))
                  if os.path.basename(p) != GOUD_SELECTIE_BESTAND)


def laad_goud_selectie(goud_dir: str = GOUD_V2_DIR) -> tuple[str, ...]:
    """De ids van de few-shot-voorbeelden, uit het manifest of anders de fallback."""
    pad = os.path.join(goud_dir, GOUD_SELECTIE_BESTAND)
    try:
        with open(pad, encoding="utf-8") as f:
            ids = [str(x) for x in (json.load(f).get("ids") or []) if str(x).strip()]
    except (OSError, ValueError):
        return _GOUD_V2_FALLBACK
    return tuple(ids) or _GOUD_V2_FALLBACK


GOUD_VOORBEELDEN = laad_goud_selectie()


def actieve_goud_voorbeelden() -> list[str]:
    """Welke voorbeelden zitten in de prefix van deze run?

    Gaat mee in de per-training-JSON, naast `spec_versie`. Zonder dat spoor is achteraf niet
    te zien welk goud een training heeft gevormd -- en dat is precies wat je wilt weten als
    je de selectie gaat bijsturen op variatie in onderwerp en aantal dagen.
    """
    return list(GOUD_VOORBEELDEN[:GOUD_N])


# Het goud dateert van vóór de huidige introzin: 47 van de 78 trainingen openen hun doelen met
# "Na deze training heb je handvatten om:". Als few-shot demonstreert dat precies de zin die de
# schrijfspec verbiedt. De bullets eronder zijn al te-infinitief en lopen ongewijzigd door op de
# nieuwe zin, dus alleen de introregel hoeft om.
_GOUD_DOELEN_INTRO_RE = re.compile(r"^Na deze training[^\n]*", re.I)


def _actualiseer_doelen_intro(tekst: str) -> str:
    return _GOUD_DOELEN_INTRO_RE.sub(sjabloon.DOELEN_INTRO, tekst, count=1)


def _goud_modules_blok(html: str, titel: str) -> str:
    """De modules van een voorbeeld mét hun niveaus, als geneste bullets.

    Niet via `clean_text`: die vervangt <ul> en <li> allebei door een newline, waardoor
    moduletitel en sub-bullet in het voorbeeld niet meer van elkaar te onderscheiden zijn. Het
    model kreeg zo bij het zwaarst wegende kopje een platte lijst van dertig regels te zien --
    geen voorbeeld van een programma-indeling, en dus geen enkel houvast voor hoeveel modules
    er bij een training horen. Dat is een van de redenen dat het steevast op de bovengrens
    uitkwam.

    Zelfde vorm als `uit.render_markdown` gebruikt, zodat het voorbeeld eruitziet zoals de
    schrijver zijn eigen structuur aanlevert.
    """
    from score_trainings import clean_text
    modules = _modules_uit_ul(html, titel)
    if not modules:
        return clean_text(html, titel)
    opening = clean_text((html or "").split("<ul", 1)[0], titel)
    regels = [opening] if opening else []
    for module in modules:
        regels.append(f"* {module.get('titel', '')}")
        regels += [f"  * {b}" for b in module.get("bullets") or []]
    return "\n".join(regels).strip()


def goud_voorbeelden(n: int = GOUD_N, goud_dir: str = GOUD_V2_DIR) -> str:
    """De voorbeelden als tekstblok voor de gecachete system-prefix.

    Vaste selectie, niet per training: een wisselende prefix maakt de prompt-cache waardeloos.
    Zie `GOUD_VOORBEELDEN` voor waar die selectie vandaan komt.
    """
    from score_trainings import clean_text
    delen = []
    for tid in GOUD_VOORBEELDEN[:n]:
        pad = os.path.join(goud_dir, f"{tid}.json")
        if not os.path.exists(pad):
            continue
        with open(pad, encoding="utf-8") as f:
            d = json.load(f)
        c = d.get("content") or {}
        titel = d.get("titel", "")
        blok = [f"### {titel}"]
        for kop, sleutel in (("Overzicht", "summary"), ("Modules", "modules"),
                             ("Doelen", "objectives")):
            if sleutel == "modules":
                tekst = _goud_modules_blok(c.get(sleutel, ""), titel)
            else:
                tekst = clean_text(c.get(sleutel, ""), titel)
            if sleutel == "objectives":
                tekst = _actualiseer_doelen_intro(tekst)
            if tekst:
                blok.append(f"**{kop}**\n{tekst}")
        delen.append("\n\n".join(blok))
    if not delen:
        return ""
    # Geen liggend streepje in deze kop: hij staat vlak voor het materiaal dat de schrijver
    # gaat imiteren, en de schrijver mag het teken zelf niet gebruiken (schrijfspec Sectie 0.23).
    return ("VOORBEELDEN. Trainingen die al in de nieuwe stijl staan en alle regels halen.\n"
            "Neem de vorm over, niet de inhoud.\n\n" + "\n\n---\n\n".join(delen))


def build_writer_system() -> list[dict]:
    prefix = "\n\n---\n\n".join([_read(SCHRIJFSPEC), _read(HUMANISERING), _read(STIJLREGISTER),
                                 _read(CORRECTIES)])
    voorbeelden = goud_voorbeelden()
    if voorbeelden:
        prefix += "\n\n---\n\n" + voorbeelden
    else:
        # `herschreven/` staat in .gitignore, dus na een verse checkout is goud_v2 leeg. Stil
        # doorgaan zou een merkbaar slechtere batch opleveren zonder dat iemand weet waarom.
        print(f"LET OP: geen few-shot gevonden in {GOUD_V2_DIR}. "
              f"Draai `python bouw_goud_v2.py` en probeer opnieuw.", file=sys.stderr)
    instr = ("Je herschrijft één training naar de nieuwe stijl. Volg de schrijfspec hierboven "
             "letterlijk (verplichte openingszinnen, persona-toon, 'je'-vorm). Alle aantallen "
             "woorden -- per kopje én per zin -- zijn richtlijnen: mik erop, maar laat de "
             "gedachte de zin bepalen. Een bijzin schrappen, een nuance weglaten of een causaal "
             "verband inslikken om binnen een aantal te landen kost de tekst meer dan de "
             "afwijking oplevert; bij twijfel gaat betekenis en stijl vóór de vorm. Alleen de "
             "200 tekens van de Kortste omschrijving zijn hard. Schrijf ALLEEN de generatieve "
             "kopjes en roep tot slot het tool `submit_rewrite` aan. Verzin geen feiten "
             "(versies/vendors/cijfers) die niet in de bron of de feiten staan.")
    return [{"type": "text", "text": instr + "\n\n---\n\n" + prefix,
             "cache_control": {"type": "ephemeral"}}]


def build_judge_system() -> list[dict]:
    """Beoordelingsspec + dezelfde stijlbestanden als de schrijver.

    De beoordelingsspec verwees al naar `humanisering_nl.md` zonder dat de judge dat bestand
    ooit te zien kreeg -- hij kon LLM-frasen en verboden woorden dus niet handhaven. Schrijver
    en judge horen tegen dezelfde definitie van "goed" te oordelen, dus krijgen ze hier
    letterlijk dezelfde stijlteksten. Het goud gaat níét mee: dat is schrijfmateriaal.

    `correcties_nl.md` gaat wél mee. Dat zijn fout/goed-paren uit echte review-rondes, dus
    precies de kalibratie die een judge nodig heeft om "net niet raak" van "fout" te scheiden.
    """
    prefix = "\n\n---\n\n".join([_read(BEOORDELINGSSPEC), _read(HUMANISERING),
                                 _read(STIJLREGISTER), _read(CORRECTIES)])
    return [{"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}}]


def _opsomming(regels, leeg: str = "(geen)") -> str:
    regels = [r for r in regels if str(r).strip()]
    return "\n".join(f"- {r}" for r in regels) or leeg


# Een goedgekeurde actie leest vaak als een vráág ("BESLISSING NODIG: bepaal of de module
# vervangen wordt door X"). Zonder deze uitleg laat het model zo'n actie liggen, want er
# staat nergens wát de uitkomst was. Het besluit ligt in de indeling: goedgekeurd = doen.
BESLISSING_UITLEG = (
    "Let op bij acties die beginnen met 'BESLISSING NODIG:'. Staat zo'n actie hieronder\n"
    "onder ACTUALISERINGEN, dan hééft de reviewer de beslissing genomen en moet je de\n"
    "wijziging doorvoeren; behandel hem als een opdracht, niet als een open vraag. Staat\n"
    "hij onder NIET DOEN, dan blijft de bestaande situatie ongewijzigd."
)


# Het werkwoord van de actie is de bovengrens. Training 27 (SQL) kreeg "benoem concrete
# SQL-platformen (bv. PostgreSQL, SQL Server, cloud data warehouses) als context bij de
# training" mee en schreef "De SQL die je leert, pas je direct toe op verschillende
# platformen" -- een belofte die we niet nakomen, want die platformen komen in de training
# niet voor. De reviewer-voorwaarde ("in inleiding is dat prima") was wél gerespecteerd; het
# ging mis op de niveau-as, en daar stond nergens een regel over. Geen randgeval: 11 van de
# 16 trainingen met output hebben minstens één goedgekeurde noem-actie.
#
# Deze uitleg staat bewust IN het actualiseringenblok en niet in het modusblok. De enige rem
# die er was, `ACTUALISEREN_ONGEACHT_MODUS`, gaat over de omvang van de wijziging en wordt
# alleen gerenderd als er een MODUS_UITLEG is; in modus `volledig` kreeg de schrijver dus
# helemaal niets.
ACTIE_WERKWOORD = (
    "Het werkwoord van een actie is de bovengrens, geen startpunt. 'Benoem', 'noem' en\n"
    "'vermeld' betekenen dat de term ergens in de lopende tekst voorkomt als context, en\n"
    "verder niets: geen eigen module, geen bullet-onderwerp, geen doel, en vooral geen\n"
    "belofte dat de deelnemer er iets mee doet. Moet de training het onderwerp ook echt\n"
    "behandelen, dan staat er 'behandel', 'voeg toe' of 'neem op'; moet er iets voor wijken,\n"
    "dan staat er 'vervang' of 'update'. Twijfel je tussen twee lezingen, kies dan de\n"
    "lichtste. Wat je noemt maar niet traint, beloof je niet."
)


# De kern legt het niveau van de training vast en stuurt daarmee elk kopje. Wie hem schreef
# bepaalt hoeveel gezag hij heeft: een reviewer-kern is een besluit, een scorer-kern een
# lezing. Zonder dit onderscheid wint de kern altijd -- en dat is precies hoe een tweedaagse
# introductietraining een tekst kreeg waarin de deelnemer modellen in productie neemt.
KERN_GEZAG_REVIEWER = (
    "De kern hierboven is door een mens vastgesteld. Hij is leidend: schrijf de training zoals\n"
    "de kern hem beschrijft, ook waar de brontekst iets anders suggereert."
)
KERN_GEZAG_SCORER = (
    "De kern hierboven is een lezing van de scorer, geen besluit van een mens. Botst hij met de\n"
    "brontekst over wat de training feitelijk doet of op welk niveau, dan wint de BRONTEKST.\n"
    "Meld die botsing in `notities`, zodat een mens ernaar kan kijken."
)

# Zonder deze regel is de brontekst het enige blok in de prompt zonder opdracht eromheen, en
# leest het model hem als achtergrond bij de scorer-velden in plaats van als de training zelf.
BRONTEKST_UITLEG = (
    "BRONTEKST. De bestaande trainingsbeschrijving, ongewijzigd en onafgekapt. Dit is wat de\n"
    "training feitelijk is: welke onderwerpen erin zitten, en wat de deelnemer ermee doet. De\n"
    "velden hierboven zijn een samenvatting ervan; deze tekst is het origineel. Let vooral op\n"
    "de werkwoorden: \"maak je kennis met\", \"we introduceren\", \"we geven een overzicht\"\n"
    "beschrijven een ander niveau dan \"je bouwt\", \"je richt in\", \"je optimaliseert\".\n"
    "Beloof nooit meer dan hier staat.\n\n"
    "Eén uitzondering, en die gaat vóór: de GOEDGEKEURDE ACTUALISERINGEN hierboven. Die voer\n"
    "je uit, ook al staan ze niet in deze brontekst; dat is precies waarom ze bestaan. Deze\n"
    "alinea is geen reden om er één te laten liggen."
)

# De judge kreeg de brontekst niet, terwijl de beoordelingsspec §2 hem opdraagt elke claim te
# herleiden tot "`bruikbaar` of de brontekst". Hij toetste feitgetrouwheid dus tegen de
# samenvatting van de scorer: een claim die de bron tegensprak maar `bruikbaar` niet, kwam er
# ongehinderd doorheen. Sinds de kern het niveau draagt weegt dat zwaarder -- zwijgt de kern
# over een aspect, dan had de judge niets om op terug te vallen.
#
# De uitleg wijkt bewust af van die voor de schrijver: de valkuil is omgekeerd. De schrijver
# moet de bron niet overtreffen; de judge moet hem niet napluizen op vorm. Zonder die laatste
# alinea gaat hij het concept afrekenen op het niet volgen van de bronstructuur -- en juist
# afwijken van die structuur is het hele punt van herschrijven.
#
# De uitzondering voor actualiseringen is geen detail maar de tegenhanger van de hele regel.
# Een goedgekeurde actie voegt per definitie iets toe dat niet in de bron staat -- dat is
# waarom hij bestaat. Met de bron als enige maatstaf is elke actualisering een "verzonnen
# feit", en draait de judge precies het werk terug dat de reviewer in de sessie deed.
#
# Maar hij dekt het onderwerp, niet het niveau, en dat stond er eerst niet bij. Training 27
# kreeg "benoem concrete SQL-platformen" en schreef "pas je direct toe op"; de judge zette
# `feitgetrouw.pass = true` met nul problemen, want de vrijstelling verbood hem letterlijk om
# zo'n passage als te hoge belofte af te rekenen. Zie `ACTIE_WERKWOORD`.
BRONTEKST_UITLEG_JUDGE = (
    "BRONTEKST. De bestaande trainingsbeschrijving, ongewijzigd en onafgekapt. De velden\n"
    "hierboven zijn een samenvatting ervan door de scorer; dit is het origineel. Gebruik hem\n"
    "als maatstaf voor precies twee dingen:\n"
    "1. FEITGETROUWHEID: elke inhoudelijke claim (versie, vendor, tool, feature, cijfer,\n"
    "   jaartal, certificering) moet herleidbaar zijn tot deze tekst, tot de feiten hierboven\n"
    "   of tot een goedgekeurde actualisering. Staat hij nergens, dan is het een feitfout.\n"
    "2. NIVEAU: lees de werkwoorden. \"Maak je kennis met\", \"we introduceren\", \"we geven\n"
    "   een overzicht\" beschrijven iets anders dan \"je bouwt\", \"je richt in\", \"je\n"
    "   optimaliseert\". Belooft het concept meer dan hier staat, dan is dat een fail.\n\n"
    "UITZONDERING op punt 1, en die gaat vóór: het ONDERWERP van een GOEDGEKEURDE\n"
    "ACTUALISERING hierboven hoort in de tekst, ook al staat het niet in deze brontekst en\n"
    "verschuift het waar de training over gaat. De reviewer heeft daarvoor getekend en de bron\n"
    "is juist het verouderde deel. Reken zo'n term nooit af als ongegrond of verzonnen; twijfel\n"
    "je of iets onder een goedgekeurde actie valt, dan valt het eronder.\n\n"
    "Die uitzondering dekt het ONDERWERP, niet het NIVEAU. Punt 2 blijft dus gewoon gelden:\n"
    "het werkwoord van de actie is de bovengrens. Een actie die vraagt om iets te BENOEMEN\n"
    "rechtvaardigt een vermelding en niets meer; wordt daar een leeractiviteit of een belofte\n"
    "van gemaakt (\"pas je toe op\", \"je werkt met\"), dan is dat een te hoge belofte en een\n"
    "fail, ook al is de term zelf goedgekeurd. Twijfel je hoe zwaar een actie uitgevoerd mocht\n"
    "worden, dan geldt de lichtste lezing. De andere grens is de VOORWAARDE die de reviewer\n"
    "eraan hing.\n\n"
    "Reken het concept NIET af op vorm. Een andere volgorde, andere indeling, andere\n"
    "formulering, samengevoegde of gesplitste modules, geschrapte ruis: dat is herschrijven,\n"
    "geen fout. Ontbrekende broninhoud is alleen een punt als er iets wezenlijks verdween."
)


# De bestaande tekst per kopje. Nodig zodra de modus zegt dat de tekst behouden moet
# blijven: `build_source_text` slaat `setup`, `follow_up`, `summary_edudex` en
# `certification` over (die zijn voor de scorer boilerplate), en levert bovendien één lap
# tekst in plaats van een indeling per kopje. Voor herschrijven vanaf nul is dat prima, voor
# bijwerken niet -- dan wil je precies zien wat er in elk veld staat.
def huidige_versie_blok(content: dict, titel: str = "") -> str:
    """Bestaande CMS-content -> leesbare weergave per kopje, in de volgorde van het template."""
    if not content:
        return ""
    plat = uit.content_naar_platte_tekst(content, titel)
    delen = []
    for kopje in sjabloon.KOPJES:
        tekst = str(plat.get(kopje.cms, "") or "").strip()
        delen.append(f"## {kopje.kop}\n{tekst or '(leeg)'}")
    return "\n\n".join(delen)


HUIDIGE_VERSIE_UITLEG = (
    "HUIDIGE VERSIE. De bestaande tekst van deze training, per kopje. Dit is je\n"
    "uitgangsmateriaal én je maatstaf: dit is wat de training feitelijk is en belooft.\n"
    "Let vooral op de werkwoorden: \"maak je kennis met\", \"we introduceren\", \"we geven\n"
    "een overzicht\" beschrijven een ander niveau dan \"je bouwt\", \"je richt in\", \"je\n"
    "optimaliseert\". Beloof nooit meer dan hier staat.\n\n"
    "Een kopje dat op \"(leeg)\" staat ontbrak in de bron; dat vul je aan uit wat de andere\n"
    "kopjes zeggen, niet uit wat je aannemelijk vindt.\n\n"
    "Eén uitzondering, en die gaat vóór: de GOEDGEKEURDE ACTUALISERINGEN hierboven. Die voer\n"
    "je uit, ook al staan ze hier niet; dat is precies waarom ze bestaan."
)


# De opdracht per herschrijfniveau. Deze tekst staat in de USER-message en niet in de
# system-prefix: die prefix is één gecachet blok van ~20k tokens, en een modus-afhankelijke
# prefix zou daar vier varianten van maken.
#
# Wat hier NIET in staat: de actualiseringen. Die lopen op elk niveau mee -- zie
# `ACTUALISEREN_ONGEACHT_MODUS` hieronder en de toelichting bij `RewriteBriefing.modus`.
MODUS_UITLEG: dict[str, str] = {
    "stijl": (
        "OPDRACHT: BIJWERKEN NAAR DE ACTUELE SCHRIJFREGELS.\n"
        "Je bent hier redacteur, geen auteur. De inhoud van deze training klopt en is\n"
        "compleet; wat niet meer klopt is de formulering. Herschrijf zin voor zin naar de\n"
        "regels in de spec hierboven: de 'je'-vorm, de verplichte openingszinnen, het\n"
        "stijlregister, het causale verband, weg met marketingtaal en verboden woorden.\n\n"
        "Verander NIET wát er staat. Geen onderwerpen toevoegen, geen onderwerpen weglaten,\n"
        "geen modules samenvoegen of splitsen, geen volgorde omgooien, geen doelen erbij\n"
        "verzinnen. Elk feit, elk onderwerp en elke belofte in jouw versie staat ook in de\n"
        "huidige versie hieronder. Kom je een kopje tegen dat inhoudelijk rammelt, laat het\n"
        "dan rammelen en meld het in `notities`; dat is een besluit voor een mens."
    ),
    "format": (
        "OPDRACHT: BIJWERKEN NAAR HET ACTUELE FORMAT.\n"
        "De inhoud van deze training klopt, maar de vorm niet: er ontbreken kopjes, of de\n"
        "structuur past niet op het format. Breng hem in vorm en pas daarbij ook de actuele\n"
        "schrijfregels toe.\n\n"
        "Wat je MAG: herindelen, modules samenvoegen of splitsen zodat je op 4-6 modules\n"
        "uitkomt, de volgorde aanpassen, en de ontbrekende kopjes schrijven, maar die leid\n"
        "je AF uit wat er al staat. Doelgroep, Voorkennis en Doelen zijn bij deze trainingen\n"
        "vaak leeg; die volgen uit de modules en de inleiding.\n\n"
        "Wat je NIET mag: onderwerpen toevoegen die nergens in de huidige versie staan, of\n"
        "het niveau optrekken omdat een kopje anders dun blijft. Herindelen is iets anders\n"
        "dan aanvullen. Blijft een kopje mager omdat de bron er niets over zegt, meld dat\n"
        "dan in `notities` in plaats van het vol te schrijven."
    ),
    "volledig": "",   # geen extra opdracht: het gedrag van vóór deze schaal
}

# Actualiseringen staan los van het herschrijfniveau en gelden dus ook in `stijl` en
# `format`. Zonder deze alinea leest het model "verander niets aan de inhoud" als een verbod
# op precies het werk dat de reviewer heeft goedgekeurd.
ACTUALISEREN_ONGEACHT_MODUS = (
    "De goedgekeurde ACTUALISERINGEN hierboven staan LOS van deze opdracht en voer je hoe\n"
    "dan ook uit. Ze zijn de enige plek waar nieuwe inhoud vandaan mag komen. Houd de\n"
    "wijziging wel zo klein als de actie zelf: pas aan wat de actie raakt en laat de rest\n"
    "op het niveau dat hierboven staat."
)


def build_writer_user(b: RewriteBriefing) -> str:
    dagen = str(b.dagen) if b.dagen is not None else "ONBEKEND (schat plausibel)"
    kern_gezag = KERN_GEZAG_REVIEWER if b.kern_van_reviewer else KERN_GEZAG_SCORER
    herkomst = "vastgesteld door reviewer" if b.kern_van_reviewer else "lezing van de scorer"
    return (
        f"Titel: {b.nieuwe_titel}\n"
        f"Persona: {b.persona}\n"
        f"Aantal dagen: {dagen}\n"
        f"Verdict scorer: {b.verdict}{'  (THIN: markeer constructie)' if b.thin else ''}\n\n"
        f"KERN ({herkomst}). Hierin staat het NIVEAU van de training; schrijf nooit boven dat\n"
        f"niveau, ook niet als een kopje om meer tekst vraagt:\n{b.kern_definitief}\n\n"
        f"{kern_gezag}\n\n"
        f"Te verwerken feiten (bruikbaar):\n{_opsomming(b.bruikbaar)}\n\n"
        f"Weglaten (strippen):\n{_opsomming(b.strippen)}\n\n"
        f"Gaten (vul plausibel waar afleidbaar):\n{_opsomming(b.gaten)}\n\n"
        f"{BESLISSING_UITLEG}\n\n"
        f"{ACTIE_WERKWOORD}\n\n"
        "ACTUALISERINGEN, door de reviewer goedgekeurd. Voer deze uit; staat er een\n"
        "VOORWAARDE bij, dan is die bindend en gaat hij vóór de actietekst:\n"
        f"{_opsomming(x.als_instructie() for x in b.goedgekeurd)}\n\n"
        "NIET DOEN, door de reviewer afgewezen. Voer deze NIET uit, ook niet als de\n"
        "brontekst er aanleiding toe geeft:\n"
        f"{_opsomming(x.als_instructie() for x in b.afgewezen)}\n\n"
        f"Rewrite-guidance: {b.guidance_definitief or '(geen)'}\n\n"
        f"{_modus_en_materiaal(b)}"
    )


def _modus_en_materiaal(b: RewriteBriefing) -> str:
    """Het slotblok van de briefing: de opdracht per niveau + het materiaal om mee te werken.

    In `stijl` en `format` VERVANGT de huidige versie de brontekst, in plaats van ernaast te
    staan. Het is dezelfde content: `build_source_text` bouwt zijn tekst uit precies dit
    `content`-object, alleen zonder `setup`, `follow_up`, `summary_edudex` en `certification`
    (voor de scorer boilerplate) en zonder indeling per kopje. Twee keer hetzelfde meesturen
    kost tokens en geeft het model twee versies van de waarheid, waarvan de ene net iets
    minder compleet is dan de andere.

    Valt de huidige content weg -- geen bron gevonden -- dan is er niets te behouden en
    blijft alleen de brontekst over, ongeacht de modus.
    """
    uitleg = MODUS_UITLEG.get(b.modus, "")
    huidig = huidige_versie_blok(b.huidige_content, b.nieuwe_titel) if b.behoudt_tekst else ""
    blokken = []
    if uitleg:
        blokken.append(uitleg)
        if b.goedgekeurd:
            blokken.append(ACTUALISEREN_ONGEACHT_MODUS)
    if huidig:
        blokken.append(f"{HUIDIGE_VERSIE_UITLEG}\n\n{huidig}")
    else:
        blokken.append(f"{BRONTEKST_UITLEG}\n\n{b.source_text}")
    return "\n\n".join(blokken)


# Wat de judge extra moet weten als de training NIET vanaf nul is opgebouwd. Additief op
# `BRONTEKST_UITLEG_JUDGE`, niet in plaats daarvan: de vorm-as blijft in elke modus gelijk
# (geen oordeel over lengte, geen afrekenen op afwijken van de bronstructuur). Er komt één
# as bij, en dat is precies de as die geen enkele code-check kan zien.
#
# Zonder dit blok gebeurt het omgekeerde van wat je wilt: de judge ziet een bewust
# conservatieve tekst, mist de herschrijving die hij gewend is, en stuurt hem terug.
MODUS_UITLEG_JUDGE = {
    "stijl": (
        "LET OP: deze training is bewust ALLEEN bijgewerkt naar de actuele schrijfregels.\n"
        "De opdracht was: de formulering aanpassen, de inhoud ongemoeid laten. Beoordeel hem\n"
        "daarop.\n\n"
        "Dat betekent twee dingen. Reken het concept NIET af omdat het dicht bij de huidige\n"
        "versie blijft, dezelfde onderwerpen in dezelfde volgorde behandelt of weinig is\n"
        "veranderd; dat was de opdracht, niet een tekortkoming. En reken het WÉL af op het\n"
        "omgekeerde: elk onderwerp, feit, doel of belofte in het concept moet herleidbaar\n"
        "zijn tot de huidige versie hieronder of tot een goedgekeurde actualisering. Wat er\n"
        "los van staat is drift, en drift is hier een fail."
    ),
    "format": (
        "LET OP: deze training is bewust ALLEEN bijgewerkt naar het actuele format.\n"
        "De opdracht was: in vorm brengen, ontbrekende kopjes afleiden uit wat er al stond,\n"
        "en de inhoud verder ongemoeid laten.\n\n"
        "Herindelen hoort er dus bij: samengevoegde of gesplitste modules, een andere\n"
        "volgorde en nieuw geschreven Doelgroep-, Voorkennis- of Doelen-kopjes zijn geen\n"
        "fout. Wat wél een fout is: een onderwerp dat nergens in de huidige versie voorkomt,\n"
        "of een kopje dat is volgeschreven met inhoud die niet uit de andere kopjes volgt.\n"
        "Dat is de fout die deze modus moet voorkomen; let er scherper op dan normaal."
    ),
}


def build_judge_user(b: RewriteBriefing, document: dict) -> str:
    herkomst = "vastgesteld door reviewer" if b.kern_van_reviewer else "lezing van de scorer"
    # `BRONTEKST_UITLEG_JUDGE` blijft in élke modus staan: daar zitten de feitgetrouwheids-
    # en niveau-instructie, de uitzondering voor goedgekeurde actualiseringen én het verbod
    # om op vorm af te rekenen. Alleen het materiaal eronder wisselt -- in `stijl`/`format`
    # de bestaande tekst per kopje (een superset van `source_text`, dat `setup`, `follow_up`,
    # `summary_edudex` en `certification` overslaat), anders de brontekst zoals altijd.
    huidig = huidige_versie_blok(b.huidige_content, b.nieuwe_titel) if b.behoudt_tekst else ""
    modus_blok = MODUS_UITLEG_JUDGE.get(b.modus, "") if huidig else ""
    materiaal = "\n\n".join(x for x in (
        BRONTEKST_UITLEG_JUDGE,
        modus_blok,
        huidig or b.source_text,
    ) if x)
    return (
        f"Persona: {b.persona}\n"
        f"Aantal dagen: {b.dagen if b.dagen is not None else 'onbekend'}\n\n"
        f"KERN ({herkomst}). Hierin staat het niveau waarop de training hoort te liggen:\n"
        f"{b.kern_definitief}\n\n"
        f"Feiten (bruikbaar):\n{_opsomming(b.bruikbaar)}\n\n"
        "Weggelaten (strippen). Deze mogen niet terug zijn in het concept:\n"
        f"{_opsomming(b.strippen)}\n\n"
        "Gaten. Hierover zweeg de bron. Wat het concept hier invult is constructie: geen\n"
        "feitfout, wél reden om de output als thin te markeren:\n"
        f"{_opsomming(b.gaten)}\n\n"
        f"{BESLISSING_UITLEG}\n\n"
        f"{ACTIE_WERKWOORD}\n\n"
        "Goedgekeurde actualiseringen (moeten verwerkt zijn, en niet zwaarder dan hun\n"
        "werkwoord toelaat):\n"
        f"{_opsomming(x.als_instructie() for x in b.goedgekeurd)}\n\n"
        # Mét de REDEN erbij: de schrijver kreeg die al (`build_writer_user`), de judge niet.
        # Zonder de reden ziet hij alleen dat iets niet mag en niet waaróm, en dat is precies
        # het verschil tussen "staat er niet in" en "is bewust weggehouden".
        "Afgewezen actualiseringen (mogen NIET terugkomen):\n"
        f"{_opsomming(x.als_instructie() for x in b.afgewezen)}\n\n"
        f"{materiaal}\n\n"
        f"CONCEPT. Dit is wat je beoordeelt:\n{uit.render_markdown(document, b.nieuwe_titel)}"
    )


# ---------------------------------------------------------------------------
# 7. API-CALL (tool-output, retry met budgetverdubbeling; zelfde geest als de scorer)
# ---------------------------------------------------------------------------

def make_client():
    """De client van het scoreproject, met de tijdgrenzen van dít project erop.

    `score_trainings.make_client` maakt een kale `anthropic.Anthropic()` en krijgt daarmee de
    SDK-defaults. Die passen daar: de scorer doet één call per training. Wij doen er tot 24,
    dus hier komen `LEES_TIMEOUT` en `MAX_RETRIES` erop.

    Via `with_options` en niet door de scorer te wijzigen: de importrichting is
    eenrichtingsverkeer, en een timeout die bij ons hoort heeft daar niets te zoeken. De naam
    blijft `make_client`, zodat het notebook en de drie aanroepers hieronder vanzelf de
    ingestelde client krijgen in plaats van de kale.
    """
    import httpx
    return _kale_client().with_options(
        timeout=httpx.Timeout(LEES_TIMEOUT, connect=VERBIND_TIMEOUT),
        max_retries=MAX_RETRIES,
    )


class TijdOverschreden(RuntimeError):
    """Het tijdsbudget van deze training is op.

    Een gewone `Exception`, en dat is het hele punt: de lussen in `rewrite_file` vangen hem
    net als elke andere fout op, maken er via `_mislukte_training` een `error`-rij van en gaan
    door naar de volgende training. Eén training die vastloopt kost daarmee die training en
    nooit de batch -- en omdat `bouw_wachtrij` error-rijen niet overslaat, draait hij de
    volgende run gewoon weer mee.
    """


_deadline: float | None = None    # None = geen budget; zie `tijdsbudget`
_budget: float | None = None      # alleen voor de foutmelding


@contextmanager
def tijdsbudget(seconden: float | None = TIJDSBUDGET):
    """Zet de deadline voor alles wat hierbinnen een modelcall doet.

    Bewust een modulevariabele en geen parameter. `_call_tool` wordt langs vijf paden bereikt
    (schrijver, judge, vervolgstappen, modus, actualisering) en vanuit twee lussen; een
    parameter zou bij elk van die aanroepers apart moeten worden doorgegeven en dus bij elk van
    hen vergeten kunnen worden. Dat is dezelfde val als bij `build_check_ctx`, waar twee
    aanroepers hun eigen dict bouwden en de een een check draaide die de ander niet had.

    Alleen de batchpaden zetten een budget (`rewrite_one`, `neem_over`). Bij een losse
    hergeneratie zit er een mens aan de knoppen die zelf kan afbreken; daar staat `_deadline`
    op None en doet `_bewaak_tijd` niets. Nesten mag: de binnenste deadline geldt, en bij het
    verlaten staat de buitenste weer.
    """
    global _deadline, _budget
    vorige_deadline, vorige_budget = _deadline, _budget
    _budget = None if seconden is None else float(seconden)
    _deadline = None if _budget is None else time.monotonic() + _budget
    try:
        yield
    finally:
        _deadline, _budget = vorige_deadline, vorige_budget


def _bewaak_tijd(wat: str = "") -> None:
    """Gooit `TijdOverschreden` zodra de deadline voorbij is. Zonder budget: niets."""
    if _deadline is None or time.monotonic() <= _deadline:
        return
    minuten = (_budget or 0) / 60
    raise TijdOverschreden(f"tijdsbudget van {minuten:.0f} minuten verstreken"
                           + (f" {wat}" if wat else ""))


# ---------------------------------------------------------------------------
# 7b. HET STORINGSSPOOR -- waar de tijd van een training heen ging
# ---------------------------------------------------------------------------
#
# `seconden` en `rondes` zeggen wat een training kostte, niet waaraan. Een training die vier
# stiltes van `LEES_TIMEOUT` opving en tóch slaagde is in het sheet niet te onderscheiden van
# een die schoon doorliep, en juist daar zit het signaal: over vier batches staan er nog 3
# error-rijen tegen 198 geslaagde trainingen, en van die 198 weten we niets. Zonder deze
# telling is "clusteren de fouten?" per constructie niet te beantwoorden -- de fouten zijn te
# zeldzaam, de bijna-fouten niet.

@dataclass
class Storingsspoor:
    """Wat één training aan modelcalls deed, en wat daarvan verloren ging.

    `stiltes` telt élke gevangen netwerkfout, ook die waarna de herkansing wél lukte. Dat is
    het hele punt: een geslaagde training met drie stiltes zegt evenveel over het moment als
    een mislukte, en er zijn er veel meer van.

    `traagste_call` is de enige plek waar de retries van de SDK zichtbaar worden. Die zitten
    op `MAX_RETRIES` en backoffen buiten ons zicht; een call die 900 s duurde terwijl de
    mediaan onder de minuut ligt, is er vier keer opnieuw gestuurd.
    """
    calls: int = 0                    # geslaagde modelcalls
    call_seconden: float = 0.0        # tijd in geslaagde calls
    traagste_call: float = 0.0
    stiltes: int = 0                  # gevangen netwerkfouten, geslaagd herkanst of niet
    stilte_seconden: float = 0.0      # tijd in pogingen die niets opleverden

    def tel_call(self, duur: float) -> None:
        self.calls += 1
        self.call_seconden = round(self.call_seconden + duur, 1)
        self.traagste_call = round(max(self.traagste_call, duur), 1)

    def tel_stilte(self, duur: float) -> None:
        self.stiltes += 1
        self.stilte_seconden = round(self.stilte_seconden + duur, 1)

    def als_dict(self) -> dict:
        return {"calls": self.calls, "call_seconden": self.call_seconden,
                "traagste_call": self.traagste_call, "stiltes": self.stiltes,
                "stilte_seconden": self.stilte_seconden}


_spoor = Storingsspoor()


def begin_spoor() -> Storingsspoor:
    """Start het spoor van één training; geeft het lopende spoor terug.

    Modulevariabele om dezelfde reden als `_deadline`: `_stream_bericht` wordt langs vijf
    paden bereikt en vanuit twee lussen, en een parameter zou bij elk van hen vergeten kunnen
    worden. Bewust géén contextmanager zoals `tijdsbudget`, en dat verschil is functioneel: de
    batchlus leest het spoor uit ná de training, óók (juist) als die omviel. Een manager die
    bij het verlaten opruimt zou precies het geval wissen waarvoor dit bestaat.
    """
    global _spoor
    _spoor = Storingsspoor()
    return _spoor


def huidig_spoor() -> Storingsspoor:
    """Het spoor van de training die nu draait, of van de laatste die draaide."""
    return _spoor


def _extract_tool_input(response, tool_name: str) -> dict | None:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input
    return None


def _netwerkfouten() -> tuple[type[BaseException], ...]:
    """De uitzonderingen die op een dode lijn wijzen en niet op een fout antwoord.

    `httpx` staat hier naast `anthropic.APIConnectionError` en dat is precies het punt: de SDK
    wikkelt (en retryt) alleen het openen van de request, dus een `ReadTimeout` MIDDEN in een
    stream komt kaal naar boven. Training 369 had letterlijk `ReadTimeout: The read operation
    timed out` in zijn `reden`-kolom staan, en niet een `APITimeoutError`.

    De imports staan in de functie, net als bij de google-imports in `drive_upload.py`: dan
    kan `test_rewrite.py` deze module blijven importeren zonder dat `httpx` de tests raakt.
    """
    import anthropic
    import httpx
    # TimeoutException dekt Read/Write/Connect/Pool; NetworkError de reset en de kapotte pipe;
    # RemoteProtocolError de server die er middenin uitstapt.
    return (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError,
            anthropic.APIConnectionError)


# Waarop clusteren fouten? `reden` is een zin voor een mens en per uitzondering anders
# geformuleerd; hierop kun je groeperen.
FOUT_NETWERK = "netwerk"           # dode lijn, timeout, verbinding weg
FOUT_LIMIET = "limiet"             # 429: te veel tokens of requests in het venster
FOUT_OVERBELAST = "overbelast"     # 5xx, waaronder 529 overloaded_error
FOUT_TIJDSBUDGET = "tijdsbudget"   # `TIJDSBUDGET` verstreken
FOUT_OVERIG = "overig"             # alles wat aan onze kant misging
# De drie soorten die bij het MOMENT horen en niet bij deze training. Alleen deze horen te
# clusteren, en alleen deze verdienen een afkoeling: opnieuw beginnen met dezelfde training
# lost niets op zolang de lijn ligt, en de volgende training treft dezelfde lijn.
STORINGSSOORTEN = (FOUT_NETWERK, FOUT_LIMIET, FOUT_OVERBELAST)

# Het fouttype van Anthropic (`body["error"]["type"]`), en waarom dat vóór de statuscode gaat:
# een `error`-EVENT midden in een stream draagt de status van de STREAM. De SDK doet daar
# `_make_status_error(f"{body}", response=self.response)`, en die response is de 200 waarmee de
# stream werd geopend -- de fout zelf heeft geen eigen status. Training 2560 (Terraform
# Automation) kreeg daardoor `overig` voor een `api_error` en dus geen afkoeling, terwijl dat de
# zuiverste vorm is van een fout die bij het moment hoort en niet bij die training.
FOUTSOORT_PER_API_TYPE = {
    "rate_limit_error": FOUT_LIMIET,
    "overloaded_error": FOUT_OVERBELAST,
    "api_error": FOUT_OVERBELAST,
    "timeout_error": FOUT_NETWERK,     # de server gaf het op, niet de lijn -- zelfde gevolg
}


def _foutsoort(fout: BaseException) -> str:
    """De uitzondering terug naar één van de vijf soorten hierboven.

    Eerst het fouttype van Anthropic (zie hierboven), dan pas `status_code`, en pas daarna het
    klassetype. Die volgorde is de les van 2560: de status hoort bij het transport en het type
    bij de fout, en midden in een stream lopen die twee uit elkaar.

    Op `status_code` en niet op het klassetype van de SDK: `RateLimitError` en de 5xx-klassen
    verschuiven wel eens tussen versies, een statuscode niet. 529 (`overloaded_error`) heeft
    bij Anthropic geen eigen klasse en valt zo vanzelf onder `overbelast` -- dat is precies de
    fout waar `MAX_RETRIES` in batch 1 van 2 naar 4 ging.
    """
    if isinstance(fout, TijdOverschreden):
        return FOUT_TIJDSBUDGET
    soort = FOUTSOORT_PER_API_TYPE.get(getattr(fout, "type", None))
    if soort:
        return soort
    code = getattr(fout, "status_code", None)
    if code == 429:
        return FOUT_LIMIET
    if isinstance(code, int) and code >= 500:
        return FOUT_OVERBELAST
    if isinstance(fout, _netwerkfouten()):
        return FOUT_NETWERK
    return FOUT_OVERIG


# Welke serverfouten mogen opnieuw? Bewust NIET dezelfde verzameling als hierboven, en het
# verschil zit op `rate_limit_error`: die classificeren we wél als storing (hij hoort bij het
# moment) maar herkansen we niet, want een directe tweede poging in hetzelfde venster is
# precies wat een limiet niet wil. De rest verandert wél van antwoord bij een tweede poging;
# `invalid_request_error` en `authentication_error` staan er om dezelfde reden niet in.
HERKANSBARE_API_TYPEN = ("api_error", "overloaded_error", "timeout_error")


def _herkansbare_fouten() -> tuple[type[BaseException], ...]:
    """Wat `_stream_bericht` überhaupt vangt; `_mag_herkansen` beslist daarna."""
    import anthropic
    return _netwerkfouten() + (anthropic.APIStatusError,)


def _mag_herkansen(fout: BaseException) -> bool:
    """Verandert een tweede poging hier iets aan?

    Twee families, en ze delen precies één eigenschap: de retry-laag van de SDK ziet ze per
    constructie niet. Die zit op het openen van de request, en allebei dienen ze zich pas aan
    als de stream al loopt.

    - **een dode lijn** (`_netwerkfouten`): er kwam niets meer over. Training 369 sneuvelde er
      na 381 s op, met nog 1119 s budget over;
    - **een levende lijn die een serverfout aflevert.** Training 2560 kreeg na 22 s een
      `api_error` als event in de stream. De stream-response was 200, dus `MAX_RETRIES` (4)
      had niets te retryen en `_netwerkfouten` herkende het niet: één 500 en de hele training
      weg, zonder één poging.
    """
    if isinstance(fout, _netwerkfouten()):
        return True
    return getattr(fout, "type", None) in HERKANSBARE_API_TYPEN


def _stream_bericht(client, *, model: str, max_tokens: int, system, messages: list[dict],
                    tools: list[dict], extra: dict, wat: str):
    """Eén streamende modelcall, met `NETWERK_HERKANSINGEN` erbij als de lijn wegvalt.

    Zelf itereren in plaats van meteen `get_final_message()` aanroepen: die doet intern precies
    dit, maar dan zonder dat wij ertussen kunnen kijken. Zo breekt een call die over de deadline
    heen loopt af binnen één event in plaats van pas als het model klaar is, en dát maakt van
    `TIJDSBUDGET` een plafond in plaats van een controle tussen de calls door. De `with` sluit
    de verbinding bij het gooien.

    De herkansing weegt licht omdat er niets aan onze kant is gebeurd: er is geen document, geen
    bestand en geen halve staat, alleen tokens die we kwijt zijn. `_bewaak_tijd` staat vóór elke
    poging, dus dicht bij de deadline herkanst hij niet meer maar gooit hij `TijdOverschreden`
    -- die erft van RuntimeError en valt dus buiten `_netwerkfouten()`.
    """
    for poging in range(NETWERK_HERKANSINGEN + 1):
        _bewaak_tijd(f"vóór een call naar {wat}")
        begonnen = time.monotonic()
        try:
            with client.messages.stream(
                model=model, max_tokens=max_tokens, system=system,
                messages=messages, tools=tools, **extra,
            ) as stroom:
                for _gebeurtenis in stroom:
                    _bewaak_tijd(f"tijdens een call naar {wat}")
                bericht = stroom.get_final_message()
            _spoor.tel_call(time.monotonic() - begonnen)
            return bericht
        except _herkansbare_fouten() as fout:
            # Een `invalid_request_error` of een 401 gaat hier meteen door: die geeft bij een
            # tweede poging hetzelfde antwoord, en dan is herkansen alleen maar tijd.
            if not _mag_herkansen(fout):
                raise
            # De stilte wordt geteld vóór de `raise`, dus ook de poging die het opgeeft komt
            # in het spoor. Anders telt uitgerekend de training die eraan sneuvelde er nul.
            _spoor.tel_stilte(time.monotonic() - begonnen)
            # Op is op: de aanroeper maakt er via `_mislukte_training` een `error`-rij van, en
            # `bouw_wachtrij` plant die de volgende run gewoon opnieuw in.
            if poging == NETWERK_HERKANSINGEN:
                raise
            print(f"  ({type(fout).__name__} tijdens {wat}; nog een poging)", file=sys.stderr)


def _call_tool(client, system, user_text: str, tools: list[dict], tool_name: str,
               max_tokens: int = MAX_TOKENS, model: str = MODEL,
               thinking: dict | None = THINKING) -> dict | None:
    """Roept het model tot het `tool_name` aanroept. Verdubbelt budget bij afkapping.

    `model`/`thinking` staan los zodat de goedkope keuzes (vervolgtrainingen) op een
    klein model zonder thinking kunnen draaien, met dezelfde retry-logica.

    Dit is ook de plek waar `TIJDSBUDGET` wordt bewaakt (in `_stream_bericht`) -- alle vijf de
    modelpaden van dit project komen hier langs, dus één bewaking daar dekt ze allemaal. Die
    functie doet ook de herkansing bij een dode lijn; hier gaat het alleen over antwoorden die
    er wél zijn maar niet deugen.

    De call STREAMT, en dat is geen snelheidskeuze maar de voorwaarde waaronder de
    verdubbeling hieronder mag bestaan. Een niet-streamende call rekent bij de SDK
    `3600 * max_tokens / 128000` seconden en weigert alles boven de tien minuten: vanaf
    max_tokens 21334 gooit `client.messages.create` een ValueError voordat er iets over
    de lijn gaat. Onze tweede poging vraagt 32000, dus de eerste keer dat de judge zijn
    budget opmaakte sneuvelde niet die call maar de retry -- en met die retry de hele
    batch van 46. Ga je terug naar `create`, dan komt dat plafond terug.
    """
    messages = [{"role": "user", "content": user_text}]
    budget = max_tokens
    extra = {"thinking": thinking} if thinking else {}
    for _ in range(3):
        resp = _stream_bericht(client, model=model, max_tokens=budget, system=system,
                               messages=messages, tools=tools, extra=extra, wat=tool_name)
        tool_input = _extract_tool_input(resp, tool_name)
        if tool_input is not None:
            return tool_input
        if resp.stop_reason == "max_tokens":
            budget *= 2
            continue
        # tekst zonder tool-aanroep -> voer terug + duw aan
        messages = messages + [
            {"role": "assistant", "content": resp.content},
            {"role": "user", "content": f"Roep nu het tool `{tool_name}` aan met je resultaat."},
        ]
    return None


def rewrite_input_complete(inp: dict) -> bool:
    if not isinstance(inp, dict):
        return False
    for k in ("overzicht", "inleiding", "aanpak_invulling",
              "doelgroep", "kortste_omschrijving"):
        if not str(inp.get(k, "")).strip():
            return False
    mod = inp.get("modules") or {}
    if not (isinstance(mod, dict) and mod.get("modules")):
        return False
    return bool(inp.get("doelen"))


# ---------------------------------------------------------------------------
# 8. ASSEMBLAGE (LLM-secties + vaste template + catalogus -> volledig document)
# ---------------------------------------------------------------------------

def bepaal_titel(writer_out: dict, b: RewriteBriefing) -> str:
    """De titel in de nieuwe stijl: code eerst, schrijver alleen als vangnet.

    De mechanische vervanging dekt vrijwel alles ('Cursus XML' -> 'Training XML'). Loopt hij
    krom, dan mag de schrijver een alternatief leveren -- maar alleen als dat zelf geen
    verboden soortwoord bevat, anders wint de code alsnog.
    """
    voorstel = str(writer_out.get("nieuwe_titel", "") or "").strip()
    if voorstel and not checks.hard_fails(checks.check_soortwoorden({"nieuwe_titel": voorstel})):
        return voorstel
    return b.nieuwe_titel


def assemble_document(writer_out: dict, b: RewriteBriefing, titels: list[str],
                      groepen: list[dict] | None = None) -> dict:
    """Bouwt het complete tien-kopjes-document; vaste teksten door de code ingevoegd."""
    invulling = (sjabloon.schoon_invulling(writer_out.get("aanpak_invulling", ""))
                 or sjabloon.AANPAK_FALLBACK)
    voorkennis = str(writer_out.get("voorkennis", "") or "").strip() or sjabloon.VOORKENNIS_FALLBACK
    titel = bepaal_titel(writer_out, b)
    return {
        "titel": titel,
        "overzicht": str(writer_out.get("overzicht", "")).strip(),
        "inleiding": str(writer_out.get("inleiding", "")).strip(),
        "modules": {
            "opening": sjabloon.modules_opening(titel, b.modules_nb),
            "modules": (writer_out.get("modules") or {}).get("modules", []),
        },
        "doelgroep": str(writer_out.get("doelgroep", "")).strip(),
        "voorkennis": voorkennis,
        # De nadruk in alinea 2 zit in de tekst zelf (aanhalingstekens, geen cursivering), dus
        # markdown en CMS-content dragen hier hetzelfde.
        "aanpak": (sjabloon.AANPAK_ALINEA_1.format(invulling=invulling)
                   + "\n\n" + sjabloon.AANPAK_ALINEA_2),
        "doelen": {"intro": sjabloon.DOELEN_INTRO, "bullets": writer_out.get("doelen", [])},
        "vervolgstappen": {
            "alineas": [sjabloon.VERVOLG_ALINEA_1, sjabloon.VERVOLG_ALINEA_2],
            "titels": titels,
            "groepen": groepen or [],
            "afsluiter": sjabloon.VERVOLG_AFSLUITER,
        },
        "kortste_omschrijving": str(writer_out.get("kortste_omschrijving", "")).strip(),
        "certificatie": sjabloon.CERTIFICATIE,
    }


def build_check_input(writer_out: dict, titels: list[str], titel: str = "",
                      groepen: list[dict] | None = None) -> dict:
    """Platte structuur voor rewrite_checks (op de door de LLM geschreven velden).

    De groepen komen uit de retrieval en niet van de schrijver; ze gaan mee zodat
    `check_vervolgstappen` kan zien of elke intro genoeg titels aankondigt.
    """
    return {
        "vervolgstappen_groepen": groepen or [],
        "overzicht": writer_out.get("overzicht"),
        "inleiding": writer_out.get("inleiding"),
        "modules": writer_out.get("modules"),
        "aanpak_invulling": writer_out.get("aanpak_invulling"),
        "doelgroep": writer_out.get("doelgroep"),
        "voorkennis": writer_out.get("voorkennis"),
        "doelen": writer_out.get("doelen"),
        "vervolgstappen_titels": titels,
        "kortste_omschrijving": writer_out.get("kortste_omschrijving"),
        "nieuwe_titel": titel,
    }


def build_check_ctx(b: RewriteBriefing, catalog: list[dict] | None) -> dict:
    """De context voor `check_rewrite`, op één plek zodat de twee aanroepers niet uiteenlopen.

    `rewrite_one` en `hergenereer_kopje` bouwden dit allebei zelf, en toen `acties` erbij kwam
    was dat meteen een plek waar de ene aanroeper een check kon draaien die de andere niet had.

    De acties gaan kaal mee, zonder de reviewer-voorwaarde: `check_actie_escalatie` kijkt
    alleen naar het werkwoord van de actie zelf.
    """
    return {"catalog_titles": catalog_titles(catalog) if catalog else None,
            "naam": b.nieuwe_titel, "dagen": b.dagen,
            "acties": [x.actie for x in b.goedgekeurd]}


def render_document(doc: dict, titel: str = "") -> str:
    """Leesbare weergave met de kopstructuur van het template (kop 1/2/3)."""
    return uit.render_markdown(doc, titel)


# ---------------------------------------------------------------------------
# 9. ORCHESTRATIE (write -> code-check -> judge -> revisie/route)
# ---------------------------------------------------------------------------

@dataclass
class RewriteResult:
    training_id: Any
    titel: str                        # de nieuwe titel; nooit een cursus of opleiding
    status: str                       # approved | human-queue | overgenomen | error
    reden: str = ""
    document: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    # Dezelfde flags, maar gebundeld per tier en ontdubbeld (`checks.per_tier`). `flags`
    # blijft het kale spoor voor de JSON; dit is wat de reviewer in zijn kolom leest, en dat
    # is een andere vraag -- zie de tier-tabel in rewrite_checks.py. Leeg = alles hoog.
    flags_tier: dict[str, list[str]] = field(default_factory=dict)
    judgment: dict = field(default_factory=dict)
    thin: bool = False
    toegepaste_acties: list[str] = field(default_factory=list)
    oude_titel: str = ""
    writer_out: dict = field(default_factory=dict)   # nodig om één kopje te hergenereren
    # Onder welk regime is dit resultaat tot stand gekomen? Zonder deze drie is `approved`
    # een status zonder betekenis zodra de spec of de modus verschuift. `goud_voorbeelden`
    # hoort in datzelfde rijtje: de few-shot vormt de output net zo goed als de spec, en de
    # selectie verandert zodra `promoveer_naar_goud()` draait.
    modus: str = MODUS_DEFAULT
    modus_voorstel: str = ""
    spec_versie: str = ""
    goud_voorbeelden: list[str] = field(default_factory=list)
    # Wat er per schrijverspoging gebeurde: `onvolledig` / `code-check` / het judge-verdict,
    # met de notities die terug naar de schrijver gingen. Zonder dit bewaart `<id>.json`
    # alleen het LAATSTE oordeel, en dan is achteraf niet te zien of de judge drie keer
    # dezelfde klacht had (dan helpt een ronde erbij) of elke ronde een nieuwe (dan is het
    # whack-a-mole en helpt hij niet). Dat is precies de vraag waar `MAX_REVISIONS` op staat.
    rondes: list[dict] = field(default_factory=list)
    # Wandkloktijd van deze training. Meetkolom, geen reviewwerk: `TIJDSBUDGET` staat op een
    # schatting zolang niemand weet hoe lang een training normaal duurt.
    seconden: float = 0.0
    # Wannéér draaide deze training, en waar ging de tijd heen? Zonder `gestart_op` is de
    # volgorde van een run achteraf alleen nog uit de mtimes van de artefacten te
    # reconstrueren, en die verschuiven zodra een training opnieuw draait -- precies de
    # trainingen waar het om gaat. `fout_soort` (alleen bij `error`) is de sleutel waarop je
    # groepeert, `storingen` het spoor uit `Storingsspoor.als_dict()`.
    gestart_op: str = ""
    fout_soort: str = ""
    storingen: dict = field(default_factory=dict)


def bepaal_vervolgstappen(client, b: RewriteBriefing, catalog: list[dict],
                          boom: dict | None = None) -> tuple[list[str], list[dict]]:
    """Twee trappen -> (platte titellijst voor de check, groepen voor de weergave).

    Trap 1 is Python en kost niets; trap 2 stuurt alleen de shortlist naar een klein model,
    nooit de hele catalogus. Levert trap 2 niets bruikbaars, dan valt het terug op de
    shortlist zelf -- dan staan er nog steeds echte catalogustitels in de tekst.

    De boom zoekt op de oude titel: dat is de titel die in de catalogus en dus in de
    taxonomie staat. De nieuwe titel gaat wel naar het model, dat is de tekst-context.
    """
    shortlist = shortlist_vervolgtrainingen(catalog, b.titel, b.kern, b.training_id, boom=boom)
    if not shortlist:
        return [], []
    groepen = kies_vervolgtrainingen(client, b.nieuwe_titel, b.kern, b.persona, shortlist,
                                     boom=boom, oude_titel=b.titel)
    if groepen:
        return [t for g in groepen for t in g["titels"]], groepen
    return [e["titel"] for e in shortlist[:N_VERVOLG]], []


def _reden_uit_revisies(judgment: dict) -> str:
    """De reden voor de human-queue als de judge tot het eind `needs-revision` bleef zeggen.

    `human_reden` is dan leeg: dat veld vult de judge alleen als hij zélf naar de mens
    routeert. Zonder deze terugval leest de reviewer "judge: needs-revision na max revisies"
    [37 tekens] over precies de trainingen waar tweemaal herschrijven niet hielp, terwijl het
    echte oordeel in `revisie_notities` staat en concreet is ("module 4 en 5 overlappen ...").
    Gemeten over batch 1: 5 van de 8 human-queue-rijen hadden alleen die 37 tekens.

    Regelovergangen en geen scheidingsteken: `_review_blok` in rewrite_output.py maakt van
    elke regel een alinea in het doc.
    """
    notities = [str(n).strip() for n in judgment.get("revisie_notities") or [] if str(n).strip()]
    if not notities:
        return "judge: needs-revision na max revisies"
    return ("Judge bleef na de maximale revisies bij needs-revision:\n"
            + "\n".join(f"- {n}" for n in notities))


def _mislukte_training(b: RewriteBriefing, fout: BaseException,
                       verbose: bool = True) -> RewriteResult:
    """Een uitzondering tijdens één training -> een `error`-rij, zodat de batch doorloopt.

    Zonder deze route kost een fout in training 1 alle 46: `herschreven.xlsx` wordt pas ná
    de lus geschreven, dus er staat daarna geen enkele rij op schijf. Gemeten bij batch 1,
    waar de SDK op de retry van de judge een ValueError gooide.

    De status `error` bestond al voor mislukte scoring en gedraagt zich hier hetzelfde: geen
    document, dus geen cms-rij en geen markdown, wél een JSON en een rij in het review-blad.
    `modus` en `spec_versie` komen uit de briefing, net als op de route_out-route in
    `rewrite_one`; anders leest de reviewer `volledig` bij een training die op `stijl` stond.

    `except Exception` en niet `BaseException` bij de aanroepers: Ctrl-C hoort de batch wél
    te stoppen, en niet 46 keer een error-rij te schrijven.

    De traceback gaat naar stderr en niet naar het sheet: de kolom `reden` moet één regel
    blijven, maar een gesmoorde uitzondering zonder spoor is niet te repareren.
    """
    if verbose:
        print(f"  FOUT bij training {b.training_id} ({b.titel[:40]}):", file=sys.stderr)
        traceback.print_exception(type(fout), fout, fout.__traceback__, file=sys.stderr)
    reden = " ".join(f"{type(fout).__name__}: {fout}".split())
    return RewriteResult(b.training_id, b.nieuwe_titel, "error",
                         reden=reden[:200], thin=b.thin, oude_titel=b.titel,
                         modus=b.modus, modus_voorstel=b.modus_voorstel,
                         spec_versie=spec_versie(), fout_soort=_foutsoort(fout))


def _pogingen_op(b: RewriteBriefing, laatste_beoordeeld: dict | None, laatste_schrijver: dict,
                 rondes: list[dict], toegepast: list[str], titels: list[str],
                 groepen: list[dict]) -> RewriteResult:
    """Alle schrijverspogingen op, zonder dat de laatste ronde een oordeel haalde.

    De oude versie gaf hier alleen `document` en `judgment` mee en verder een vaste reden van
    35 tekens. Dat is duurder dan het lijkt: `writer_out` bleef leeg (en juist dat veld heeft
    `hergenereer_kopje` nodig), `flags`/`flags_tier` bleven leeg (dus de opmerking bij het
    Drive-doc was leeg), en `_reden_uit_revisies` werd niet gebruikt terwijl het oordeel er
    gewoon lag. Batch 2 leverde er twee: 422 kreeg 1280 s en een reviewrij zonder één concreet
    woord, 482 kreeg 460 s en helemaal niets op schijf.

    Drie trappen, van goed naar slecht. Wat er ligt bepaalt welke:

    1. **een beoordeeld concept uit een eerdere ronde.** De laatste schrijverspoging viel op een
       code-check, maar een ronde ervoor had de judge er al iets van gevonden. Dat concept gaat
       naar de mens met het oordeel van de judge erbij -- inhoudelijk het beste dat deze
       training heeft opgeleverd;
    2. **alleen een volledige schrijverspoging die HARD viel.** Nooit beoordeeld, dus geen
       oordeel om bij te zetten, maar wel leesbaar: er komt een document, een markdown en een
       doc op Drive, en de code-check-fouten staan als flag in de opmerking. Een reviewer kan
       daar iets mee; met een lege map niet;
    3. **niets.** Trap 2 vangt élke ronde die een volledige `submit_rewrite` opleverde, dus hier
       komt alleen een training waarvan de schrijver vier keer op rij een onvolledig tool-antwoord
       gaf. Dat is een ander soort probleem dan een tekst die de checks niet haalt, en de reden
       zegt dat ook: hier valt geen kopje te repareren, hier is niets geschreven.
    """
    gedeeld_altijd = dict(toegepaste_acties=toegepast, oude_titel=b.titel, rondes=rondes,
                          modus=b.modus, modus_voorstel=b.modus_voorstel,
                          spec_versie=spec_versie(), goud_voorbeelden=actieve_goud_voorbeelden())
    if laatste_beoordeeld:
        gedeeld = laatste_beoordeeld["gedeeld"]
        gedeeld["rondes"] = rondes          # ook de ronde(s) ná dit concept horen in het spoor
        reden = (f"De laatste schrijverspoging haalde de code-check niet; dit is het concept van "
                 f"ronde {laatste_beoordeeld['ronde']}.\n{laatste_beoordeeld['reden']}")
        return RewriteResult(b.training_id, laatste_beoordeeld["titel"], HUMAN_QUEUE,
                             reden=reden, thin=b.thin, **gedeeld)

    if laatste_schrijver:
        issues, hard = laatste_schrijver["issues"], laatste_schrijver["hard"]
        # De HARD-issues gaan mee de flag-kolom in, en altijd in de tier `hoog`: `TIER_PER_CODE`
        # is gemaakt om FLAGS te sorteren, dus een code die daar op `mechanisch` staat zou een
        # harde fout uit de kolom houden die er juist om vraagt. Zelfde richting als op het
        # `overnemen`-pad, waar HARD-issues ook gewoon in de kolom belanden.
        tiers = checks.per_tier([i for i in issues if i.severity != checks.HARD])
        tiers[checks.TIER_HOOG] = ([r for lijst in checks.per_tier(hard).values() for r in lijst]
                                   + tiers[checks.TIER_HOOG])
        reden = ("Geen concept dat de code-check haalt, na max pogingen. Dit is de laatste "
                 f"poging (ronde {laatste_schrijver['ronde']}); deze fouten staan er nog in:\n"
                 + "\n".join(f"- {i}" for i in hard))
        return RewriteResult(
            b.training_id, laatste_schrijver["titel"], HUMAN_QUEUE, reden=reden, thin=b.thin,
            document=assemble_document(laatste_schrijver["writer_out"], b, titels, groepen),
            flags=[str(i) for i in hard] + [str(i) for i in checks.flags(issues)],
            flags_tier=tiers, writer_out=laatste_schrijver["writer_out"], **gedeeld_altijd)

    return RewriteResult(b.training_id, b.nieuwe_titel, HUMAN_QUEUE, thin=b.thin,
                         reden=f"geen valide concept na max pogingen: alle {len(rondes)} pogingen "
                               f"leverden een onvolledige submit_rewrite, er is geen tekst",
                         **gedeeld_altijd)


def _begin_meting() -> tuple[str, float]:
    """Wandkloktijd + monotone start van één training, en een schoon storingsspoor.

    Twee klokken, want ze beantwoorden verschillende vragen: `datetime` zegt wannéér (en dus
    of twee fouten bij elkaar in de tijd liggen), `monotonic` hoe lang (en die verspringt niet
    bij een zomertijd of een NTP-correctie midden in een batch van drie uur).
    """
    begin_spoor()
    return datetime.now().isoformat(timespec="seconds"), time.monotonic()


def _stempel_meting(res: RewriteResult, gestart_op: str, start: float) -> RewriteResult:
    """De drie meetvelden op één resultaat, langs elk pad hetzelfde.

    Eén functie in plaats van drie keer dezelfde toekenning, om dezelfde reden als
    `build_check_ctx`: `rewrite_one`, de overnemen-lus en de herschrijflus stempelen alle drie,
    en drie kopieën is meteen de plek waar er eentje achterloopt. Precies wat er bij
    `_pogingen_op` gebeurde, waar één uitgang `writer_out` en de flags niet meekreeg.
    """
    res.gestart_op = gestart_op
    res.seconden = round(time.monotonic() - start, 1)
    res.storingen = huidig_spoor().als_dict()
    return res


def rewrite_one(client, b: RewriteBriefing, catalog: list[dict],
                boom: dict | None = None) -> RewriteResult:
    """Schrijver -> code-check -> judge -> revisie of route, binnen één tijdsbudget.

    Het budget staat hier en niet in `rewrite_file`, zodat élke ingang begrensd is: de batch,
    de CLI en de losse notebook-cel van sectie 5. Loopt hij over, dan gooit `_call_tool` een
    `TijdOverschreden` en is dat voor de aanroeper een gewone fout -- één `error`-rij, en de
    volgende training gaat gewoon door.
    """
    gestart_op, start = _begin_meting()
    with tijdsbudget():
        res = _schrijf_en_beoordeel(client, b, catalog, boom)
    # De batch meet zelf opnieuw (daar telt ook een mislukte training mee, en die levert geen
    # resultaat op om het getal in te zetten); dit is voor de losse aanroepen.
    return _stempel_meting(res, gestart_op, start)


def _schrijf_en_beoordeel(client, b: RewriteBriefing, catalog: list[dict],
                          boom: dict | None = None) -> RewriteResult:
    # harde routes eruit (onbruikbaar, of een beslissing waar de reviewer nog niet aan toe is)
    route = b.route_out
    if route:
        return RewriteResult(b.training_id, b.nieuwe_titel, HUMAN_QUEUE, reden=route,
                             thin=b.thin, oude_titel=b.titel, modus=b.modus,
                             modus_voorstel=b.modus_voorstel, spec_versie=spec_versie())

    # audit-spoor: welke actualiseringen zijn meegegaan, en onder welke voorwaarde
    toegepast = [f"{x.nr}. {x.actie}" + (f" [{x.voorwaarde}]" if x.voorwaarde else "")
                 for x in b.goedgekeurd]

    titels, groepen = bepaal_vervolgstappen(client, b, catalog, boom)
    ctx = build_check_ctx(b, catalog)
    writer_system = build_writer_system()
    base_user = build_writer_user(b)

    notes: list[str] = []
    # Alle HARD-boodschappen die deze training ooit heeft gekregen, en dat is nodig omdat een
    # revisie hier geen reparatie is maar een HERGENERATIE: `_call_tool` begint elke poging met
    # een schone `messages`, dus de schrijver ziet zijn vorige concept niet en leidt elke
    # eerdere correctie opnieuw af. Training 422 loste "professional(s)" in ronde 1 op in de
    # Modules en zette het in ronde 4 terug in de Inleiding -- de laatste ronde, dus dat kostte
    # het hele concept. Alleen HARD-checks: die zijn regels en blijven gelden. De notities van
    # de judge niet, want die zijn positioneel ("module 4 en 5 overlappen") en slaan nergens
    # meer op zodra de schrijver opnieuw begint.
    hard_gezien: list[str] = []
    laatste_schrijver: dict = {}     # laatste volledige submit_rewrite, ook als die HARD viel
    laatste_beoordeeld: dict | None = None   # laatste concept dat de judge echt heeft gezien
    # Eén regel per schrijverspoging, ook de rondes die de judge nooit haalden. Zie
    # `RewriteResult.rondes`: dit is het spoor waarop `MAX_REVISIONS` wordt bijgesteld.
    rondes: list[dict] = []
    for attempt in range(MAX_REVISIONS + 1):
        user_text = base_user
        # De staande regels vóór de HERSTEL-lijst, zodat de klacht van deze ronde het laatste
        # is wat de schrijver leest. Wat al in `notes` staat komt hier niet nog eens langs.
        eerder = [m for m in hard_gezien if m not in notes]
        if eerder:
            user_text += ("\n\n---\nEERDER AL GECORRIGEERD in deze training, laat het niet "
                          "terugkomen (ook niet in een ander kopje):\n" + "\n".join(eerder))
        if notes:
            user_text += "\n\n---\nHERSTEL:\n" + "\n".join(notes)
        writer_out = _call_tool(client, writer_system, user_text, [SUBMIT_REWRITE], "submit_rewrite")
        if not rewrite_input_complete(writer_out):
            notes = ["De submit_rewrite-output was onvolledig; lever alle verplichte kopjes."]
            rondes.append({"ronde": attempt + 1, "uitkomst": "onvolledig", "notities": []})
            continue

        titel = bepaal_titel(writer_out, b)
        issues = checks.check_rewrite(
            build_check_input(writer_out, titels, titel, groepen), ctx)
        hard = checks.hard_fails(issues)
        if hard:
            boodschappen = [str(i) for i in hard]
            notes = ["Los deze code-check fouten op:"] + boodschappen
            hard_gezien += [m for m in boodschappen if m not in hard_gezien]
            # Bewaren ook al valt hij: de output is compleet (`rewrite_input_complete`) en
            # daarmee genoeg om er beneden een leesbaar concept van te maken. Zonder dit hield
            # 482 vier rondes lang niets over: writer_out {}, document {}, geen markdown, geen
            # doc op Drive, en een human-queue-rij waar een reviewer niets mee kan.
            laatste_schrijver = {"writer_out": writer_out, "titel": titel, "issues": issues,
                                 "hard": hard, "ronde": attempt + 1}
            rondes.append({"ronde": attempt + 1, "uitkomst": "code-check",
                           "notities": boodschappen})
            continue

        document = assemble_document(writer_out, b, titels, groepen)
        flags = [str(i) for i in checks.flags(issues)]
        flags_tier = checks.per_tier(checks.flags(issues))

        judgment = judge_document(client, b, document)
        verdict = judgment.get("verdict", HUMAN_QUEUE)
        rondes.append({"ronde": attempt + 1, "uitkomst": verdict,
                       "notities": [str(n) for n in judgment.get("revisie_notities") or []]})
        gedeeld = dict(document=document, flags=flags, flags_tier=flags_tier, judgment=judgment,
                       toegepaste_acties=toegepast, oude_titel=b.titel, writer_out=writer_out,
                       modus=b.modus, modus_voorstel=b.modus_voorstel, rondes=list(rondes),
                       spec_versie=spec_versie(), goud_voorbeelden=actieve_goud_voorbeelden())
        if verdict == APPROVED:
            return RewriteResult(b.training_id, titel, APPROVED, reden="",
                                 thin=b.thin or judgment.get("feitgetrouw", {}).get("thin", False),
                                 **gedeeld)
        # Vasthouden vóór de `continue`: valt de LAATSTE ronde straks op een code-check, dan is
        # dit het beste dat deze training heeft opgeleverd en gaat het alsnog naar de mens.
        laatste_beoordeeld = {"ronde": attempt + 1, "titel": titel, "gedeeld": dict(gedeeld),
                              "reden": judgment.get("human_reden") or _reden_uit_revisies(judgment)}
        if verdict == NEEDS_REVISION and attempt < MAX_REVISIONS:
            notes = ["Judge-revisie:"] + list(judgment.get("revisie_notities", []))
            continue
        # human-queue of revisies op -> mens
        return RewriteResult(b.training_id, titel, HUMAN_QUEUE, reden=laatste_beoordeeld["reden"],
                             thin=b.thin, **gedeeld)

    return _pogingen_op(b, laatste_beoordeeld, laatste_schrijver, rondes, toegepast,
                        titels, groepen)


# ---------------------------------------------------------------------------
# 9b. ÉÉN KOPJE OPNIEUW (retry, of gericht bijsturen met een opmerking)
# ---------------------------------------------------------------------------

# Welke checks horen bij welk kopje: bij een gerichte hergeneratie willen we niet dat een
# ander kopje de revisie-lus laat vastlopen.
CHECKS_PER_KOPJE = {
    "overzicht": checks.check_overzicht,
    "inleiding": checks.check_inleiding,
    "modules": checks.check_modules,
    "doelgroep": checks.check_doelgroep,
    "voorkennis": checks.check_voorkennis,
    "doelen": checks.check_doelen,
    "kortste_omschrijving": checks.check_kortste_omschrijving,
}
# `notities` is geen kopje maar een signaal van de schrijver -- niet los te hergenereren.
HERGENEREERBAAR = tuple(k for k in SUBMIT_REWRITE["input_schema"]["properties"]
                        if k != "notities")


def build_kopje_tool(kopje: str) -> dict:
    """Tool voor één kopje, afgeleid uit `SUBMIT_REWRITE`.

    Eén bron voor de veldbeschrijvingen: past de schrijfspec zich aan, dan verandert deze
    tool automatisch mee.
    """
    if kopje not in HERGENEREERBAAR:
        raise KeyError(f"onbekend kopje {kopje!r}; kies uit {sorted(HERGENEREERBAAR)}")
    schema = SUBMIT_REWRITE["input_schema"]["properties"][kopje]
    return {
        "name": "submit_kopje",
        "description": f"Lever alleen het kopje '{kopje}' opnieuw. De rest van de training "
                       f"blijft ongewijzigd.",
        "input_schema": {"type": "object", "properties": {kopje: schema}, "required": [kopje]},
    }


def _writer_out_uit_json(resultaat: dict) -> dict:
    """`writer_out` uit een per-training-JSON, met reconstructie voor oudere bestanden."""
    writer_out = resultaat.get("writer_out")
    if writer_out:
        return dict(writer_out)
    doc = resultaat.get("document") or {}
    # Oudere bestanden hebben alleen het samengestelde document. Alles is terug te halen
    # behalve aanpak_invulling -- die zit ingebakken in de vaste alinea; vandaar de regex.
    aanpak = str(doc.get("aanpak", "") or "")
    prefix = sjabloon.AANPAK_ALINEA_1.split("{invulling}")[0]
    invulling = ""
    if aanpak.startswith(prefix):
        # `schoon_invulling` ook hier: een document dat al "ervaar je hoe hoe ..." bevat levert
        # anders de invulling "hoe ..." op, die er bij het opnieuw samenstellen ongewijzigd weer
        # achter komt. Zonder deze regel repareert een hergeneratie de fout dus nooit.
        invulling = sjabloon.schoon_invulling(
            aanpak[len(prefix):].split("\n\n")[0].rstrip(". "))
    return {
        "overzicht": doc.get("overzicht", ""),
        "inleiding": doc.get("inleiding", ""),
        "modules": {"modules": (doc.get("modules") or {}).get("modules", [])},
        "doelgroep": doc.get("doelgroep", ""),
        "voorkennis": doc.get("voorkennis", ""),
        "aanpak_invulling": invulling,
        "doelen": (doc.get("doelen") or {}).get("bullets", []),
        "kortste_omschrijving": doc.get("kortste_omschrijving", ""),
        "nieuwe_titel": doc.get("titel", ""),
    }


def hergenereer_kopje(client, b: RewriteBriefing, resultaat: dict, kopje: str,
                      comment: str = "", *, catalog: list[dict] | None = None,
                      boom: dict | None = None, judge: bool = True) -> RewriteResult:
    """Genereert één kopje opnieuw en bouwt het document opnieuw op.

    Zonder `comment` is dit een gewone retry. Met `comment` krijgt de schrijver de
    aanwijzing van de reviewer erbij -- de rest van de training gaat als context mee, zodat
    het nieuwe kopje aansluit op wat er al staat.
    """
    writer_out = _writer_out_uit_json(resultaat)
    document = resultaat.get("document") or {}
    vervolg = document.get("vervolgstappen") or {}
    titels = list(vervolg.get("titels") or [])
    # Snoeien vóór hergebruik: een document van vóór deze regel kan nog een groep met één
    # titel bevatten, en die zou hier ongemerkt weer meegaan. Valt er een groep af, dan
    # lopen de titels mee terug -- ze staan al in de overgebleven groepen.
    groepen = snoei_groepen(list(vervolg.get("groepen") or []))
    if groepen:
        titels = [t for g in groepen for t in g["titels"]]
    if not titels and catalog:
        titels, groepen = bepaal_vervolgstappen(client, b, catalog, boom)

    ctx = build_check_ctx(b, catalog)
    huidig = json.dumps(writer_out.get(kopje), ensure_ascii=False, indent=2)
    opdracht = [
        f"Schrijf ALLEEN het kopje '{kopje}' opnieuw. Alle andere kopjes blijven zoals ze zijn;",
        "gebruik ze als context zodat je versie erop aansluit.",
        "",
        f"HUIDIGE VERSIE VAN '{kopje}':\n{huidig}",
        "",
        f"VOLLEDIGE HUIDIGE TRAINING:\n{uit.render_markdown(document, b.nieuwe_titel)}",
    ]
    if comment.strip():
        opdracht += ["", f"AANWIJZING VAN DE REVIEWER, dit moet er anders:\n{comment.strip()}"]
    base_user = build_writer_user(b) + "\n\n---\n" + "\n".join(opdracht)

    tool = build_kopje_tool(kopje)
    check = CHECKS_PER_KOPJE.get(kopje)
    notes: list[str] = []
    for _ in range(MAX_REVISIONS + 1):
        user_text = base_user if not notes else base_user + "\n\n---\nHERSTEL:\n" + "\n".join(notes)
        out = _call_tool(client, build_writer_system(), user_text, [tool], "submit_kopje")
        if not isinstance(out, dict) or kopje not in out:
            notes = [f"De output miste het veld '{kopje}'; roep submit_kopje correct aan."]
            continue

        kandidaat = dict(writer_out, **{kopje: out[kopje]})
        titel = bepaal_titel(kandidaat, b)
        issues = (check(kandidaat, ctx) if check else []) + checks.check_soortwoorden(
            {kopje: out[kopje], "nieuwe_titel": titel})
        hard = checks.hard_fails(issues)
        if hard:
            notes = ["Los deze code-check fouten op:"] + [str(i) for i in hard]
            continue
        writer_out = kandidaat
        break
    else:
        return RewriteResult(b.training_id, b.nieuwe_titel, HUMAN_QUEUE,
                             reden=f"kopje '{kopje}' bleef falen na max pogingen",
                             document=document, oude_titel=b.titel, writer_out=writer_out)

    nieuw_document = assemble_document(writer_out, b, titels, groepen)
    alle_issues = checks.check_rewrite(
        build_check_input(writer_out, titels, bepaal_titel(writer_out, b), groepen), ctx)
    flags = [str(i) for i in checks.flags(alle_issues)]
    judgment = judge_document(client, b, nieuw_document) if judge else {}
    status = judgment.get("verdict", APPROVED) if judge else APPROVED
    return RewriteResult(
        b.training_id, bepaal_titel(writer_out, b),
        APPROVED if status == APPROVED else HUMAN_QUEUE,
        reden="" if status == APPROVED else judgment.get("human_reden", status),
        document=nieuw_document, flags=flags,
        flags_tier=checks.per_tier(checks.flags(alle_issues)), judgment=judgment, thin=b.thin,
        toegepaste_acties=list(resultaat.get("toegepaste_acties") or []),
        oude_titel=b.titel, writer_out=writer_out,
        spec_versie=spec_versie(), goud_voorbeelden=actieve_goud_voorbeelden())


def judge_document(client, b: RewriteBriefing, document: dict) -> dict:
    """Eén judge-oordeel, met de vorm van het antwoord afgedwongen.

    Het tool-schema garandeert de vorm niet: bij het meten van de spreiding leverde één van
    de runs `feitgetrouw` als string in plaats van als object. Ongefilterd loopt dat
    verderop stuk op `judgment["feitgetrouw"].get("thin")` -- midden in een batch, ná de
    dure schrijfcall. Een verkeerd gevormd blok maken we daarom leeg; de rest van het
    oordeel blijft bruikbaar en `judge_vorm` maakt zichtbaar dat er iets misging.
    """
    system = build_judge_system()
    user_text = build_judge_user(b, document)
    out = _call_tool(client, system, user_text, [SUBMIT_JUDGMENT], "submit_judgment")
    if not isinstance(out, dict) or "verdict" not in out:
        return {"verdict": HUMAN_QUEUE, "human_reden": "judge leverde geen bruikbaar oordeel"}
    afwijkend = [k for k in ("feitgetrouw", "persona_toon", "secties")
                 if k in out and not isinstance(out[k], dict)]
    for k in afwijkend:
        out[k] = {}
    if afwijkend:
        out["judge_vorm"] = f"niet-conform veld vervangen door leeg object: {', '.join(afwijkend)}"
    return out


# ---------------------------------------------------------------------------
# 10. I/O (scored + source joinen; per-training JSON + samenvattings-xlsx)
# ---------------------------------------------------------------------------

# Kolommen die `modus_voorstellen()` (sectie 3b) aan het scoresheet toevoegt, plus de vier
# kolommen die een reviewer met de hand invult. Ontbreken ze, dan werkt de pijplijn gewoon
# door -- maar met de defaults, en die zijn niet neutraal: `modus` valt terug op `volledig`
# en `modules_nb` op `stabiel`. Dat is precies wat er gebeurde toen de batch nog op het ruwe
# scoresheet draaide in plaats van op `scoresheet_met_modus.xlsx`: drie van de vier
# trainingen werden volledig herschreven terwijl de scan `format` voorstelde, en een
# training met `modules_nb_voorstel = actueel` kreeg toch de stabiele NB.
_PIJPLIJN_KOLOMMEN: tuple[str, ...] = ("modus_voorstel", "modules_nb_voorstel")
# `rewrite_guidance` en niet `guidance_reviewer`: de vrije aanwijzing is sinds augustus 2026 één
# kolom die de scorer vult en de reviewer bijstelt (zie `guidance_definitief`). Hij staat vooraan
# in het gedeelde reviewsheet, dus in dezelfde ronde als `actie_besluit` en `kern_reviewer` --
# `guidance_reviewer` ontstond pas in sectie 3b en kwam daardoor nooit bij het reviewteam terecht.
_REVIEWER_KOLOMMEN: tuple[str, ...] = ("modus_reviewer", "kern_reviewer",
                                       "rewrite_guidance", "modules_nb_reviewer")


def _waarschuw_ontbrekende_kolommen(df, path: str) -> None:
    """Meld naar stderr dat dit sheet de uitkomst van sectie 3b mist.

    Bewust een waarschuwing en geen exception: een sheet zonder deze kolommen is een geldig
    startpunt (dat is precies wat sectie 3b zelf inleest). Wat niet mag, is dat het herschrijven
    er stil op doordraait -- de terugval is onzichtbaar in de output en kost een hele batch.
    """
    mist_pijplijn = [k for k in _PIJPLIJN_KOLOMMEN if k not in df.columns]
    mist_reviewer = [k for k in _REVIEWER_KOLOMMEN if k not in df.columns]
    if not mist_pijplijn and not mist_reviewer:
        return
    regels = [f"LET OP: {os.path.basename(path)} mist kolommen uit de pijplijn."]
    if mist_pijplijn:
        regels.append(f"  ontbreekt: {', '.join(mist_pijplijn)}")
        regels.append("  -> elke training valt terug op modus 'volledig' en NB 'stabiel'; "
                      "het voorstel uit sectie 3b wordt genegeerd.")
    if mist_reviewer:
        regels.append(f"  ontbreekt: {', '.join(mist_reviewer)}")
        regels.append("  -> handmatige correcties van de reviewer worden genegeerd.")
    regels.append("  Draai sectie 3b (`modus_voorstellen`) en lees het resultaatsheet "
                  "(`scoresheet_met_modus.xlsx`) in plaats van het ruwe scoresheet.")
    print("\n".join(regels), file=sys.stderr)


def _load_scored(path: str, waarschuw: bool = True):
    """Scoresheet inlezen en normaliseren.

    `waarschuw=False` alleen voor `modus_voorstellen()`: dat is de stap die de ontbrekende
    kolommen juist gaat máken, dus daar is hun afwezigheid het normale geval.
    """
    import pandas as pd
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]
    # Zelfde normalisatie als de besluitenlaag, zodat een handgemaakt prioriteitssheet met
    # `id`/`name` in beide stappen werkt en niet halverwege de pijplijn omvalt. Dat geldt ook
    # voor de id-kolom zelf: een als decimaal gelezen `2.347` hoort hier te stranden en niet
    # verderop als een inhoudelijk `volledig`-advies op te duiken.
    df = bes.normaliseer_scored_kolommen(df)
    if waarschuw:
        _waarschuw_ontbrekende_kolommen(df, path)
    return bes.normaliseer_training_ids(df, path)


def load_source(source_path: str) -> tuple[dict, dict]:
    """Bronsheet -> ({id: rij}, kolomnamen). Levert de content-JSON per training."""
    src_df, cols = read_source_input(source_path)
    src_by_id = {row[cols["id"]] if cols["id"] else None: row
                 for _, row in src_df.iterrows()}
    return src_by_id, cols


def build_briefing_for(scored_df, src_by_id: dict, cols: dict, training_id: Any,
                       besluiten_per_training: dict | None = None) -> RewriteBriefing:
    """Eén rij -> briefing, zonder de hele batch te draaien.

    Het notebook en `rewrite_file` gebruiken dezelfde functie, zodat wat je in een cel
    inspecteert precies is wat de batch verstuurt.
    """
    rijen = scored_df[scored_df["training_id"] == training_id]
    if rijen.empty:
        raise KeyError(f"training_id {training_id} staat niet in het scoresheet")
    scored_dict = {k: rijen.iloc[0][k] for k in scored_df.columns}
    src_row = src_by_id.get(training_id)
    content = parse_content(src_row[cols["content"]]) if src_row is not None else {}
    naam = str(scored_dict.get("titel", "") or "")
    if not naam and src_row is not None:
        naam = str(src_row[cols["name"]])
    return build_briefing(scored_dict, content, naam,
                          (besluiten_per_training or {}).get(training_id, []))


def build_briefing_for_id(scored_path: str, source_path: str, training_id: Any,
                          besluiten_path: str | None = None) -> RewriteBriefing:
    """Zelfde als build_briefing_for, maar vanaf de bestandspaden. Doet geen API-calls."""
    src_by_id, cols = load_source(source_path)
    per_training = bes.load_besluiten(besluiten_path) if besluiten_path else {}
    return build_briefing_for(_load_scored(scored_path), src_by_id, cols,
                              training_id, per_training)


def export_goud_corpus(source_path: str, out_dir: str, verbose: bool = True) -> int:
    """Schrijft de trainingen die al in de nieuwe stijl staan (`herschreven=1`) weg.

    Referentiemateriaal om spec en judge aan te kalibreren; niet om te herschrijven.
    """
    src_df, cols = read_source_input(source_path)
    if "herschreven" not in src_df.columns:
        if verbose:
            print("bronsheet heeft geen kolom 'herschreven' -> geen goud-corpus")
        return 0
    goud_dir = os.path.join(out_dir, "goud")
    os.makedirs(goud_dir, exist_ok=True)
    n = 0
    for _, row in src_df[src_df["herschreven"] == 1].iterrows():
        tid = row[cols["id"]]
        _schrijf_atomisch(
            os.path.join(goud_dir, f"{tid}.json"),
            lambda f, row=row, tid=tid: json.dump(
                {"training_id": tid, "titel": str(row[cols["name"]]),
                 "content": parse_content(row[cols["content"]])},
                f, ensure_ascii=False, indent=2, default=_json_default))
        n += 1
    if verbose:
        print(f"Goud-corpus: {n} trainingen in {goud_dir}/")
    return n


_LI_RE = re.compile(r"<li>(.*?)(?=<li>|</li>)", re.S)
_BLOK_TAG_RE = re.compile(r"<\s*(/?)\s*(ul|ol|li|h3)\b[^>]*>", re.I)


def _modules_uit_ul(html: str, titel: str = "") -> list[dict]:
    """Geneste <ul> -> [{titel, bullets}]; de vorm die `uit.render_modules` produceert.

    Loopt de tags langs met een diepteteller in plaats van met één reguliere expressie.
    Geneste lijsten zijn met een regex niet betrouwbaar te vangen -- dat is precies de
    valkuil waarom deze structuur eerder werd overgeslagen. Een diepteteller heeft dat
    probleem niet: hij leest de nesting zoals de browser hem leest.

    Alleen de standaardvorm wordt herkend. Modules die als <h3>-koppen zijn opgeschreven
    (32 van de 45 nog te herschrijven trainingen) leveren hier weinig of niets op, en dat is
    de bedoeling: die structuur is niet de standaard en moet dus herschikt worden. Zie
    `scan_vorm`, dat een onleesbare modulestructuur als ondergrens `format` behandelt.
    """
    from score_trainings import clean_text
    modules: list[dict] = []
    bullets: list[str] = []
    titel_buf: list[str] = []
    bullet_buf: list[str] = []
    ul_diepte = 0
    modus: str | None = None      # "titel" | "bullet" | None

    def sluit_bullet() -> None:
        nonlocal bullet_buf
        tekst = clean_text("".join(bullet_buf), titel).strip()
        bullet_buf = []
        if tekst:
            bullets.append(tekst)

    def sluit_module() -> None:
        nonlocal titel_buf, bullets
        sluit_bullet()
        naam = clean_text("".join(titel_buf), titel).strip()
        if naam or bullets:
            modules.append({"titel": naam, "bullets": bullets})
        titel_buf, bullets = [], []

    laatste = 0
    for m in _BLOK_TAG_RE.finditer(html or ""):
        tekst = (html or "")[laatste:m.start()]
        laatste = m.end()
        if modus == "titel":
            titel_buf.append(tekst)
        elif modus == "bullet":
            bullet_buf.append(tekst)

        sluit, naam = bool(m.group(1)), m.group(2).lower()
        if naam in ("ul", "ol"):
            if not sluit:
                ul_diepte += 1
                if ul_diepte >= 2:
                    modus = None          # de moduletitel is af; hierna komen de bullets
            else:
                if ul_diepte >= 2:
                    sluit_bullet()
                elif ul_diepte == 1:
                    sluit_module()
                ul_diepte = max(0, ul_diepte - 1)
                modus = None
        elif naam == "li":
            if sluit:
                if ul_diepte >= 2:
                    sluit_bullet()
                modus = None
            elif ul_diepte <= 1:
                sluit_module()
                modus = "titel"
            else:
                sluit_bullet()
                modus = "bullet"
        else:                              # <h3> hoort niet in de standaardvorm
            modus = None
    sluit_module()
    return modules


def modules_leesbaar(modules: list[dict]) -> bool:
    """Is dit een echte modulestructuur, of wat losse resten van een andere opmaak?

    Bewust streng. Voor de vormscan geldt: onbekend is niet hetzelfde als conform, dus bij
    twijfel schatten we omhoog en niet omlaag.
    """
    return len(modules) >= 2 and any(m.get("bullets") for m in modules)


def goud_naar_check_input(content: dict, titel: str = "") -> dict:
    """Bestaande CMS-content (HTML) -> de platte writer-vorm, zodat de checks erover kunnen.

    Gebruikt door `checks_over_goud` (hoe vaak faalt elke regel op het goud?), door
    `neem_over` (flags op een ongewijzigd doorgezette training) en door `scan_vorm` (de
    deterministische ondergrens van de herschrijfmodus).
    """
    from score_trainings import clean_text
    plat = {k: clean_text(v, titel) if isinstance(v, str) else v for k, v in content.items()}
    intro = plat.get("intro", "").replace(sjabloon.BEDRIJFSTRAINING_KOP, "")
    intro = intro.replace(sjabloon.BEDRIJFSTRAINING_TEKST, "").strip()
    doelen = [clean_text(m, titel) for m in _LI_RE.findall(content.get("objectives", "") or "")]
    return {
        "overzicht": plat.get("summary", ""),
        "inleiding": intro,
        "modules": {"modules": _modules_uit_ul(content.get("modules", "") or "", titel)},
        "doelgroep": plat.get("target_audience", ""),
        "voorkennis": plat.get("prior_knowledge", ""),
        "doelen": [d for d in doelen if d],
        "kortste_omschrijving": plat.get("summary_edudex", ""),
        "nieuwe_titel": titel,
    }


# Welke harde check-fails betekenen "de structuur moet op de schop" in plaats van "de
# formulering moet anders"? Dat onderscheid is precies de grens tussen `format` en `stijl`.
# Een ontbrekend kopje of een verkeerd aantal modules/bullets/doelen vraagt om herindelen;
# een missende openingszin, een verboden woord of een lengte-overschrijding vraagt alleen om
# andere zinnen. Codes komen uit rewrite_checks.py; verandert daar een regel van aard, dan
# hoort hij hier mee te verhuizen.
STRUCTUUR_CODES = frozenset({
    "ontbreekt",          # check_presence: het kopje is er niet
    "modules_aantal",     # niet 4-6 modules
    "bullets_aantal",     # niet 3-6 sub-bullets
    "bullets_variatie",   # overal hetzelfde aantal sub-bullets
    "aantal",             # check_doelen: niet 4-5 doelen
})


def _verouderde_vaste_tekst(content_bron: dict) -> list[str]:
    """Vaste teksten uit de vórige generatie van het template, gevonden in bestaande content.

    Nodig omdat `rewrite_checks` hier per definitie blind voor is: die kijkt alleen naar wat
    de schrijver levert, en vaste tekst levert de schrijver nooit. Een training die verder
    elke check haalt maar nog de oude Aanpak-alinea's draagt, zou dus als `overnemen` door de
    pipeline glippen en met verouderde boilerplate in het CMS belanden.
    """
    plat = " ".join(str(v) for v in (content_bron or {}).values() if isinstance(v, str))
    plat = re.sub(r"<[^>]+>", " ", plat)
    plat = html.unescape(plat)
    plat = re.sub(r"\s+", " ", plat)
    return [frag for frag in sjabloon.VERVALLEN_VASTE_TEKSTEN if frag in plat]


def scan_vorm(content_bron: dict, titel: str = "", dagen: int | None = None,
              verdict: str = "") -> dict:
    """Deterministische ONDERGRENS van de herschrijfmodus. Doet geen API-call.

    Wat deze scan kan en niet kan is de kern van het ontwerp. `rewrite_checks` vangt
    openingszinnen, lengtes, aantallen en verboden woorden -- daarmee is te bewijzen dat een
    tekst NIET voldoet, nooit dat hij wél voldoet. Of een zin het stijlregister volgt, of de
    causale constructie zichtbaar is, of een woord raak gekozen is: dat ziet code niet.

    Daarom is de uitkomst asymmetrisch. De scan legt een bodem en stelt NOOIT `overnemen`
    voor; die conclusie mag alleen uit `schat_modus` (die de spec echt leest) of van een mens
    komen. Zelfs een tekst die elke check haalt levert hier `stijl` op.
    """
    if not content_bron:
        # Nadrukkelijk niet "geen bron": dat een id niet joint is een fout in het scoresheet
        # en wordt in `modus_voorstellen` afgevangen voordat het hier als oordeel landt.
        return {"ondergrens": "volledig",
                "reden": "bronrij gevonden maar de content is leeg",
                "lege_kopjes": [], "harde_issues": [], "modules_leesbaar": False}
    if str(verdict or "").strip() == "onbruikbaar":
        return {"ondergrens": "volledig", "reden": "verdict onbruikbaar — te weinig bron",
                "lege_kopjes": [], "harde_issues": [], "modules_leesbaar": False}

    rw = goud_naar_check_input(content_bron, titel)
    leesbaar = modules_leesbaar((rw.get("modules") or {}).get("modules") or [])
    issues = checks.check_rewrite(rw, {"naam": titel, "dagen": dagen})
    hard = checks.hard_fails(issues)
    lege = [i.section for i in hard if i.code == "ontbreekt"]
    structuur = [i for i in hard if i.code in STRUCTUUR_CODES]
    verouderd = _verouderde_vaste_tekst(content_bron)

    if verouderd:
        # Vóór de structuurcheck: dit is een harde vaststelling en geen oordeel. De vaste
        # teksten zijn deterministisch, dus "format" is genoeg -- de code rendert ze opnieuw
        # en de schrijver hoeft de inhoud niet aan te raken.
        ondergrens = "format"
        reden = ("vaste sjabloonteksten zijn de vorige generatie: "
                 + "; ".join(f'"{v[:60]}…"' for v in verouderd[:3])
                 + (f" (+{len(verouderd) - 3} meer)" if len(verouderd) > 3 else ""))
    elif not leesbaar:
        ondergrens, reden = "format", "modulestructuur niet als titel + sub-bullets te lezen"
    elif structuur:
        ondergrens = "format"
        reden = "structuur wijkt af: " + ", ".join(sorted({f"{i.section}/{i.code}"
                                                           for i in structuur}))
    elif hard:
        ondergrens = "stijl"
        reden = f"{len(hard)} harde check-fails op de formulering"
    else:
        # Geen enkele check faalt. Dat is geen bewijs van conformiteit -- zie de docstring.
        ondergrens, reden = "stijl", "geen check-fails; conformiteit niet door code vast te stellen"

    return {"ondergrens": ondergrens, "reden": reden, "lege_kopjes": lege,
            "harde_issues": [str(i) for i in hard], "modules_leesbaar": leesbaar,
            "verouderde_vaste_tekst": verouderd}


SUBMIT_MODUS = {
    "name": "submit_modus",
    "description": "Bepaal hoeveel er aan deze bestaande trainingstekst moet gebeuren om hem "
                   "aan de meegeleverde schrijfregels en het format te laten voldoen.",
    "input_schema": {
        "type": "object",
        "properties": {
            "modus": {"type": "string", "enum": list(MODI),
                "description": "overnemen = voldoet al; stijl = alle kopjes staan er en de "
                               "inhoud klopt, alleen de formulering moet naar de regels "
                               "hierboven; format = de structuur klopt niet of er ontbreken "
                               "kopjes; volledig = de tekst is als basis onbruikbaar en de "
                               "training moet opnieuw worden opgebouwd."},
            "reden": {"type": "string",
                "description": "Eén zin, concreet: wát voldoet er niet. Niet 'de stijl kan "
                               "beter' maar 'Overzicht opent niet met een vraag en Doelgroep "
                               "gebruikt de u-vorm'."},
            "modules_nb": {"type": "string", "enum": list(sjabloon.MODULES_NB_VARIANTEN),
                "description": "Welke NB hoort onder het kopje Modules? 'stabiel' is de "
                               "default en past bij verreweg de meeste trainingen. Kies "
                               "'actueel' ALLEEN als het onderwerp zo snel beweegt dat een "
                               "programmabeschrijving binnen een jaar achterloopt op de "
                               "praktijk: denk aan generatieve AI, cloudplatformen of "
                               "security. Een training over een stabiele taal, methode of "
                               "norm krijgt 'stabiel', ook als er af en toe een versie "
                               "uitkomt."},
            "modules_nb_reden": {"type": "string",
                "description": "Eén korte zin waarom deze variant. Bij 'actueel': wát "
                               "veroudert er precies."},
        },
        "required": ["modus", "reden", "modules_nb", "modules_nb_reden"],
    },
}

SCHAT_MODUS_INSTRUCTIE = """\
Je bepaalt hoeveel werk een bestaande trainingstekst nodig heeft om te voldoen aan de
schrijfregels en het format hieronder. Je herschrijft niets; je stelt alleen vast wat er
minimaal moet gebeuren.

De vier niveaus, van licht naar zwaar:

- `overnemen`:  de tekst voldoet al aan deze regels. Kies dit alleen als je bij het
  nalezen niets zou veranderen.
- `stijl`:      alle kopjes staan er en de inhoud klopt; alleen de formulering voldoet
  niet aan de regels (u-vorm, marketingtaal, ontbrekende openingszinnen, verboden woorden,
  geen causaal verband, verkeerd register voor de persona).
- `format`:     er ontbreken kopjes, of de structuur klopt niet (modules niet als titel
  met sub-bullets, verkeerde aantallen, inhoud die in het verkeerde kopje staat).
- `volledig`:   de bestaande tekst is als basis onbruikbaar en de training moet vanaf de
  brontekst opnieuw worden opgebouwd.

Je krijgt een ONDERGRENS van een deterministische controle mee. Die controle vindt alleen
wat met code te betrappen is; hij kan bewijzen dat iets niet voldoet, nooit dat het wél
voldoet. Ga daarom nooit onder die ondergrens zitten, maar voel je vrij erboven te gaan als
je iets ziet wat code niet ziet.

Beoordeel de tekst op de regels hierboven, niet op je eigen smaak.

Je bepaalt daarnaast welke NB onder het kopje Modules hoort. Dat staat volledig los van de
modus: het gaat niet over de kwaliteit van de tekst maar over het onderwerp.

- `stabiel`:  de default, en de juiste keuze voor verreweg de meeste trainingen. Het
  programma is wat het is; de NB nodigt uit tot afstemming op de eigen praktijksituatie.
- `actueel`:  alleen als het expertisegebied zo snel beweegt dat de programmabeschrijving
  binnen een jaar achterloopt op de praktijk. Zet je die NB er zonder die noodzaak onder,
  dan doet hij afbreuk aan het geheel: hij suggereert dat wij zelf niet weten wat we geven.

Roep tot slot het tool `submit_modus` aan.
"""


def build_modus_system() -> list[dict]:
    """Systeem-prompt voor de modusschatting: de ACTUELE schrijfregels, gecachet.

    Dat de spec hier letterlijk in gaat is het hele punt van deze laag. Wordt de schrijfspec
    of het stijlregister strenger, dan schuift het voorstel automatisch mee -- zonder dat er
    ergens een drempel of een lijst met codes hoeft te worden bijgewerkt. Eén prefix voor de
    hele batch, dus de cache pakt vanaf de tweede training.
    """
    prefix = "\n\n---\n\n".join([_read(SCHRIJFSPEC), _read(HUMANISERING), _read(STIJLREGISTER),
                                 _read(CORRECTIES)])
    return [{"type": "text", "text": SCHAT_MODUS_INSTRUCTIE + "\n\n---\n\n" + prefix,
             "cache_control": {"type": "ephemeral"}}]


def schat_modus(client, content_bron: dict, titel: str = "", dagen: int | None = None,
                verdict: str = "") -> dict:
    """Voorstel voor de herschrijfmodus: Python-ondergrens + een oordeel van een klein model.

    Derde laag boven `scan_vorm`, in dezelfde geest als `kies_vervolgtrainingen`: Python
    levert wat deterministisch vaststaat, een goedkoop model doet het oordeel dat code niet
    kan doen, en een mens beslist (kolom `modus_reviewer`). Levert het model niets
    bruikbaars, dan blijft de ondergrens staan -- nooit een voorstel dat lichter is dan wat
    de checks al hebben weerlegd.

    Levert meteen ook de Modules-NB-variant op. Dat is een tweede veld in hetzelfde
    tool-schema en dus geen extra API-call: dit model leest de brontekst toch al, en de vraag
    "beweegt dit vakgebied snel?" is precies het soort oordeel waar het hier voor zit.

    Geeft {"modus", "reden", "ondergrens", "scan", "modules_nb", "modules_nb_reden"} terug.
    """
    scan = scan_vorm(content_bron, titel, dagen, verdict)
    ondergrens = scan["ondergrens"]
    uitkomst = {"modus": ondergrens, "reden": scan["reden"], "ondergrens": ondergrens,
                "scan": scan, "modules_nb": sjabloon.MODULES_NB_DEFAULT,
                "modules_nb_reden": ""}
    if client is None or not content_bron or ondergrens == "volledig":
        return uitkomst

    user_text = (
        f"Titel: {titel}\n"
        f"Aantal dagen: {dagen if dagen is not None else 'onbekend'}\n\n"
        f"ONDERGRENS uit de deterministische controle: {ondergrens}\n"
        f"Reden: {scan['reden']}\n"
        + ("Harde check-fails:\n" + _opsomming(scan["harde_issues"]) + "\n\n"
           if scan["harde_issues"] else "\n")
        + "BESTAANDE TEKST:\n" + huidige_versie_blok(content_bron, titel)
    )
    out = _call_tool(client, build_modus_system(), user_text, [SUBMIT_MODUS], "submit_modus",
                     max_tokens=2000, model=KLEIN_MODEL, thinking=None)
    if not isinstance(out, dict) or not out.get("modus"):
        return uitkomst

    voorstel = normaliseer_modus(out.get("modus"), default=ondergrens)
    uitkomst["modus"] = hoogste_modus(voorstel, ondergrens)
    reden = _cel(out.get("reden"))
    if uitkomst["modus"] != voorstel:
        reden = f"{reden} (opgehoogd naar de ondergrens {ondergrens}: {scan['reden']})".strip()
    uitkomst["reden"] = reden or scan["reden"]
    uitkomst["modules_nb"] = normaliseer_modules_nb(out.get("modules_nb"))
    uitkomst["modules_nb_reden"] = _cel(out.get("modules_nb_reden"))
    return uitkomst


# De vijf kolommen die `modus_voorstellen()` zelf vult, plus de twee die de reviewer daarna in
# datzelfde sheet bijwerkt. Samen zijn ze wat een tweede ronde uit het uitvoersheet terughaalt.
_MODUS_KOLOMMEN: tuple[str, ...] = ("modus_voorstel", "modus_reden", "modus_ondergrens",
                                    "modules_nb_voorstel", "modules_nb_reden")
_MODUS_REVIEWER_KOLOMMEN: tuple[str, ...] = ("modus_reviewer", "modules_nb_reviewer")


def _modus_uit_frame(df) -> dict[Any, dict[str, str]]:
    """{training_id: kolomwaarden} voor de kolommen die deze stap zelf kent."""
    kolommen = [k for k in _MODUS_KOLOMMEN + _MODUS_REVIEWER_KOLOMMEN if k in df.columns]
    if "modus_voorstel" not in kolommen:
        return {}
    return {rij["training_id"]: {k: _cel(rij[k]) for k in kolommen}
            for _, rij in df.iterrows()}


def _eerdere_modus(out_path: str | None) -> dict[Any, dict[str, str]]:
    """Hetzelfde, maar uit een eerder weggeschreven modus-sheet.

    Via `_load_scored` en niet via `pd.read_excel`, want de id's moeten precies zo genormaliseerd
    worden als die van het invoersheet; een `2347` die hier als `2347.0` binnenkomt joint nergens
    mee en zou het hergebruik stil uitschakelen.
    """
    if not out_path or not os.path.exists(out_path):
        return {}
    try:
        eerder = _load_scored(out_path, waarschuw=False)
    except Exception as e:
        # Een onleesbaar of geheel ander bestand op deze plek is geen reden om te stoppen: het
        # wordt hierna toch overschreven. Wel luid melden, want de stille versie hiervan is een
        # ronde die alles opnieuw langs het model stuurt zonder te zeggen waarom.
        print(f"LET OP: {out_path} is niet als scoresheet te lezen ({e}).\n"
              f"  -> elke training wordt opnieuw bepaald.", file=sys.stderr)
        return {}
    return _modus_uit_frame(eerder)


def _modus_uit_sheet(eerder: dict[str, str]) -> dict:
    """Een rij uit het vorige sheet terug in de vorm die `schat_modus` oplevert.

    De waarden gaan door dezelfde normalisatie als een modelantwoord: een reviewer kan in het
    sheet `Format` of een typefout hebben gezet, en die hoort hier net zo te vallen als daar.

    Ontbreekt `modus_ondergrens` (een sheet waarin alleen het voorstel is teruggeplakt), dan is
    het voorstel zelf de terugval en niet de default `volledig`: anders leest zo'n rij in het
    notebook als "model wijkt af van de ondergrens" terwijl er niets is om van af te wijken.
    """
    voorstel = normaliseer_modus(eerder.get("modus_voorstel"))
    return {
        "modus": voorstel,
        "ondergrens": normaliseer_modus(eerder.get("modus_ondergrens"), default=voorstel),
        "reden": eerder.get("modus_reden", ""),
        "modules_nb": normaliseer_modules_nb(eerder.get("modules_nb_voorstel")),
        "modules_nb_reden": eerder.get("modules_nb_reden", ""),
    }


def modus_voorstellen(scored_path: str, source_path: str, out_path: str | None = None,
                      met_llm: bool = True, verbose: bool = True,
                      opnieuw: bool | Iterable[Any] = False):
    """Vult `modus_voorstel` en `modus_reden` voor elke training in het scoresheet.

    Dit is de stap die de reviewer voorbereidt, net zoals `besluiten.write_besluiten_sheet`
    dat doet voor `actie_besluit`: de code doet het voorwerk, de mens kijkt na en beslist.
    Bestaande waarden in `modus_reviewer` blijven ongemoeid -- die zijn per definitie
    leidend.

    **Een training die al in `out_path` staat gaat niet nog een keer langs het model.** Deze
    stap leest het ruwe scoresheet en schrijft een tweede bestand, en dat ruwe sheet groeit
    per batch aan: draai je 3b opnieuw omdat er tien rijen bij zijn gekomen, dan kostten de
    honderd rijen die er al stonden evenveel calls als de eerste keer -- en verloren en
    passant hun `modus_reviewer`, want die kolom staat in het uitvoersheet en werd hier
    leeg opnieuw aangemaakt. `_eerdere_modus` haalt beide terug. `opnieuw=True` bepaalt alles
    opnieuw (nodig na een wijziging in `scan_vorm` of de modus-prompt), een lijst id's alleen
    die trainingen -- bijvoorbeeld als de brontekst van één training is bijgewerkt.

    Met `met_llm=False` blijft het bij de deterministische ondergrens (geen API-key nodig).
    Dat is bruikbaar als kalibratie, niet als voorstel: de ondergrens stelt nooit
    `overnemen` voor, dus elke training zou minstens een stijlronde krijgen. Wil je die
    kalibratie over álle rijen, geef er dan `opnieuw=True` bij; anders blijft staan wat een
    eerdere ronde mét model heeft bepaald.

    Levert daarnaast `modules_nb_voorstel`: welke vaste NB onder kopje Modules komt. Dat
    staat los van de actualiseringen uit de besluitenronde -- zie `sjabloon.MODULES_NB_*`.
    """
    from collections import Counter

    # Geen kolomwaarschuwing: dit ís de stap die `modus_voorstel` en `modules_nb_voorstel`
    # aanmaakt, dus hier hoort het invoersheet ze nog niet te hebben.
    scored = _load_scored(scored_path, waarschuw=False)
    src_by_id, cols = load_source(source_path)

    # Poort vóór de calls: zonder bronrij valt er niets te beoordelen en zou `scan_vorm` op
    # lege content terugvallen op `volledig` -- een duur advies dat als een oordeel leest
    # terwijl het een join-fout is. Stoppen dus, en wel voordat er tokens zijn verbrand.
    zonder_bron = [srow["training_id"] for _, srow in scored.iterrows()
                   if src_by_id.get(srow["training_id"]) is None]
    if zonder_bron:
        raise ValueError(
            f"{len(zonder_bron)} van de {len(scored)} training_ids staan niet in de "
            f"bronlijst: " + ", ".join(str(t) for t in zonder_bron)
            + f".\nControleer de id-kolom van {scored_path} naast {source_path}; zonder "
              "bronrij is er niets te beoordelen en zou elke rij als 'volledig' worden "
              "voorgesteld.")

    # Alles wat een vorige ronde al bepaald heeft, plus wat de reviewer daarna in dat sheet
    # invulde. Het uit-sheet wint van het invoersheet: dat is de verste stand van deze stap.
    # Een invoersheet dat het modus-blok al meebrengt (een blad waarin een vorige ronde is
    # teruggeplakt) telt dus ook mee.
    uit_sheet = _eerdere_modus(out_path)
    bestaand = {**_modus_uit_frame(scored), **uit_sheet}

    # Het uitvoersheet is een 1-op-1 afbeelding van het invoersheet -- het is verderop de
    # wachtrij, dus er mag geen training in staan die niet in deze batch zit. Gevolg: draai je
    # 3b met een ander scoresheet, dan verdwijnen de rijen van de vorige batch uit het bestand,
    # inclusief hun `modus_reviewer`. Dat is de tegenhanger van `_rijen_van_andere_trainingen`
    # in de besluitenlaag, die ze juist wél bewaart -- besluiten.xlsx wordt op id opgezocht en
    # is geen wachtrij. Hier kan dat niet, dus hier hoort een waarschuwing.
    eigen_ids = set(scored["training_id"])
    verdwijnen = [t for t in uit_sheet if t not in eigen_ids]
    if verdwijnen:
        print(f"LET OP: {len(verdwijnen)} trainingen staan wel in "
              f"{os.path.basename(str(out_path))} maar niet in dit scoresheet.\n"
              f"  -> dat bestand wordt zo overschreven met alleen deze {len(scored)} rijen; "
              f"hun modus en `modus_reviewer` gaan daarbij verloren.\n"
              f"  -> hou je meerdere batches naast elkaar, geef deze dan een eigen "
              f"uitvoernaam.", file=sys.stderr)
    # `opnieuw` gaat alleen over het voorstel. De reviewer-kolommen komen hieronder hoe dan ook
    # terug: een verse bepaling is geen reden om het oordeel van een mens weg te gooien.
    alles_opnieuw = opnieuw is True
    forceer = set() if isinstance(opnieuw, bool) else {t for t in opnieuw}

    # De client pas bij de eerste echte call: een ronde waarin elke training al bepaald is
    # hoort geen API-key nodig te hebben. `make_client()` zou anders meteen struikelen.
    client = None

    if verbose:
        print("Modules-NB: de vaste zin onder kopje Modules. 'voorbehoud-zin' = de variant "
              "die zegt\ndat de inhoud kan afwijken door snelle ontwikkelingen; die hoort de "
              "uitzondering te zijn.\nStaat los van de actualiseringen uit de besluitenronde; "
              "overrulen doe je in `modules_nb_reviewer`.\n")

    voorstellen, redenen, ondergrenzen = [], [], []
    nb_voorstellen, nb_redenen = [], []
    n_hergebruikt = 0
    for _, srow in scored.iterrows():
        tid = srow["training_id"]
        src_row = src_by_id.get(tid)
        content = parse_content(src_row[cols["content"]]) if src_row is not None else {}
        naam = str(srow.get("titel") or (src_row[cols["name"]] if src_row is not None else "") or "")
        dagen = bepaal_dagen(content, srow.get("aantal_dagen_bron"))
        verdict = str(srow.get("verdict", "") or "")
        eerder = {} if (alles_opnieuw or tid in forceer) else bestaand.get(tid, {})
        if eerder.get("modus_voorstel"):
            uitkomst = _modus_uit_sheet(eerder)
            n_hergebruikt += 1
        else:
            if met_llm and client is None:
                if not os.getenv("ANTHROPIC_API_KEY"):
                    raise RuntimeError(
                        f"training {tid} heeft nog geen modus en daarvoor is een API-key "
                        "nodig.\nZet ANTHROPIC_API_KEY, of draai met met_llm=False voor "
                        "alleen de deterministische ondergrens.")
                client = make_client()
            uitkomst = schat_modus(client, content, sjabloon.nieuwe_titel(naam), dagen, verdict)
        voorstellen.append(uitkomst["modus"])
        redenen.append(uitkomst["reden"])
        ondergrenzen.append(uitkomst["ondergrens"])
        nb_voorstellen.append(uitkomst["modules_nb"])
        nb_redenen.append(uitkomst["modules_nb_reden"])
        if verbose:
            afwijking = "" if uitkomst["modus"] == uitkomst["ondergrens"] else \
                f"  (ondergrens {uitkomst['ondergrens']})"
            afwijkende_nb = uitkomst["modules_nb"] != sjabloon.MODULES_NB_DEFAULT
            nb = "  [Modules-NB: voorbehoud-zin]" if afwijkende_nb else ""
            # Het teken vooraan is het enige verschil tussen een verse call en een regel uit
            # het vorige sheet; zonder dat teken leest een ronde die niets deed precies zoals
            # een ronde die alles opnieuw bepaalde.
            merk = "." if eerder.get("modus_voorstel") else ">"
            print(f"{merk} {tid:>6}  {naam[:42]:42} -> {uitkomst['modus']:9}{afwijking}{nb}")
            # De reden meteen eronder: de NB-keuze is de enige uitkomst van deze cel die niet
            # over de tekst gaat maar over het onderwerp, en dat is zonder motivering niet na
            # te lezen.
            if afwijkende_nb and uitkomst["modules_nb_reden"]:
                print(f"          NB-reden: {uitkomst['modules_nb_reden']}")

    scored["modus_voorstel"] = voorstellen
    scored["modus_reden"] = redenen
    scored["modus_ondergrens"] = ondergrenzen
    scored["modules_nb_voorstel"] = nb_voorstellen
    scored["modules_nb_reden"] = nb_redenen
    # `guidance_reviewer` maken we hier niet meer aan: de vrije aanwijzing is `rewrite_guidance`
    # geworden, en die staat al in het scoresheet omdat de scorer hem vult. Ontbreekt hij (een met
    # de hand samengestelde prioriteitslijst), dan komt hij er leeg bij.
    for kolom in ("modus_reviewer", "modules_nb_reviewer", "rewrite_guidance"):
        if kolom not in scored.columns:
            scored[kolom] = ""
    # De twee kolommen die het notebook je in het UITVOERsheet laat invullen, terug uit dat
    # sheet: het ruwe scoresheet kent ze niet, dus zonder dit overschrijft elke tweede ronde
    # de beslissing van de reviewer met een lege cel. Alleen waar de invoer leeg is -- vult
    # het reviewteam `modus_reviewer` in de gedeelde sheet in, dan is dát de verse waarde.
    # `rewrite_guidance` en `kern_reviewer` doen hier niet aan mee: die horen thuis in de
    # gedeelde sheet, en terughalen zou een cel die daar net leeggemaakt is weer opvullen.
    for kolom in ("modus_reviewer", "modules_nb_reviewer"):
        scored[kolom] = [_cel(rij[kolom]) or bestaand.get(rij["training_id"], {}).get(kolom, "")
                         for _, rij in scored.iterrows()]

    if verbose:
        if n_hergebruikt:
            print(f"\n{n_hergebruikt}/{len(voorstellen)} overgenomen (`.`, geen call); "
                  f"{len(voorstellen) - n_hergebruikt} opnieuw bepaald (`>`).\n"
                  f"Wil je alles opnieuw: opnieuw=True, of opnieuw=[id, ...] voor losse "
                  f"trainingen.")
        print("\nverdeling voorstel:", dict(Counter(voorstellen)))
        print("verdeling ondergrens:", dict(Counter(ondergrenzen)))
        anders = sum(1 for v, o in zip(voorstellen, ondergrenzen) if v != o)
        print(f"voorstel wijkt af van de ondergrens bij {anders}/{len(voorstellen)} "
              f"-- juist die rijen zijn het nalezen waard")
        nb_telling = dict(Counter(nb_voorstellen))
        print("verdeling Modules-NB:", nb_telling)
        n_actueel = nb_telling.get("actueel", 0)
        if n_actueel > len(nb_voorstellen) / 3:
            print(f"LET OP: {n_actueel}x de voorbehoud-zin onder kopje Modules. Die hoort de "
                  f"uitzondering te zijn;\nlees de NB-redenen na en zet 'stabiel' in "
                  f"`modules_nb_reviewer` waar je het er niet mee eens bent.")
    # De vijf modus-kolommen zijn hier net achteraan aangeplakt; `orden_kolommen` zet de
    # scorer-kolommen weer in de volgorde van het gedeelde reviewsheet en laat het modus-blok
    # staan waar het staat. Dat blok wordt nooit geplakt -- de modus wordt pas ná de scoor-review
    # bepaald -- maar de rest van het sheet moet wel dezelfde volgorde houden als de scoring-output.
    scored = orden_kolommen(scored)

    if out_path:
        scored.to_excel(out_path, index=False)
        if verbose:
            print(f"\nGeschreven: {out_path}")
    return scored


def checks_over_goud(goud_dir: str = GOUD_DIR, verbose: bool = True) -> dict:
    """Kalibratie: hoe vaak faalt elke harde regel op het goud-corpus?

    Het goud is referentie, geen norm -- daarom staat dit hier en niet in test_rewrite.py.
    Valt een regel bij meer dan de helft van het corpus om, dan is die regel verdacht en
    niet de training. De trainingen die álles halen zijn de few-shot-kandidaten
    (`GOUD_VOORBEELDEN`).

    De modules tellen sinds de modulestructuur parseerbaar is gewoon mee. Ze staan daarnaast
    apart in de uitkomst, want ze domineren het beeld: `bullets_aantal` valt 172 keer om
    over 78 trainingen. Dat is precies het soort cijfer waarvoor de regel hierboven bedoeld
    is -- lees het als een vraag aan de spec, niet als een oordeel over het goud.
    """
    from collections import Counter
    tellingen: Counter = Counter()
    schoon: list[tuple[Any, str]] = []
    schoon_buiten_modules: list[tuple[Any, str]] = []
    bestanden = goud_bestanden(goud_dir)
    for pad in bestanden:
        with open(pad, encoding="utf-8") as f:
            d = json.load(f)
        titel = d.get("titel", "")
        rw = goud_naar_check_input(d.get("content") or {}, titel)
        hard = checks.hard_fails(checks.check_rewrite(rw, {"naam": titel}))
        for issue in hard:
            tellingen[f"{issue.section}: {issue.code}"] += 1
        if not hard:
            schoon.append((d.get("training_id"), titel))
        if not [i for i in hard if i.section != "modules"]:
            schoon_buiten_modules.append((d.get("training_id"), titel))
    if verbose:
        print(f"{len(bestanden)} goud-trainingen; aantal dat elke harde regel NIET haalt:")
        for regel, n in tellingen.most_common():
            print(f"  {n:3d}  {regel}")
        print(f"\n{len(schoon)} halen alles -> kandidaat voor GOUD_VOORBEELDEN:")
        for tid, titel in schoon:
            print(f"  {tid:6} {titel}")
        print(f"\n{len(schoon_buiten_modules)} halen alles buiten de modules-checks om "
              f"(vergelijkingspunt: zo zag deze meting eruit toen modules nog werden "
              f"overgeslagen).")
    return {"tellingen": dict(tellingen), "schoon": schoon,
            "schoon_buiten_modules": schoon_buiten_modules, "totaal": len(bestanden)}


def lengtes_over_goud(goud_dir: str = GOUD_DIR, verbose: bool = True) -> dict:
    """Kalibratie van de lengtebanden: hoe lang is het goud écht?

    Levert de verdeling per kopje plus hoeveel trainingen binnen de doelband en binnen de
    vangrail vallen (`checks.BANDEN`), en daarnaast de verdeling van de zinslengte. Hiermee
    zijn de banden gekozen: het goud haalt de doelband van Overzicht en Inleiding in ~35%
    resp. ~23% van de gevallen, en 41% van de zinnen is langer dan de richtlijn van ±20
    woorden. Een harde grens zou de schrijver dus wegduwen van de vorm die hij hoort te
    imiteren. Draai dit opnieuw voordat je een band of richtlijn verschuift -- niet op gevoel.
    """
    metingen: dict[str, list[int]] = {"overzicht": [], "inleiding": [], "kortste_omschrijving": []}
    zinnen: list[int] = []
    for pad in goud_bestanden(goud_dir):
        with open(pad, encoding="utf-8") as f:
            d = json.load(f)
        rw = goud_naar_check_input(d.get("content") or {}, d.get("titel", ""))
        for kopje in metingen:
            tekst = (rw.get(kopje) or "").strip()
            if not tekst:
                continue
            metingen[kopje].append(len(tekst) if kopje == "kortste_omschrijving"
                                   else checks.word_count(tekst))
        for kopje in ("overzicht", "inleiding"):
            zinnen += [n for n in (checks.word_count(z)
                                   for z in checks.zinnen(rw.get(kopje) or "")) if n >= 3]
    metingen["zinnen"] = zinnen
    if verbose:
        for kopje, waarden in metingen.items():
            if not waarden or kopje == "zinnen":
                continue
            v = sorted(waarden)
            eenheid = "tekens" if kopje == "kortste_omschrijving" else "woorden"
            band = checks.BANDEN.get(kopje)
            print(f"\n{kopje} ({eenheid}, n={len(v)})")
            print(f"  min {v[0]}  mediaan {v[len(v) // 2]}  max {v[-1]}")
            if band:
                doel = sum(1 for x in v if band.doel_lo <= x <= band.doel_hi)
                rail = sum(1 for x in v if band.rail_lo <= x <= band.rail_hi)
                print(f"  binnen doelband {band.doel_lo}-{band.doel_hi}: "
                      f"{doel}/{len(v)} ({100 * doel // len(v)}%)")
                print(f"  binnen vangrail {band.rail_lo}-{band.rail_hi}: "
                      f"{rail}/{len(v)} ({100 * rail // len(v)}%)")
            else:
                boven = sum(1 for x in v if x > 200)
                print(f"  boven de harde 200 tekens: {boven}/{len(v)}")
        if zinnen:
            v = sorted(zinnen)
            n = len(v)
            print(f"\nzinslengte in Overzicht + Inleiding (woorden, n={n})")
            print(f"  mediaan {v[n // 2]}  p90 {v[int(.90 * n)]}  max {v[-1]}")
            for grens in (checks.ZIN_RICHTLIJN, checks.ZIN_SIGNAAL):
                boven = sum(1 for x in v if x > grens)
                print(f"  langer dan {grens} woorden: {boven}/{n} ({100 * boven // n}%)")
    return metingen


# ---------------------------------------------------------------------------
# 10b. EIGEN OUTPUT PROMOVEREN TOT GOUD
# ---------------------------------------------------------------------------

def _check_input_uit_artefact(resultaat: dict) -> tuple[dict, str, int | None]:
    """De rijke check-vorm uit een per-training-JSON: (check-input, titel, dagen).

    Bewust niet via `goud_naar_check_input`: die leest de CMS-HTML terug en ziet daardoor
    `aanpak_invulling`, de catalogustitels en de groepen niet. Voor een training die we tot
    voorbeeld willen promoveren wil je juist die velden meewegen -- het zijn precies de
    plekken waar reviewronde 4 fouten vond.
    """
    document = resultaat.get("document") or {}
    writer_out = _writer_out_uit_json(resultaat)
    vervolg = document.get("vervolgstappen") or {}
    titel = resultaat.get("titel") or document.get("titel", "")
    dagen = (resultaat.get("content") or {}).get("days")
    try:
        dagen = int(float(dagen)) if dagen not in (None, "") else None
    except (TypeError, ValueError):
        dagen = None
    rw = build_check_input(writer_out, list(vervolg.get("titels") or []), titel,
                           list(vervolg.get("groepen") or []))
    return rw, titel, dagen


def _goud_profiel(tid: Any, titel: str, document: dict, dagen: int | None) -> str:
    """Eén regel met de maten waarop de few-shot stil kan afdrijven.

    Zelfde gedachte als `vormprofiel()` in `bouw_goud_v2.py`: de checks bewijzen dat een
    voorbeeld de regels haalt, niet dat de sélectie gevarieerd is. Vier voorbeelden van twee
    dagen over hetzelfde vakgebied halen alles en leveren toch een eenzijdige prefix.
    """
    modules = (document.get("modules") or {}).get("modules") or []
    n_bullets = [len(m.get("bullets") or []) for m in modules]
    overzicht = str(document.get("overzicht", "") or "")
    return (f"  {str(tid):8} {titel[:44]:44} {str(dagen or '?'):>2} dg | "
            f"{len(modules)} modules | {sum(n_bullets):2d} bullets "
            f"({','.join(str(n) for n in n_bullets)}) | "
            f"overzicht {checks.word_count(overzicht):3d} w")


def promoveer_naar_goud(trainingen_dir: str = os.path.join(_HERE, "herschreven", "trainingen"),
                        goud_dir: str = GOUD_V2_DIR, *, catalog: list[dict] | None = None,
                        ids: list | None = None, vervang: bool = True,
                        dry_run: bool = False, verbose: bool = True) -> dict:
    """Controleert de eigen herschreven output en kopieert wat slaagt naar de goudmap.

    Dit is de stap die `checks_over_goud()` niet doet. Die meet een corpus door; dit kiest
    eruit en legt de keuze vast, zodat de few-shot van de volgende batch bestaat uit eigen
    output die met de huidige spec is gegenereerd -- en niet uit een lijst ids die iemand met
    de hand in dit bestand moet bijwerken.

    Een training komt in aanmerking als hij `approved` is, een document heeft en geen enkele
    harde check laat vallen. De content wordt daarbij *opnieuw gerenderd* uit het document en
    niet overgenomen uit het JSON-bestand: een voorbeeld mag nooit verouderde boilerplate
    demonstreren, en zo krijgt het goud automatisch de actuele vaste teksten mee.

    `vervang=True` maakt de goudmap gelijk aan de nieuwe selectie; wat er niet in zit gaat
    weg. Dat is veilig: de vier gerepareerde `v2_*`-voorbeelden zijn altijd terug te bouwen
    met `python bouw_goud_v2.py`.

    Geeft {"gepromoveerd": [...], "afgewezen": [(id, [issues])], "verwijderd": [...]} terug.
    """
    import glob
    from datetime import date

    gewenst = {str(i) for i in ids} if ids else None
    catalog_titels = catalog_titles(catalog) if catalog else None

    gepromoveerd: list[dict] = []
    afgewezen: list[tuple[Any, list[str]]] = []
    overgeslagen: list[tuple[Any, str]] = []

    # recursief: sinds de batch-submappen staat de eigen output verspreid over
    # `trainingen/<batch>/`, en de few-shot wordt gekozen uit álles wat we ooit schreven
    for pad in sorted(glob.glob(os.path.join(trainingen_dir, "**", "*.json"), recursive=True)):
        with open(pad, encoding="utf-8") as f:
            resultaat = json.load(f)
        tid = resultaat.get("training_id", os.path.splitext(os.path.basename(pad))[0])
        if gewenst is not None and str(tid) not in gewenst:
            continue
        if resultaat.get("status") != APPROVED:
            overgeslagen.append((tid, f"status is '{resultaat.get('status')}', niet approved"))
            continue
        document = resultaat.get("document") or {}
        if not document:
            overgeslagen.append((tid, "geen document in het artefact"))
            continue

        rw, titel, dagen = _check_input_uit_artefact(resultaat)
        ctx = {"naam": titel, "dagen": dagen, "catalog_titles": catalog_titels}
        hard = checks.hard_fails(checks.check_rewrite(rw, ctx))
        if hard:
            afgewezen.append((tid, [str(i) for i in hard]))
            continue

        content = uit.document_to_content(document, {"days": dagen} if dagen else {})
        gepromoveerd.append({
            "training_id": tid, "titel": titel, "content": content,
            "bron": os.path.relpath(pad, _HERE),
            "gepromoveerd_op": date.today().isoformat(),
            "_profiel": _goud_profiel(tid, titel, document, dagen),
        })

    nieuwe_ids = [str(g["training_id"]) for g in gepromoveerd]
    verwijderd: list[str] = []
    if vervang:
        blijft = {f"{i}.json" for i in nieuwe_ids} | {GOUD_SELECTIE_BESTAND}
        for pad in goud_bestanden(goud_dir):
            if os.path.basename(pad) not in blijft:
                verwijderd.append(os.path.basename(pad))

    if not dry_run and gepromoveerd:
        os.makedirs(goud_dir, exist_ok=True)
        for goud in gepromoveerd:
            uit_pad = os.path.join(goud_dir, f"{goud['training_id']}.json")
            payload = {k: v for k, v in goud.items() if not k.startswith("_")}
            _schrijf_atomisch(uit_pad, lambda f, p=payload: json.dump(
                p, f, ensure_ascii=False, indent=2, default=_json_default))
        for naam in verwijderd:
            os.remove(os.path.join(goud_dir, naam))
        _schrijf_atomisch(os.path.join(goud_dir, GOUD_SELECTIE_BESTAND), lambda f: json.dump(
            {"ids": nieuwe_ids, "bijgewerkt": date.today().isoformat(),
             "bron": "promoveer_naar_goud"}, f, ensure_ascii=False, indent=2))
        # Zodat de rest van deze sessie meteen met de nieuwe selectie werkt.
        global GOUD_VOORBEELDEN
        GOUD_VOORBEELDEN = laad_goud_selectie(goud_dir)

    if verbose:
        kop = "DRY RUN -- er is niets weggeschreven\n" if dry_run else ""
        print(f"{kop}{len(gepromoveerd)} van de "
              f"{len(gepromoveerd) + len(afgewezen) + len(overgeslagen)} trainingen "
              f"in {os.path.relpath(trainingen_dir, _HERE)} kan goud zijn:")
        for goud in gepromoveerd:
            print(goud["_profiel"])
        for tid, redenen in afgewezen:
            print(f"  {str(tid):8} VALT AF ({len(redenen)} harde fails):")
            for regel in redenen:
                print(f"           {regel}")
        for tid, reden in overgeslagen:
            print(f"  {str(tid):8} overgeslagen: {reden}")
        if verwijderd:
            actie = "zou weggaan" if dry_run else "weg"
            print(f"\nuit {os.path.relpath(goud_dir, _HERE)} {actie}: {', '.join(verwijderd)}")
        if not dry_run and gepromoveerd:
            print(f"\nfew-shot is nu {GOUD_VOORBEELDEN[:GOUD_N]}")
            print("Let bij een volgende ronde op de variatie hierboven: loopt het aantal "
                  "dagen of het vakgebied te veel gelijk, dan leert de schrijver één vorm.")
    return {"gepromoveerd": [{k: v for k, v in g.items() if k not in ("content", "_profiel")}
                             for g in gepromoveerd],
            "afgewezen": afgewezen, "overgeslagen": overgeslagen, "verwijderd": verwijderd}


def _review_rij(res: RewriteResult, content: dict, content_bron: dict | None = None) -> dict:
    """Eén rij voor het review-tabblad: status + elk kopje in platte tekst.

    De brontekst staat er als laatste kolom bij. Zonder die kolom leest de reviewer alleen
    de nieuwe tekst en kan hij een claim of een opgeschoven niveau niet zien -- dat is de
    fout die de tekst overleeft nadat de judge hem heeft goedgekeurd. Dezelfde reden waarom
    `build_judge_user` de bron meestuurt; hier alleen voor een mens in plaats van een model.

    De flags staan in drie kolommen in plaats van één. `flags_hoog` is de kolom die naar de
    reviewer gaat; toen alles in één kolom stond was 62% ervan een lengte-melding binnen de
    vangrail of hetzelfde woord voor de derde keer, en dan leest niemand de kolom nog. Zie
    de tier-tabel in rewrite_checks.py. Zonder `flags_tier` (oude resultaten, error-routes)
    valt alles terug op `flags_hoog`: liever te veel laten zien dan iets verstoppen.
    """
    tiers = res.flags_tier or {checks.TIER_HOOG: list(res.flags)}
    rij = {
        "training_id": res.training_id, "titel": res.titel,
        "oude_titel": res.oude_titel, "status": res.status,
        "modus": res.modus, "modus_voorstel": res.modus_voorstel,
        "spec_versie": res.spec_versie,
        # Onder welke few-shot is deze tekst geschreven? Valt een hele batch op dezelfde
        # manier tegen, dan is dit meestal de verklaring en niet de spec.
        "goud_voorbeelden": " | ".join(res.goud_voorbeelden),
        "reden": res.reden, "thin": res.thin,
        # n_hoog is het triagegetal: hoeveel opmerkingen vragen om een oordeel? n_flags
        # blijft het totaal, zodat een training met veel ruis nog steeds opvalt.
        "n_hoog": len(tiers.get(checks.TIER_HOOG, [])), "n_flags": len(res.flags),
        "flags_hoog": " | ".join(tiers.get(checks.TIER_HOOG, [])),
        "flags_mechanisch": " | ".join(tiers.get(checks.TIER_MECHANISCH, [])),
        "flags_laag": " | ".join(tiers.get(checks.TIER_LAAG, [])),
        "judge_confidence": (res.judgment or {}).get("judge_confidence", ""),
        "toegepaste_acties": " | ".join(res.toegepaste_acties),
        "approve_edit": "",   # reviewer vult in: approve / edit / reject
        # Meetkolommen, geen reviewwerk. `seconden` is het getal waarop `TIJDSBUDGET` wordt
        # gekalibreerd -- dat staat op een schatting zolang de duur nergens is vastgelegd --
        # en `n_rondes` laat zien hoeveel schrijverspogingen een training kostte.
        "n_rondes": len(res.rondes),
        "seconden": res.seconden,
        # Idem, en om deze vier draait de vraag of fouten clusteren. `gestart_op` maakt de
        # rijen vergelijkbaar in de tijd (het sheet zelf staat niet in runvolgorde:
        # `drop_duplicates(keep="last")` zet een opnieuw gedraaide training achteraan), en
        # `n_stiltes` staat óók bij een geslaagde training -- dat zijn de 198 rijen die nu
        # niets zeggen over het moment waarop ze draaiden.
        "gestart_op": res.gestart_op,
        "fout_soort": res.fout_soort,
        "n_stiltes": res.storingen.get("stiltes", 0),
        "stilte_seconden": res.storingen.get("stilte_seconden", 0.0),
    }
    plat = uit.content_naar_platte_tekst(content, res.titel) if content else {}
    for kopje in sjabloon.KOPJES:
        rij[kopje.kop] = plat.get(kopje.cms, "")
    rij["brontekst"] = (build_source_text(content_bron, res.oude_titel or res.titel)
                        if content_bron else "")
    return rij


_FOLLOW_UP_LI_RE = re.compile(r"(<li>)(.*?)(</li>)", re.S)


def normaliseer_follow_up(html_tekst: str) -> tuple[str, list[str]]:
    """Zet de vervolgtraining-titels in een bestaande follow_up in de nieuwe stijl.

    Al herschreven trainingen nemen we ongewijzigd over, met één uitzondering: hun
    Vervolgstappen-lijst. Daar staan nog titels als "Cursus PowerPoint", en bij 197 van de
    519 regels het voorvoegsel "Training". Beide gaan eruit; `sjabloon.vervolgtitel` laat
    regels die geen titel zijn ("Trainingen voor specifieke databasesystemen zoals ...")
    met rust. Geeft (nieuwe html, lijst van gewijzigde titels) terug.
    """
    gewijzigd: list[str] = []

    def vervang(m):
        oud = m.group(2).strip()
        nieuw = sjabloon.vervolgtitel(oud)
        if nieuw != oud:
            gewijzigd.append(f"{oud} -> {nieuw}")
            return f"{m.group(1)}{nieuw}{m.group(3)}"
        return m.group(0)

    return _FOLLOW_UP_LI_RE.sub(vervang, html_tekst or ""), gewijzigd


# Kopjes die een actualisering los kan raken. `aanpak`, `vervolgstappen` en `certificatie`
# staan er niet bij: die zijn volledig sjabloon of komen uit de catalogus, dus daar valt
# inhoudelijk niets te actualiseren.
ACTUALISEERBARE_KOPJES = ("overzicht", "inleiding", "modules", "doelgroep",
                          "voorkennis", "doelen", "kortste_omschrijving")


def build_actualisatie_tool() -> dict:
    """Tool voor een gerichte actualisering: elk kopje optioneel, niets verplicht.

    Afgeleid uit `SUBMIT_REWRITE`, net als `build_kopje_tool` -- één bron voor de
    veldbeschrijvingen. Het verschil met `submit_rewrite` is dat `required` leeg is: het
    model levert alléén de kopjes die de goedgekeurde actie daadwerkelijk raakt. Wat het niet
    noemt blijft byte-voor-byte staan, en dat is precies de garantie die deze modus geeft.
    """
    props = {k: v for k, v in SUBMIT_REWRITE["input_schema"]["properties"].items()
             if k in ACTUALISEERBARE_KOPJES}
    props["notities"] = {"type": "string",
                         "description": "Optioneel: wat je niet kon doorvoeren en waarom."}
    return {
        "name": "submit_actualisatie",
        "description": "Lever ALLEEN de kopjes die door de goedgekeurde actualiseringen "
                       "veranderen, in hun geheel en in de nieuwe stijl. Laat elk kopje weg "
                       "dat ongewijzigd kan blijven.",
        "input_schema": {"type": "object", "properties": props, "required": []},
    }


def build_actualisatie_user(b: RewriteBriefing, content: dict, titel: str) -> str:
    """De user-prompt voor een gerichte actualisering; los zodat hij zonder API te lezen is.

    Zelfde reden als bij `build_writer_user` en `build_judge_user`: een prompt die alleen
    ontstaat binnen een functie die ook de call doet, is niet te testen en dus niet te bewaken.
    """
    return (
        f"Titel: {titel}\n"
        f"Persona: {b.persona}\n"
        f"Aantal dagen: {b.dagen if b.dagen is not None else 'onbekend'}\n\n"
        f"KERN. Het niveau van deze training; schrijf daar nooit boven:\n"
        f"{b.kern_definitief}\n\n"
        f"{BESLISSING_UITLEG}\n\n"
        # Dit pad draait géén code-checks: `actualiseer_content` levert losse kopjes, geen
        # compleet document, dus `check_rewrite` kan er niet overheen. De prompt is hier de
        # enige laag die de escalatie tegenhoudt.
        f"{ACTIE_WERKWOORD}\n\n"
        "GOEDGEKEURDE ACTUALISERINGEN, dit is het enige wat er mag veranderen. Staat er een\n"
        "VOORWAARDE bij, dan is die bindend:\n"
        f"{_opsomming(x.als_instructie() for x in b.goedgekeurd)}\n\n"
        "NIET DOEN, afgewezen door de reviewer:\n"
        f"{_opsomming(x.als_instructie() for x in b.afgewezen)}\n\n"
        # Hier stond alleen `guidance_reviewer`, en dat kon toen: de aanwijzing van de reviewer
        # was een eigen kolom, los van de scorer-guidance die over hérschrijven gaat. Nu het één
        # kolom is valt dat onderscheid weg, en beide keuzes zijn slecht: alleen de oude kolom
        # lezen laat elke aanwijzing van het reviewteam vallen, en de hele guidance kaal
        # doorgeven nodigt in déZE modus uit tot precies de herstructurering die `overnemen`
        # verbiedt ("cluster de vijf modules naar 4-6"). Vandaar de aanwijzing mét de grens
        # eromheen; de opdracht eronder blijft het enige mandaat.
        + (f"AANWIJZING bij deze training (van scorer en reviewer). Hij is GEEN opdracht om de\n"
           f"tekst te herstructureren; volg hem alleen waar hij een goedgekeurde actualisering\n"
           f"hierboven raakt:\n{b.guidance_definitief}\n\n"
           if b.guidance_definitief.strip() else "")
        + "OPDRACHT: deze training staat al in de nieuwe stijl en wordt NIET herschreven.\n"
          "Voer alleen de goedgekeurde actualiseringen hierboven door. Lever uitsluitend de\n"
          "kopjes die daardoor veranderen, elk in zijn geheel en in dezelfde stijl als nu.\n"
          "Raakt een actie maar één kopje, lever dan ook maar één kopje. Verander niets aan\n"
          "de kopjes die je weglaat; die blijven letterlijk staan.\n\n"
        + "HUIDIGE VERSIE:\n" + huidige_versie_blok(content, titel)
    )


def actualiseer_content(client, b: RewriteBriefing, content: dict,
                        titel: str) -> tuple[dict, list[str]]:
    """Voert de goedgekeurde actualiseringen door in een verder ongemoeide training.

    Dit is as 2 op zijn smalst: de tekst voldoet al, er is alleen een besluit van de reviewer
    dat er iets bij of anders moet. Het model krijgt de bestaande tekst per kopje en levert
    alleen wat het verandert; de code rendert precies díe velden opnieuw en laat de rest van
    `content` ongemoeid -- inclusief `follow_up`, `setup` en `certification`, die anders door
    het sjabloon zouden worden overschreven.

    Geeft (nieuwe content, lijst met wat er is aangepast) terug.
    """
    if client is None or not b.goedgekeurd:
        return content, []
    tool = build_actualisatie_tool()
    user_text = build_actualisatie_user(b, content, titel)
    out = _call_tool(client, build_writer_system(), user_text, [tool], "submit_actualisatie")
    if not isinstance(out, dict):
        return content, ["actualisering niet doorgevoerd: geen bruikbaar antwoord van het model"]

    nieuw = dict(content)
    gewijzigd: list[str] = []
    for veld in ACTUALISEERBARE_KOPJES:
        if veld not in out or out[veld] in (None, "", [], {}):
            continue
        sleutel, waarde = uit.render_veld(veld, out[veld], {"titel": titel})
        if nieuw.get(sleutel) != waarde:
            nieuw[sleutel] = waarde
            gewijzigd.append(sjabloon.KOP_PER_VELD.get(veld, veld))
    if _cel(out.get("notities")):
        gewijzigd.append(f"notitie schrijver: {_cel(out['notities'])}")
    return nieuw, gewijzigd


def neem_over(b: RewriteBriefing, client=None) -> tuple[RewriteResult, dict]:
    """Een training die al aan het actuele format voldoet, ongewijzigd doorzetten.

    Niet herschrijven (dat zou een goede tekst alleen maar slechter maken), maar wel in
    `herschreven.xlsx` zetten -- anders is dat sheet geen compleet CMS-document. De
    code-check draait er wel overheen, zodat afwijkingen zichtbaar worden in `flags`.

    Wél doorgevoerd: de goedgekeurde actualiseringen. Die staan los van het herschrijfniveau
    (zie `RewriteBriefing.modus`) en golden tot nu toe alleen voor trainingen die de volledige
    herschrijving in gingen -- een training met `herschreven=1` liet ze stilzwijgend vallen,
    terwijl een mens er wel voor had getekend. Zonder acties is dit pad nog steeds gratis:
    geen enkele API-call.

    Geeft (resultaat, CMS-content) terug.
    """
    content = dict(b.huidige_content or {})
    naam = b.titel
    titel = b.nieuwe_titel
    flags: list[str] = []
    tier_regels: dict[str, list[str]] = {t: [] for t in checks.TIERS}

    def meld(regel: str, tier: str = checks.TIER_HOOG) -> None:
        """Een wijziging op dit pad, met de aandacht die hij vraagt.

        Alles wat hier wordt bijgewerkt is een flag, maar niet elke flag is werk voor een
        reviewer: de vaste teksten en de vervolgstappen-titels komen deterministisch uit
        `sjabloon` en kunnen daar niet fout gaan. Een gewijzigde titel of een doorgevoerde
        actualisering wil een mens wél nalezen -- daar heeft iemand voor getekend.
        """
        flags.append(regel)
        tier_regels[tier].append(regel)

    if content.get("follow_up"):
        content["follow_up"], gewijzigd = normaliseer_follow_up(content["follow_up"])
        for g in gewijzigd:
            meld(f"vervolgstappen-titel aangepast: {g}", checks.TIER_LAAG)
    if naam != titel:
        meld(f"titel aangepast: {naam} -> {titel}")

    # Vaste teksten altijd verversen, ook op dit pad. "Overnemen" gaat over de geschreven
    # tekst; de boilerplate is van ons en volgt het template. Zonder dit zou een training die
    # niemand aanraakt met de vorige generatie vaste teksten in het CMS blijven staan.
    content, ververst = uit.ververs_vaste_teksten(content, titel, b.modules_nb)
    for v in ververst:
        meld(f"vaste tekst bijgewerkt: {v}", checks.TIER_LAAG)

    reden = "voldoet al aan het actuele format"
    toegepast: list[str] = []
    if b.goedgekeurd:
        # Het enige stuk van dit pad dat het netwerk raakt, dus het enige dat een budget nodig
        # heeft. Zonder goedgekeurde actualiseringen kost `neem_over` geen enkele call.
        with tijdsbudget():
            content, aangepast = actualiseer_content(client, b, content, titel)
        toegepast = [f"{x.nr}. {x.actie}" + (f" [{x.voorwaarde}]" if x.voorwaarde else "")
                     for x in b.goedgekeurd]
        if aangepast:
            for a in aangepast:
                meld(f"geactualiseerd: {a}")
            reden = f"{reden}; {len(b.goedgekeurd)} actualisering(en) doorgevoerd"
        else:
            meld("goedgekeurde actualiseringen leverden geen wijziging op")

    # Modules tellen hier sinds kort mee: zolang `goud_naar_check_input` ze oversloeg, gaf
    # elke overgenomen training een misleidend schone lijst. Training 328 bleek zo modules
    # met 0 en 1 sub-bullets te hebben zonder dat iemand dat zag.
    rw = goud_naar_check_input(content, titel)
    # Ook de HARD-issues gaan hier mee: op dit pad komt de schrijver er niet aan te pas, dus
    # ze zijn signaal en geen revisie-opdracht. `per_tier` kent geen tier voor een HARD-code
    # en zet ze daarmee vanzelf op hoog -- precies waar ze horen.
    issues = checks.check_rewrite(rw, {"naam": titel})
    flags += [str(i) for i in issues]
    for tier_naam, regels in checks.per_tier(issues).items():
        tier_regels[tier_naam] += regels
    res = RewriteResult(b.training_id, titel, OVERGENOMEN, reden=reden,
                        flags=flags, flags_tier=tier_regels, oude_titel=naam,
                        modus="overnemen",
                        modus_voorstel=b.modus_voorstel, toegepaste_acties=toegepast,
                        spec_versie=spec_versie())
    return res, content


def _json_default(o):
    """numpy-scalars uit pandas naar Python-types.

    `training_id` komt uit een DataFrame en is dus `numpy.int64`; `thin` kan `numpy.bool_`
    zijn. De stdlib `json` kent die typen niet en gooit er een TypeError over. Elk scalair
    numpy-object heeft `.item()`, dus een numpy-import is hier niet nodig -- de repo heeft
    die dependency verder nergens.
    """
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _schrijf_atomisch(pad: str, schrijf) -> None:
    """Schrijft via een tijdelijk bestand en hernoemt pas als de inhoud compleet is.

    `open(pad, "w")` gooit de bestaande inhoud weg vóór er iets geschreven is. Faalt het
    schrijven daarna, dan ligt er een half artefact op de plek van een versie die het wél
    deed -- en de inspectiecel kan het niet meer inlezen. Het tmp-bestand staat in dezelfde
    map, dus `os.replace` is een atomaire rename.
    """
    tmp = f"{pad}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            schrijf(f)
        os.replace(tmp, pad)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def artefact_dir(out_dir: str, batch: str | None = None) -> str:
    """Waar de artefacten van deze batch komen te staan.

    Zonder `batch` is dat de platte map `trainingen/`, en dat blijft zo: de trainingen van
    voor de submappen staan daar, en die hoeven niet te verhuizen om vindbaar te blijven.
    Met een batchnaam wordt het `trainingen/<batch>/`, zodat een Drive-upload precies één
    batch kan meenemen in plaats van alles wat er ooit is geschreven.
    """
    basis = os.path.join(out_dir, "trainingen")
    return os.path.join(basis, str(batch).strip()) if str(batch or "").strip() else basis


def artefact_paden(out_dir: str, batch: str | None = None) -> list[str]:
    """Alle `<id>.json` in deze batch, of -- zonder batch -- in de héle outputmap.

    Let op het verschil met `artefact_dir`: dáár betekent "geen batch" de platte map, hier
    betekent het "alles, submappen incluis". Dat is precies wat de twee aanroepers nodig
    hebben: schrijven doe je op één plek, zoeken over het geheel (`promoveer_naar_goud` kiest
    de few-shot uit alles wat we ooit hebben geschreven).
    """
    import glob
    if str(batch or "").strip():
        return sorted(glob.glob(os.path.join(artefact_dir(out_dir, batch), "*.json")))
    basis = os.path.join(out_dir, "trainingen")
    return sorted(glob.glob(os.path.join(basis, "**", "*.json"), recursive=True))


def zoek_artefact(out_dir: str, training_id: Any) -> str | None:
    """Het pad van `<id>.json`, waar het ook staat: plat of in een batch-submap.

    Sinds de submappen weet een aanroeper niet meer in welke batch een training zit, en dat
    zou hij ook niet moeten hoeven weten -- een training_id is uniek over alle batches heen.
    Staat hetzelfde id in twee batches (opnieuw gedraaid onder een nieuwe naam), dan wint de
    laatst gewijzigde: dat is de versie die ook in `herschreven.xlsx` staat.
    """
    kandidaten = [p for p in artefact_paden(out_dir)
                  if os.path.splitext(os.path.basename(p))[0] == str(training_id)]
    if not kandidaten:
        return None
    return max(kandidaten, key=os.path.getmtime)


def schrijf_training_artefacten(json_dir: str, tid: Any, res: RewriteResult,
                                content_uit: dict) -> dict[str, str | None]:
    """De twee artefacten per training: de lossless JSON en het leesbare markdown-document.

    Eén plek, zodat batch, hergeneratie en de losse notebook-cel hetzelfde wegschrijven.
    De markdown is exact de weergave die de judge beoordeelt en die het notebook onder de
    cel toont -- om terug te lezen zonder de JSON open te klappen. Zonder document
    (`error`/`rejected`, en ook het spoor `overnemen`) is er niets te renderen; een oudere .md
    van dezelfde training zou dan bij een nieuwere JSON gaan liggen, dus die gaat weg. Dat is
    meteen het enige randgeval: een training die eerst is herschreven en later op `overnemen`
    komt te staan, raakt zijn .md kwijt. De JSON en `herschreven.xlsx` houden alles vast, en
    de Drive-upload draait op `content` en niet op de markdown.

    Geeft de paden terug ({"json": ..., "md": ... of None}).
    """
    os.makedirs(json_dir, exist_ok=True)
    json_pad = os.path.join(json_dir, f"{tid}.json")
    _schrijf_atomisch(json_pad, lambda f: json.dump({
        "training_id": tid, "titel": res.titel, "oude_titel": res.oude_titel,
        "status": res.status, "reden": res.reden, "thin": res.thin, "flags": res.flags,
        # de flags ook uitgesplitst: zonder de tier is `flags` één lijst waarin de paar
        # opmerkingen die om een oordeel vragen ondergaan in de lengte- en woordmeldingen.
        # De notitie boven aan het Drive-doc toont alleen `hoog`, net als de kolom in
        # het review-tabblad.
        "flags_tier": res.flags_tier,
        "modus": res.modus, "modus_voorstel": res.modus_voorstel,
        "spec_versie": res.spec_versie, "goud_voorbeelden": res.goud_voorbeelden,
        "toegepaste_acties": res.toegepaste_acties,
        # writer_out is wat de schrijver letterlijk leverde; nodig om later één kopje te
        # hergenereren (aanpak_invulling zit ingebakken in de vaste Aanpak-alinea).
        "writer_out": res.writer_out,
        "document": res.document, "content": content_uit,
        "judgment": res.judgment,
        # `judgment` is alleen het LAATSTE oordeel; `rondes` is het verloop ernaartoe, en dat
        # is wat de vraag "helpt een revisie erbij?" beantwoordt. `seconden` hoort in datzelfde
        # rijtje: het is de meting waarop `TIJDSBUDGET` wordt bijgesteld.
        "rondes": res.rondes, "seconden": res.seconden,
        # En hetzelfde rijtje voor de vraag daarnaast: draaide deze training in een goed of
        # in een slecht moment? `storingen` telt ook de stiltes die de training overleefde,
        # dus dit veld zegt óók iets bij een `approved`.
        "gestart_op": res.gestart_op, "fout_soort": res.fout_soort,
        "storingen": res.storingen,
    }, f, ensure_ascii=False, indent=2, default=_json_default))

    md_pad = os.path.join(json_dir, f"{tid}.md")
    if res.document:
        _schrijf_atomisch(md_pad, lambda f: f.write(uit.render_markdown(res.document, res.titel)))
    else:
        if os.path.exists(md_pad):
            os.remove(md_pad)
        md_pad = None
    return {"json": json_pad, "md": md_pad}


def bewaar_training(out_dir: str, res: RewriteResult, content_bron: dict | None = None,
                    batch: str | None = None) -> dict[str, str | None]:
    """Eén los resultaat wegschrijven naar `<out_dir>/trainingen/[<batch>/]`.

    Voor de notebook-cel die één training herschrijft: zonder dit blijft dat resultaat in
    het geheugen en staat de markdown die je onder de cel leest nergens op schijf.
    `herschreven.xlsx` blijft ongemoeid -- dat sheet vullen de batch (sectie 6) en
    `hergenereer_kopje_op_schijf` (sectie 8).
    """
    content_uit = uit.document_to_content(res.document, content_bron or {}) if res.document else {}
    return schrijf_training_artefacten(artefact_dir(out_dir, batch),
                                       res.training_id, res, content_uit)


VERLOOP_LOG = "verloop.jsonl"


def _log_verloop(out_dir: str, run_id: str, positie: int, res: RewriteResult,
                 batch: str | None = None, verbose: bool = True) -> None:
    """Eén regel per training per run, append-only: de enige plek die een herkansing niet wist.

    `<id>.json` en de reviewrij worden allebei overschreven zodra een gestrande training de
    volgende run alsnog slaagt -- `bouw_wachtrij` draagt error-rijen bewust opnieuw aan en
    `drop_duplicates(..., keep="last")` doet de rest. Gemeten gevolg: van vier batches stonden
    er nog 3 error-rijen op 201 trainingen, en de volgorde waarin ze draaiden was alleen nog
    uit de mtimes van de artefacten te reconstrueren. De vraag "volgt een fout op een fout?"
    was daarmee niet meer te stellen, en dat is een bewaarkeuze en geen toeval.

    `positie` is de plek in DEZE run, en dat is precies het getal dat het sheet niet heeft:
    daar staat een opnieuw gedraaide training achteraan in plaats van waar hij liep.

    Per training weggeschreven en niet aan het eind van de lus, om dezelfde reden waarom een
    uitzondering per training wordt opgevangen: `herschreven.xlsx` verschijnt pas ná de lus,
    dus een run die halverwege afbreekt laat anders niets achter -- en juist een run die
    afbreekt is er een waarvan je het verloop wilt terugzien.

    Een mislukte regel is geen mislukte training, net als een mislukte opmerking bij het
    Drive-doc: de tekst staat er, dit is een meting ernaast.
    """
    regel = {"run": run_id, "positie": positie, "gestart_op": res.gestart_op,
             "training_id": res.training_id, "batch": str(batch or ""),
             "status": res.status, "fout_soort": res.fout_soort,
             "modus": res.modus, "seconden": res.seconden, "n_rondes": len(res.rondes),
             "reden": " ".join(str(res.reden or "").split())[:200],
             **(res.storingen or {})}
    try:
        with open(os.path.join(out_dir, VERLOOP_LOG), "a", encoding="utf-8") as f:
            f.write(json.dumps(regel, ensure_ascii=False, default=_json_default) + "\n")
    except OSError as e:
        if verbose:
            print(f"  (verloop van {res.training_id} niet gelogd: {type(e).__name__}: {e})",
                  file=sys.stderr)


def _storing_uit(status: str, fout_soort: str, stiltes: Any) -> bool:
    """Hoort deze fout bij het moment of bij deze training? Alleen de eerste koelt af.

    Een `TijdOverschreden` telt mee zodra er stiltes in het spoor staan: dan is het budget niet
    opgegaan aan werk maar aan wachten, en dat is dezelfde storing in een andere jas. Training
    2483 verbrandde zo 1571 s terwijl zijn buren 164 s en 202 s deden. Zonder stiltes is het
    juist géén storing maar een trage training, en dan helpt wachten niets.

    Op de drie losse waarden en niet op een `RewriteResult`, zodat `lees_verloop` dezelfde
    definitie gebruikt als de afkoeling. Anders meet de analyse iets anders dan de batch deed,
    en dat is precies het soort verschil dat je nooit terugvindt.
    """
    if status != "error":
        return False
    if fout_soort in STORINGSSOORTEN:
        return True
    return fout_soort == FOUT_TIJDSBUDGET and bool(stiltes)


def _is_storing(res: RewriteResult) -> bool:
    return _storing_uit(res.status, res.fout_soort, (res.storingen or {}).get("stiltes"))


def lees_verloop(out_dir: str):
    """Het verloop van alle runs als DataFrame, met de kolom waar de vraag om draait.

    Inlezen is één regel pandas; wat deze functie toevoegt is `na_storing`: viel de VORIGE
    training in dezelfde run op een storing? Dat is de definitie van clustering, en die hoort
    één keer in code te staan en niet in een wegwerpscript naast elke analyse. Daarmee is de
    vraag een `groupby`:

        v = lees_verloop("herschreven")
        v.groupby("na_storing")["storing"].mean()

    Staat er in de tweede rij een hoger percentage dan in de eerste, dan volgen fouten op
    fouten. `stiltes` doet in dezelfde tabel mee, en dat is het gevoeligere getal: storingen
    zijn zeldzaam (3 op 201 over vier batches), opgevangen stiltes niet.
    """
    import pandas as pd
    pad = os.path.join(out_dir, VERLOOP_LOG)
    if not os.path.exists(pad) or not os.path.getsize(pad):
        return pd.DataFrame()
    df = pd.read_json(pad, lines=True)
    # Sorteren op (run, positie) en niet op de regelvolgorde: het bestand is append-only, dus
    # een run die na een afgebroken run opnieuw begint staat erachter maar hoort apart.
    df = df.sort_values(["run", "positie"]).reset_index(drop=True)
    df["storing"] = [_storing_uit(s, f, n) for s, f, n
                     in zip(df["status"], df["fout_soort"].fillna(""), df["stiltes"])]
    # `fill_value` en geen `fillna` erachter: de eerste training van een run heeft geen
    # voorganger, en fillna op een object-kolom is bij pandas een downcast met een waarschuwing.
    df["na_storing"] = df.groupby("run")["storing"].shift(1, fill_value=False).astype(bool)
    return df


@dataclass
class Afkoeling:
    """Pauze vóór de volgende training, zolang de vorige op een storing sneuvelde.

    Vóór en niet ná, en dat scheelt: zo wacht de batch nooit achter zijn laatste training aan.

    De teller loopt op `opeenvolgend` en niet op de wachttijd zelf, zodat `wacht()` idempotent
    is: hij slaapt één keer per training, ook als de lus hem twee keer zou aanroepen.
    """
    verbose: bool = True
    opeenvolgend: int = 0             # storingen op rij; terug op 0 na een training die liep
    seconden: float = 0.0             # wat de volgende training moet wachten

    def na(self, res: RewriteResult) -> None:
        if not _is_storing(res):
            self.opeenvolgend, self.seconden = 0, 0.0
            return
        self.opeenvolgend += 1
        self.seconden = min(AFKOELING_START * 2 ** (self.opeenvolgend - 1), AFKOELING_MAX)

    def wacht(self) -> None:
        if not self.seconden:
            return
        wachttijd, self.seconden = self.seconden, 0.0
        if self.verbose:
            print(f"  ({self.opeenvolgend}e storing op rij; {wachttijd:g} s afkoelen voor "
                  f"de volgende training)", file=sys.stderr)
        time.sleep(wachttijd)


def _werk_xlsx_rij_bij(out_path: str, res: RewriteResult, content_uit: dict, verbose=True,
                       content_bron: dict | None = None):
    """Vervangt één rij in `herschreven.xlsx` (beide tabbladen) na een hergeneratie."""
    import pandas as pd
    if not os.path.exists(out_path):
        if verbose:
            print(f"({out_path} bestaat nog niet; alleen de JSON is bijgewerkt)")
        return
    vorige = pd.read_excel(out_path, sheet_name=None)
    cms = vorige.get("cms", pd.DataFrame(columns=["id", "name", "content"]))
    review = vorige.get("review", pd.DataFrame())
    if res.status == APPROVED and content_uit:
        nieuw = pd.DataFrame([{"id": res.training_id, "name": res.titel,
                               "content": json.dumps(content_uit, ensure_ascii=False, default=_json_default)}])
        cms = pd.concat([cms, nieuw], ignore_index=True).drop_duplicates(
            subset="id", keep="last")
    review = pd.concat([review, pd.DataFrame([_review_rij(res, content_uit, content_bron)])],
                       ignore_index=True).drop_duplicates(subset="training_id", keep="last")
    with pd.ExcelWriter(out_path) as writer:
        cms.to_excel(writer, sheet_name="cms", index=False)
        review.to_excel(writer, sheet_name="review", index=False)


def _zet_drive_urls_in_xlsx(out_path: str, urls: dict, verbose: bool = True) -> int:
    """Vult de kolom `drive_url` in het review-tabblad bij, op training_id.

    Achteraf en niet in `_review_rij`: die rij wordt gebouwd voordat er ook maar iets geüpload
    is. De kolom staat daarmee achter `brontekst`, en dat is precies waar losse kolommen in dit
    project horen -- het plakblok van het gedeelde scoresheet ligt vast, alles wat wij er zelf
    bij verzinnen komt erachter.
    """
    import pandas as pd
    if not urls or not os.path.exists(out_path):
        return 0
    vorige = pd.read_excel(out_path, sheet_name=None)
    review = vorige.get("review")
    if review is None or "training_id" not in review.columns:
        return 0
    # de sleutels komen uit de JSON en zijn int; de kolom kan numpy-typen bevatten
    per_id = {str(k): v for k, v in urls.items() if v}
    nieuw = review["training_id"].map(lambda t: per_id.get(str(t), ""))
    if "drive_url" in review.columns:
        # Een lege nieuwe waarde mag een bestaande link niet wissen (deelupload, andere batch).
        # Via `_cel` en niet via `str(o or "")`: een lege cel komt uit Excel terug als NaN, en
        # NaN is truthy -- dat zou de tekst "nan" in de kolom zetten.
        review["drive_url"] = [n or _cel(o) for n, o in zip(nieuw, review["drive_url"])]
    else:
        review["drive_url"] = nieuw
    with pd.ExcelWriter(out_path) as writer:
        for naam, blad in vorige.items():
            (review if naam == "review" else blad).to_excel(writer, sheet_name=naam, index=False)
    gevuld = int((review["drive_url"] != "").sum())
    if verbose:
        print(f"drive_url gevuld voor {gevuld} van de {len(review)} rijen in {out_path}")
    return gevuld


def upload_naar_drive(out_dir: str = "herschreven", drive_map: str = "", **kw) -> dict:
    """De artefacten in `out_dir` als Google Docs naar Drive, en de links in het reviewblad.

    Dunne schil om `drive_upload.upload_naar_drive`: die module kent geen pandas en geen xlsx,
    en dat moet zo blijven. Hier hoort de sheetkolom thuis, want dit is de module die het sheet
    bezit.

    Los aan te roepen, en dat is geen luxe: is de upload halverwege gestrand of stond er een
    batch van voor deze functie op schijf, dan is dit de manier om hem alsnog naar Drive te
    krijgen. De batch overslaan wat er al staat, dus herhalen kost niets.
    """
    verbose = kw.get("verbose", True)
    resultaat = drive_upload.upload_naar_drive(out_dir, drive_map, **kw)
    _zet_drive_urls_in_xlsx(os.path.join(out_dir, "herschreven.xlsx"),
                            resultaat.get("urls") or {}, verbose=verbose)
    return resultaat


def _upload_na_batch(out_dir: str, drive_map: str | None, service, verbose: bool,
                     batch: str | None = None) -> None:
    """De upload aan het eind van een batch, met een vangnet eromheen.

    De artefacten staan op schijf en het sheet is geschreven voordat dit draait, dus een
    kapotte Drive-verbinding kan hoogstens de upload kosten en nooit een batch die net een uur
    aan API-calls heeft opgesoupeerd. De melding wijst naar de losse aanroep, want dat is wat
    je daarna wilt doen.
    """
    if not drive_map:
        return
    try:
        upload_naar_drive(out_dir, drive_map, service=service, batch=batch, verbose=verbose)
    except Exception as fout:   # noqa: BLE001 -- de batch is af; dit is bijwerk
        print(f"LET OP: uploaden naar Drive mislukt ({type(fout).__name__}: {fout}) -> "
              f"de artefacten staan op schijf; draai "
              f"rw.upload_naar_drive({out_dir!r}, {drive_map!r}) als je het opnieuw wilt.")


def hergenereer_kopje_op_schijf(scored_path: str, source_path: str, training_id: Any,
                                kopje: str, comment: str = "", *, besluiten_path: str,
                                out_dir: str = "herschreven", judge: bool = True,
                                verbose: bool = True) -> RewriteResult:
    """Hergenereert één kopje van een al herschreven training en slaat het resultaat op.

    Zonder `comment` is het een gewone retry; met `comment` stuur je gericht bij
    ("de modules overlappen, voeg 2 en 4 samen"). Werkt de per-training-artefacten
    (JSON + markdown) en de rij in `herschreven.xlsx` bij.
    """
    # Zoeken en niet aannemen: sinds de batch-submappen weet de aanroeper niet in welke map
    # deze training staat, en het resultaat hoort terug op de plek waar het vandaan komt.
    pad = zoek_artefact(out_dir, training_id)
    if pad is None:
        raise FileNotFoundError(
            f"{training_id}.json staat niet in {os.path.join(out_dir, 'trainingen')} "
            f"(ook niet in een batch-submap); herschrijf deze training eerst.")
    json_dir = os.path.dirname(pad)
    with open(pad, encoding="utf-8") as f:
        resultaat = json.load(f)

    b = build_briefing_for_id(scored_path, source_path, training_id,
                              besluiten_path=besluiten_path)
    src_by_id, cols = load_source(source_path)
    src_row = src_by_id.get(training_id)
    content_bron = parse_content(src_row[cols["content"]]) if src_row is not None else {}

    client = make_client()
    catalog = load_catalog()
    res = hergenereer_kopje(client, b, resultaat, kopje, comment,
                            catalog=catalog, boom=load_tree(catalog), judge=judge)
    content_uit = uit.document_to_content(res.document, content_bron) if res.document else {}
    schrijf_training_artefacten(json_dir, training_id, res, content_uit)
    _werk_xlsx_rij_bij(os.path.join(out_dir, "herschreven.xlsx"), res, content_uit, verbose,
                       content_bron)
    if verbose:
        print(f"{res.titel} — kopje '{kopje}' opnieuw gegenereerd -> {res.status}"
              + (f" ({res.reden})" if res.reden else ""))
    return res


def bouw_wachtrij(scored, out_dir: str, *, skip_herschreven: bool = True,
                  append: bool = True, skip_existing: bool = True, start: int = 0,
                  limit: int | None = None, alleen_ids=None):
    """Welke trainingen draaien er, en waarom valt de rest af? Eén rij per sheetrij.

    Dit is de ENIGE plek waar dat wordt bepaald; `rewrite_file` en `toon_wachtrij` lezen
    allebei dit frame. Zet je de filters ergens anders neer, dan liegt de preview zodra er
    een filter bijkomt, en dat is precies waar de verwarring vandaan komt die deze functie
    oplost: `start` telt NIET over het scoresheet maar over wat er ná de filters overblijft.
    Stonden er 10 van de 15 trainingen al in `herschreven.xlsx`, dan is `start=3` de vierde
    van de vijf die resteren en niet sheetrij 3.

    Kolommen: `sheet` (0-based rijindex in het scoresheet), `training_id`, `titel`, `modus`,
    `spoor` (overnemen/herschrijven), `wachtrij` (positie in het herschrijf-spoor, of None),
    `geselecteerd`, `reden` (leeg als de rij meedraait).
    """
    import pandas as pd
    if isinstance(scored, str):
        scored = _load_scored(scored)

    # De splitsing loopt over de MODUS, niet meer over de `herschreven`-kolom. Die kolom is
    # nog steeds het uitgangspunt -- zonder `modus_reviewer`/`modus_voorstel` betekent
    # `herschreven=1` gewoon `overnemen` (zie `RewriteBriefing.modus`) -- maar er is nu één
    # schaal in plaats van twee mechanismen naast elkaar.
    def _modus_van_rij(srow) -> str:
        if not skip_herschreven:
            # expliciet gevraagd om alles te herschrijven: alleen een reviewer overrulet dat
            return normaliseer_modus(srow.get("modus_reviewer"))
        return build_briefing({k: srow[k] for k in scored.columns}, {}, "").modus

    # hervatten: rijen die al in de output staan tellen niet mee in de wachtrij.
    # Behalve de `error`-rijen: die staan er wél, maar er ligt geen tekst achter. Sinds een
    # uitzondering per training wordt opgevangen (`_mislukte_training`) zou een mislukte
    # training zichzelf anders uit de volgende run schrijven, en dan is de reparatie van
    # "één fout kost de hele batch" verruild voor "één fout kost stil die ene training".
    klaar: set = set()
    out_path = os.path.join(out_dir, "herschreven.xlsx")
    if append and skip_existing and os.path.exists(out_path):
        bestaand_review = pd.read_excel(out_path, sheet_name=None).get("review")
        if bestaand_review is not None:
            gelukt = bestaand_review
            if "status" in gelukt.columns:
                gelukt = gelukt[gelukt["status"] != "error"]
            klaar = set(gelukt["training_id"])

    ids = None if alleen_ids is None else {int(t) for t in alleen_ids}

    rijen, positie = [], 0
    for sheet_ix, (_, srow) in enumerate(scored.iterrows()):
        tid = srow.get("training_id")
        modus = _modus_van_rij(srow) if len(scored) else MODUS_DEFAULT
        spoor = "overnemen" if modus == "overnemen" else "herschrijven"
        rij = {"sheet": sheet_ix, "training_id": tid, "titel": str(srow.get("titel") or ""),
               "modus": modus, "spoor": spoor, "wachtrij": None,
               "geselecteerd": False, "reden": ""}
        if tid in klaar:
            rij["reden"] = "staat al in herschreven.xlsx"
        elif spoor == "overnemen":
            # het overnemen-spoor heeft een eigen lus in `rewrite_file` en kost zonder
            # goedgekeurde actualiseringen geen enkele API-call; start/limit raken het niet
            rij["geselecteerd"] = True
        else:
            rij["wachtrij"] = positie
            if ids is not None:
                rij["geselecteerd"] = tid in ids
                rij["reden"] = "" if rij["geselecteerd"] else "niet in IDS"
            else:
                # `limit is not None` en niet `if limit`: N=0 hoort niets te selecteren,
                # niet stilzwijgend alles
                binnen = positie >= start and (limit is None or positie < start + limit)
                rij["geselecteerd"] = binnen
                rij["reden"] = "" if binnen else "buiten START/N"
            positie += 1
        rijen.append(rij)

    q = pd.DataFrame.from_records(rijen, columns=[
        "sheet", "training_id", "titel", "modus", "spoor", "wachtrij", "geselecteerd", "reden"])
    # Int64 en niet int: een rij zonder wachtrijpositie hoort `pd.NA` te zijn en niet een
    # float-NaN die je bij het printen alsnog moet vangen
    q["wachtrij"] = q["wachtrij"].astype("Int64")
    if ids is not None:
        # id's die niet bestaan of al klaar zijn horen zichtbaar te zijn, anders draait er
        # stil minder dan gevraagd
        gevonden = set(q[q["geselecteerd"]]["training_id"])
        q.attrs["ids_niet_gedraaid"] = sorted(ids - gevonden)
    return q


def toon_wachtrij(scored, out_dir: str, *, start: int = 0, limit: int | None = None,
                  alleen_ids=None, alles: bool = False, **kw):
    """Print de wachtrij en geef hem terug. Geen API-calls; dit is de kijk-voor-je-betaalt.

    Draai dit vóór `rewrite_file` met dezelfde `start`/`limit`/`alleen_ids`, dan zie je
    exact welke trainingen er straks door de schrijver gaan. `alles=True` toont ook de
    overgeslagen rijen; standaard blijft het bij de wachtrij zelf, want op een sheet van
    honderden rijen verdrinkt de selectie anders in wat al klaar is.
    """
    import pandas as pd
    q = bouw_wachtrij(scored, out_dir, start=start, limit=limit, alleen_ids=alleen_ids, **kw)
    gekozen  = q[q["geselecteerd"] & (q["spoor"] == "herschrijven")]
    n_over   = int((q["geselecteerd"] & (q["spoor"] == "overnemen")).sum())
    klaar    = q[q["reden"] == "staat al in herschreven.xlsx"]
    in_rij   = q[q["wachtrij"].notna()]
    te_tonen = q if alles else q[q["wachtrij"].notna() | q["geselecteerd"]]

    print(f"wachtrij — {len(in_rij)} van {len(q)} sheetrijen, in sheetvolgorde\n")
    print(f"  {'sheet':>5}  {'wachtrij':>8}  {'id':>6}  {'titel':<46}  {'modus':<9}")
    for _, r in te_tonen.iterrows():
        pos = "-" if pd.isna(r["wachtrij"]) else str(int(r["wachtrij"]))
        pijl = "->" if r["geselecteerd"] else "  "
        staart = ("<< draait" if r["geselecteerd"] and r["spoor"] == "herschrijven"
                  else "<< overnemen" if r["geselecteerd"] else f"({r['reden']})")
        print(f"  {r['sheet']:>5}  {pijl}{pos:>6}  {r['training_id']:>6}  "
              f"{r['titel'][:46]:<46}  {r['modus']:<9}  {staart}")
    if len(klaar) and not alles:
        print(f"  ... plus {len(klaar)} rijen die al in {out_dir}/herschreven.xlsx staan "
              "(alles=True toont ze)")

    selectie = (f"IDS={sorted(int(t) for t in alleen_ids)}" if alleen_ids is not None
                else f"START={start}, N={limit}")
    print(f"\nselectie: {len(gekozen)} te herschrijven ({selectie})"
          + (f" + {n_over} op modus 'overnemen'" if n_over else ""))
    for waarschuwing in _wachtrij_waarschuwingen(q, start, limit, alleen_ids):
        print(waarschuwing)
    return q


def _wachtrij_waarschuwingen(q, start: int, limit: int | None, alleen_ids) -> list[str]:
    """Stille lege selecties zijn de tweede helft van het probleem: `start` voorbij het einde
    draaide 0 trainingen zonder één regel uitvoer. Deze regels horen bij de preview én bij de
    run zelf."""
    uit, n_wachtrij = [], int(q["wachtrij"].notna().sum())
    niet_gedraaid = q.attrs.get("ids_niet_gedraaid") or []
    if niet_gedraaid:
        uit.append(f"LET OP: {niet_gedraaid} staan niet in de wachtrij (onbekend id, al "
                   "herschreven, of modus 'overnemen')")
    if not q["geselecteerd"].any():
        if alleen_ids is not None:
            uit.append("LET OP: geen van de opgegeven IDS staat in de wachtrij; niets te doen.")
        elif start >= n_wachtrij:
            uit.append(f"LET OP: START={start} valt buiten de wachtrij van {n_wachtrij} "
                       "trainingen; niets te doen.")
        else:
            uit.append(f"LET OP: N={limit} selecteert niets; niets te doen.")
    return uit


def rewrite_file(scored_path: str, source_path: str, out_dir: str, *,
                 besluiten_path: str | None = None, start: int = 0,
                 limit: int | None = None, skip_herschreven: bool = True,
                 append: bool = True, skip_existing: bool = True, verbose: bool = True,
                 alleen_ids=None, batch: str | None = None, drive_map: str | None = None):
    """Herschrijft de trainingen en schrijft de artefacten in `out_dir`.

    - trainingen/[<batch>/]<id>.json   lossless: document + CMS-content + oordeel
    - trainingen/[<batch>/]<id>.md     het leesbare document (kopstructuur van het template)
    - herschreven.xlsx                 tabblad `cms` (id/name/content) + tabblad `review`
    - verloop.jsonl                    append-only, één regel per training per run; de eerste
                                       drie worden overschreven zodra een training opnieuw
                                       draait, deze niet (zie `_log_verloop`)

    `batch` zet de artefacten in een eigen submap. Dat is wat een Drive-map per batch mogelijk
    maakt: zonder die scheiding is er op schijf niets wat batch 1 van batch 2 onderscheidt, en
    dan belandt alles wat we ooit schreven in élke Drive-map opnieuw.

    Met `drive_map` gaat die submap daarna als Google Docs naar een Drive-map met die naam. Dat
    is bewust een synchronisatie van de map en niet van deze run: een training die eerder wel is
    geschreven maar niet geüpload, valt buiten de wachtrij van de volgende run en zou anders
    nooit meer langskomen.
    """
    import pandas as pd
    os.makedirs(out_dir, exist_ok=True)
    json_dir = artefact_dir(out_dir, batch)
    os.makedirs(json_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "herschreven.xlsx")

    scored = _load_scored(scored_path)
    src_by_id, cols = load_source(source_path)

    if besluiten_path is None:
        raise ValueError(
            "Geen --besluiten opgegeven. Draai eerst besluiten.write_besluiten_sheet(); "
            "zonder dat sheet is niet vast te stellen wat de reviewer heeft goedgekeurd.")
    per_training = bes.load_besluiten(besluiten_path)

    # De selectie komt uit `bouw_wachtrij`, niet uit een tweede kopie van dezelfde filters:
    # wat de preview toont is per constructie wat deze run draait.
    toon = toon_wachtrij if verbose else bouw_wachtrij
    q = toon(scored, out_dir, skip_herschreven=skip_herschreven, append=append,
             skip_existing=skip_existing, start=start, limit=limit, alleen_ids=alleen_ids)
    gekozen = q[q["geselecteerd"]]
    overnemen = scored.iloc[list(gekozen[gekozen["spoor"] == "overnemen"]["sheet"])]
    te_doen   = gekozen[gekozen["spoor"] == "herschrijven"]
    wachtrij_van_id = dict(zip(te_doen["training_id"], te_doen["wachtrij"]))
    scored_sel = scored.iloc[list(te_doen["sheet"])]

    bestaand_cms, bestaand_review = None, None
    if append and os.path.exists(out_path):
        vorige = pd.read_excel(out_path, sheet_name=None)
        bestaand_cms = vorige.get("cms")
        bestaand_review = vorige.get("review")

    # Authenticeren vóór de eerste Claude-call: een verlopen token wil je weten voordat er een
    # uur aan schrijf- en oordeelcalls in zit, niet erna.
    drive_service = drive_upload.bouw_service() if drive_map else None

    if not len(gekozen):
        # Niets te herschrijven: geen client openen en het bestaande sheet met rust laten. De
        # upload draait wél door -- dit is juist het geval waarin een eerdere run alles al
        # heeft geschreven maar de upload strandde, en de wachtrij die trainingen niet meer
        # aandraagt.
        for regel in _wachtrij_waarschuwingen(q, start, limit, alleen_ids):
            if not verbose:
                print(regel)
        _upload_na_batch(out_dir, drive_map, drive_service, verbose, batch)
        return bestaand_review if bestaand_review is not None else pd.DataFrame()

    catalog = load_catalog()
    if verbose and not catalog:
        print(f"LET OP: {CATALOG_PATH} ontbreekt -> Vervolgstappen-titels leeg/geflagd.")
    boom = load_tree(catalog)
    if verbose and catalog and not boom["paden"]:
        print(f"LET OP: {TREE_PATH} ontbreekt -> vervolgtrainingen alleen op keyword-overlap.")
    # Ook het overnemen-pad kan een client nodig hebben: goedgekeurde actualiseringen worden
    # daar sinds deze schaal wél doorgevoerd (zie `neem_over`).
    client = make_client() if len(scored_sel) or len(overnemen) else None

    cms_records, review_records = [], []
    # Eén run-id en één afkoeling over beide lussen heen: het `overnemen`-spoor doet ook calls
    # zodra er een goedgekeurde actualisering ligt, dus een storing daar hoort de herschrijflus
    # net zo goed te vertragen. `positie` telt over beide lussen door, want dat is de volgorde
    # waarin het netwerk ze zag.
    # Tot op de milliseconde, en dat is geen overdaad: op secondeprecisie krijgen twee runs die
    # binnen dezelfde seconde starten hetzelfde id, en dan lijkt de tweede run een vervolg van
    # de eerste. `lees_verloop` sorteert op (run, positie) en zet dan de verkeerde buren naast
    # elkaar -- precies de fout die de analyse zou maken.
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S%f")[:-3]
    afkoeling = Afkoeling(verbose)
    positie = 0

    # 1. de trainingen die al aan het format voldoen (modus `overnemen`). Zonder goedgekeurde
    #    actualiseringen kost dit pad geen enkele API-call.
    for _, srow in overnemen.iterrows():
        tid = srow["training_id"]
        src_row = src_by_id.get(tid)
        if src_row is None:
            if verbose:
                print(f"  (id {tid} staat op modus 'overnemen' maar heeft geen bron; overgeslagen)")
            continue
        scored_dict = {k: srow[k] for k in overnemen.columns}
        naam = str(scored_dict.get("titel") or src_row[cols["name"]] or "")
        content_bron = parse_content(src_row[cols["content"]])
        b = build_briefing(scored_dict, content_bron, naam, per_training.get(tid, []))
        afkoeling.wacht()
        gestart_op, start = _begin_meting()
        try:
            res, content_uit = neem_over(b, client)
        except Exception as e:
            # Ook dit spoor doet API-calls zodra er een goedgekeurde actualisering ligt,
            # dus het kan op dezelfde manier omvallen als lus 2 hieronder.
            res, content_uit = _mislukte_training(b, e, verbose), {}
        _stempel_meting(res, gestart_op, start)
        positie += 1
        # Ook dit spoor legt zijn artefact vast. Tot deze ronde deed het dat niet, en dan
        # bestaat een overgenomen training nergens op schijf: niet te inspecteren in sectie 7
        # en niet te uploaden naar Drive, terwijl een reviewer hem net zo goed moet lezen.
        schrijf_training_artefacten(json_dir, tid, res, content_uit)
        _log_verloop(out_dir, run_id, positie, res, batch, verbose)
        if res.status != "error":
            cms_records.append({"id": tid, "name": res.titel,
                                "content": json.dumps(content_uit, ensure_ascii=False, default=_json_default)})
        review_records.append(_review_rij(res, content_uit, content_bron))
        afkoeling.na(res)
    if verbose and len(overnemen):
        print(f"{len(overnemen)} trainingen op modus 'overnemen' doorgezet")

    # 2. de trainingen die wél herschreven moeten worden (modus stijl/format/volledig)
    for n, (_, srow) in enumerate(scored_sel.iterrows(), start=1):
        scored_dict = {k: srow[k] for k in scored_sel.columns}
        tid = scored_dict.get("training_id")
        naam = str(scored_dict.get("titel", "") or "")
        src_row = src_by_id.get(tid)
        content_bron = parse_content(src_row[cols["content"]]) if src_row is not None else {}

        afkoeling.wacht()
        if scored_dict.get("ok") is False:
            # Geen call gedaan en niets te meten: dit is een gat in het scoresheet en niet
            # iets dat het netwerk raakt. `overig` dus, zodat het nooit voor een storing
            # wordt aangezien en de batch er niet op gaat wachten.
            res = RewriteResult(tid, naam, "error", reden="scoring mislukt",
                                fout_soort=FOUT_OVERIG)
        else:
            if src_row is None and verbose:
                print(f"  (geen bron gevonden voor id {tid}; alleen scorer-feiten)")
            if not naam and src_row is not None:
                naam = str(src_row[cols["name"]])
            # `build_briefing` staat bewust BUITEN de vangst: dat is deterministische
            # assemblage, dus valt hij om dan valt hij bij elke training om en is stoppen
            # het juiste antwoord. Binnen de vangst staat alleen wat het netwerk raakt.
            b = build_briefing(scored_dict, content_bron, naam, per_training.get(tid, []))
            # Buiten de vangst gemeten, want juist de training die omvalt -- op het tijdsbudget
            # of op wat dan ook -- is degene waarvan je de duur en het storingsspoor wilt
            # terugzien. `_begin_meting` zet het spoor op nul; `rewrite_one` doet dat nog eens
            # voor zijn eigen aanroepers, en dat is dezelfde meting.
            gestart_op, start = _begin_meting()
            try:
                res = rewrite_one(client, b, catalog, boom)
            except Exception as e:
                res = _mislukte_training(b, e, verbose)
            _stempel_meting(res, gestart_op, start)

        content_uit = uit.document_to_content(res.document, content_bron) if res.document else {}

        positie += 1
        schrijf_training_artefacten(json_dir, tid, res, content_uit)
        _log_verloop(out_dir, run_id, positie, res, batch, verbose)

        if res.status == APPROVED and content_uit:
            cms_records.append({"id": tid, "name": res.titel,
                                "content": json.dumps(content_uit, ensure_ascii=False, default=_json_default)})
        review_records.append(_review_rij(res, content_uit, content_bron))
        if verbose:
            # id en wachtrijpositie erbij: `[1/1]` alleen zegt niets over wáár in de wachtrij
            # je zit, en dat is precies wat je wilt kunnen terugvinden in de preview
            print(f"[{n}/{len(scored_sel)} · wachtrij {wachtrij_van_id.get(tid)}] "
                  f"{tid:>6} {naam[:40]:40} "
                  f"[{res.modus:9}] -> {res.status}"
                  + (f" ({res.reden})" if res.reden else ""))
        afkoeling.na(res)

    cms = pd.DataFrame.from_records(cms_records)
    review = pd.DataFrame.from_records(review_records)
    if bestaand_cms is not None:
        cms = pd.concat([bestaand_cms, cms], ignore_index=True).drop_duplicates(
            subset="id", keep="last")
    if bestaand_review is not None:
        review = pd.concat([bestaand_review, review], ignore_index=True).drop_duplicates(
            subset="training_id", keep="last")

    with pd.ExcelWriter(out_path) as writer:
        cms.to_excel(writer, sheet_name="cms", index=False)
        review.to_excel(writer, sheet_name="review", index=False)
    if verbose:
        print(f"\nGeschreven: {out_path} — cms {len(cms)} rijen, review {len(review)} rijen; "
              f"JSON + markdown in {json_dir}/")

    _upload_na_batch(out_dir, drive_map, drive_service, verbose, batch)
    return review


def main():
    from dotenv import load_dotenv
    load_dotenv()
    p = argparse.ArgumentParser(description="Herschrijf trainingen naar de nieuwe stijl.")
    p.add_argument("--scored", required=True, help="scoresheet xlsx (feiten + actie_besluit)")
    # niet `required`: --toon-wachtrij leest alleen het scoresheet en de al geschreven
    # output, en een preview die om een bronsheet vraagt die hij nooit opent nodigt uit tot
    # `--source /dev/null`
    p.add_argument("--source", help="bron-xlsx met content-JSON (brontekst)")
    # niet `required`: --goud en --scan-modus herschrijven niets en hebben dus geen
    # besluiten nodig. `rewrite_file` weigert nog steeds te draaien zonder.
    p.add_argument("--besluiten", help="genormaliseerde besluiten.xlsx")
    p.add_argument("--out-dir", default="herschreven")
    p.add_argument("--start", type=int, default=0,
                   help="positie in de WACHTRIJ (na de skip-filters), niet de rij in het sheet")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--ids", type=int, nargs="*", metavar="ID",
                   help="draai precies deze training_id's; overrulet --start/--limit")
    p.add_argument("--toon-wachtrij", action="store_true",
                   help="print alleen welke trainingen zouden draaien; herschrijft niets")
    p.add_argument("--no-append", action="store_true", help="overschrijf i.p.v. hervatten")
    p.add_argument("--goud", action="store_true", help="exporteer alleen het goud-corpus")
    p.add_argument("--scan-modus", metavar="UIT_XLSX",
                   help="vul modus_voorstel/modus_reden in en schrijf het sheet weg voor de "
                        "reviewer; herschrijft niets")
    p.add_argument("--geen-llm", action="store_true",
                   help="alleen bij --scan-modus: alleen de deterministische ondergrens")
    # nargs="*": zonder id's geeft argparse een lege lijst (= alles opnieuw), zonder de vlag
    # `None` (= hergebruiken wat er in het uit-sheet staat)
    p.add_argument("--modus-opnieuw", nargs="*", type=int, metavar="ID",
                   help="alleen bij --scan-modus: bepaal deze training_id's opnieuw in plaats "
                        "van ze over te nemen uit het uit-sheet; zonder id's alle rijen")
    p.add_argument("--batch", metavar="NAAM",
                   help="zet de artefacten in trainingen/NAAM/ in plaats van in trainingen/; "
                        "een Drive-upload neemt dan alleen die submap mee")
    p.add_argument("--drive-map", metavar="NAAM",
                   help="upload de trainingen na afloop als Google Docs naar een Drive-map "
                        "met deze naam; zonder --batch is dat de submap met dezelfde naam")
    # eigen modus: uploaden zonder te herschrijven, om een gestrande upload op te pakken of
    # een batch van voor deze functie alsnog naar Drive te krijgen
    p.add_argument("--alleen-uploaden", action="store_true",
                   help="alleen bij --drive-map: upload wat er in --out-dir staat en "
                        "herschrijf niets")
    a = p.parse_args()
    if a.alleen_uploaden:
        if not a.drive_map:
            raise SystemExit("--alleen-uploaden vraagt om --drive-map.")
        upload_naar_drive(a.out_dir, a.drive_map, batch=a.batch)
        return
    if a.toon_wachtrij:
        # kijken kost niets: geen key, geen besluiten-sheet, geen bronsheet nodig
        toon_wachtrij(a.scored, a.out_dir, start=a.start, limit=a.limit,
                      alleen_ids=a.ids, append=not a.no_append)
        return
    if not a.source:
        raise SystemExit("--source is verplicht, behalve bij --toon-wachtrij.")
    if a.goud:
        export_goud_corpus(a.source, a.out_dir)
        return
    if a.scan_modus:
        opnieuw = True if a.modus_opnieuw == [] else (a.modus_opnieuw or False)
        # Geen key-poort meer vóór de scan: staat elke training al in het uit-sheet, dan doet
        # deze stap geen enkele call, en dan hoort hij ook niet op een key te stranden. De
        # poort staat nu bij de eerste training die er wél een nodig heeft, in
        # `modus_voorstellen`, en noemt die training bij naam.
        modus_voorstellen(a.scored, a.source, a.scan_modus, met_llm=not a.geen_llm,
                          opnieuw=opnieuw)
        return
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("Zet ANTHROPIC_API_KEY (in een .env-bestand of je omgeving).")
    rewrite_file(a.scored, a.source, a.out_dir, besluiten_path=a.besluiten,
                 start=a.start, limit=a.limit, append=not a.no_append, alleen_ids=a.ids,
                 batch=a.batch, drive_map=a.drive_map)


if __name__ == "__main__":
    main()
