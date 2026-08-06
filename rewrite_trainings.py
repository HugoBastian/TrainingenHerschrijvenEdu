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
from dataclasses import dataclass, field
from typing import Any

# Deze herschrijf-module hergebruikt de content-ingestie van de scorer
# (parse_content / build_source_text / extract_days / make_client / read_input) zodat
# schrijver en scorer EXACT dezelfde brontekst zien. score_trainings.py leeft in het
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
        parse_content, build_source_text, extract_days, make_client,
        read_input as read_source_input,
    )
except ModuleNotFoundError as e:  # pragma: no cover
    raise ModuleNotFoundError(
        "Kon score_trainings.py niet vinden. Zet de omgevingsvariabele "
        f"SCORE_TRAININGEN_DIR naar de map met score_trainings.py (geprobeerd: {_SCORE_DIR})."
    ) from e

import besluiten as bes
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
MAX_REVISIONS = 2                  # code-check + judge revisies vóór mens-wachtrij
N_SHORTLIST = 30                   # kandidaten die Python uit de catalogus voorselecteert
N_VERVOLG = 6                      # vervolgtrainingen die uiteindelijk in de tekst komen
N_VERVOLG_MIN = 3                  # daaronder is een lijst met twee groep-intro's niet zinnig

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

Gebruik geen liggende streepjes (— of –) in de introzin; een komma of een punt doet het werk.

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
_INTRO_DASH_RE = re.compile(r"\s*[—–]\s*")


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

    `extract_days` in het scoringsproject zoekt op de sleutel "dagen", maar in de bron heet
    hij "days" — daardoor viel dit altijd terug op de scorer-schatting. Hier eerst zelf de
    juiste sleutel proberen; het scoringsproject blijft ongemoeid.
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
    rewrite_guidance: str = ""
    menselijke_input_nodig: bool = False
    kern_reviewer: str = ""
    modus_reviewer: str = ""      # kolom `modus_reviewer`: het besluit van een mens
    modus_voorstel: str = ""      # kolom `modus_voorstel`: uit scan_vorm + schat_modus
    guidance_reviewer: str = ""   # kolom `guidance_reviewer`: vrije aanwijzing van de reviewer
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
        """Scorer-guidance plus de vrije aanwijzing van de reviewer, in die volgorde.

        Beide gaan mee: de scorer zegt waar het bronmateriaal het beste landt, de reviewer
        zegt wat hij bij het nalezen zag. De reviewer staat achteraan omdat hij het laatste
        woord heeft, en wordt als zodanig gelabeld zodat het model weet wie wat zegt.
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
                               "beeld, dan is het te kort. De TWEEDE zin — die de openingsvraag "
                               "beantwoordt — begint met 'In deze training leer je …', of met "
                               "'Tijdens deze training …' waar 'leer je' niet past. Nooit een kaal "
                               "'Je leert …' of 'Je werkt met …': dan staat de zin los van de vraag "
                               "erboven. Twee dingen die verder het vaakst misgaan: "
                               "(1) de openingsvraag dekt maar één deelaspect in plaats van het "
                               "zwaartepunt van de training; (2) de werkwoorden staan aan de "
                               "onderkant — 'begrippen kunnen plaatsen', 'gerichter meepraten', "
                               "'ervaren hoe X in elkaar zit'. Kies binnen dezelfde scope het "
                               "sterkste ware werkwoord: 'de opbouw van X doorgronden', 'een stevige "
                               "basis leggen in X'. De slotzin staat in de in-staat-vorm ('Hierdoor "
                               "ben je in staat om …'), niet in een kaal 'Hierdoor kun je …'."},
            "inleiding": {"type": "string",
                "description": "Kopje Inleiding. Richtlijn 180-210 woorden (bij een training van "
                               "4 dagen of meer mag het richting 230), verdiepend op Overzicht. "
                               "Ook hier telt de formulering zwaarder dan het exacte aantal. "
                               "De zin die de openingsvraag beantwoordt noemt de training ('Tijdens "
                               "deze training …'), zonder de duur. Maak 'je' het onderwerp — niet "
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
                               "bereiken, niet op wie iemand is — dat scheelt ook dubbeling met "
                               "Voorkennis, dat er pal onder staat."},
            "voorkennis": {"type": "string",
                "description": "Kopje Voorkennis. Compact: één zin waar dat kan, twee als er een "
                               "voorbehoud of een contactzin bij hoort. Die contactzin luidt 'neem "
                               "DAN gerust contact met ons op'. Herhaal niet wat de Doelgroep al "
                               "zegt: staat daar 'iedereen die al in JavaScript ontwikkelt', dan "
                               "voegt 'ervaring met JavaScript is vereist' niets toe — noem hier de "
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
                               "vaste introzin 'Na deze training ben je in staat om:' — dus "
                               "'Dashboards te bouwen die de juiste vraag beantwoorden', niet "
                               "'Dashboards bouwen'. Herhaal 'in staat' niet; dat staat al in de "
                               "introzin. Hoofdletter aan het begin, zonder de introzin. Een "
                               "vergrotende trap ('scherper', 'gerichter') mag de belofte op maat "
                               "houden, maar vervangt geen sterk werkwoord: 'de opbouw van X "
                               "scherper te doorgronden', niet 'gerichter mee te praten over X'."},
            "kortste_omschrijving": {"type": "string",
                "description": "Kopje Kortste omschrijving. Maximaal 200 tekens — als enige lengte "
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
                               "elkaar tegenspreken over wat de training doet of op welk niveau — "
                               "begin die melding met 'kern-conflict:' en zeg wat elk van beide zegt."},
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
    "wijziging doorvoeren — behandel hem als een opdracht, niet als een open vraag. Staat\n"
    "hij onder NIET DOEN, dan blijft de bestaande situatie ongewijzigd."
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
    "brontekst over wat de training feitelijk doet of op welk niveau — dan wint de BRONTEKST.\n"
    "Meld die botsing in `notities`, zodat een mens ernaar kan kijken."
)

# Zonder deze regel is de brontekst het enige blok in de prompt zonder opdracht eromheen, en
# leest het model hem als achtergrond bij de scorer-velden in plaats van als de training zelf.
BRONTEKST_UITLEG = (
    "BRONTEKST — de bestaande trainingsbeschrijving, ongewijzigd en onafgekapt. Dit is wat de\n"
    "training feitelijk is: welke onderwerpen erin zitten, en wat de deelnemer ermee doet. De\n"
    "velden hierboven zijn een samenvatting ervan; deze tekst is het origineel. Let vooral op\n"
    "de werkwoorden — \"maak je kennis met\", \"we introduceren\", \"we geven een overzicht\"\n"
    "beschrijven een ander niveau dan \"je bouwt\", \"je richt in\", \"je optimaliseert\".\n"
    "Beloof nooit meer dan hier staat.\n\n"
    "Eén uitzondering, en die gaat vóór: de GOEDGEKEURDE ACTUALISERINGEN hierboven. Die voer\n"
    "je uit, ook al staan ze niet in deze brontekst — dat is precies waarom ze bestaan. Deze\n"
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
BRONTEKST_UITLEG_JUDGE = (
    "BRONTEKST — de bestaande trainingsbeschrijving, ongewijzigd en onafgekapt. De velden\n"
    "hierboven zijn een samenvatting ervan door de scorer; dit is het origineel. Gebruik hem\n"
    "als maatstaf voor precies twee dingen:\n"
    "1. FEITGETROUWHEID — elke inhoudelijke claim (versie, vendor, tool, feature, cijfer,\n"
    "   jaartal, certificering) moet herleidbaar zijn tot deze tekst, tot de feiten hierboven\n"
    "   of tot een goedgekeurde actualisering. Staat hij nergens, dan is het een feitfout.\n"
    "2. NIVEAU — lees de werkwoorden. \"Maak je kennis met\", \"we introduceren\", \"we geven\n"
    "   een overzicht\" beschrijven iets anders dan \"je bouwt\", \"je richt in\", \"je\n"
    "   optimaliseert\". Belooft het concept meer dan hier staat, dan is dat een fail.\n\n"
    "UITZONDERING, en die gaat vóór allebei: wat een GOEDGEKEURDE ACTUALISERING hierboven\n"
    "voorschrijft hoort in de tekst, ook al staat het niet in deze brontekst en verschuift het\n"
    "waar de training over gaat. De reviewer heeft daarvoor getekend en de bron is juist het\n"
    "verouderde deel. Reken zo'n passage nooit af als ongegrond of als een te hoge belofte;\n"
    "twijfel je of iets onder een goedgekeurde actie valt, dan valt het eronder. De enige grens\n"
    "is de VOORWAARDE die de reviewer eraan hing.\n\n"
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
    "HUIDIGE VERSIE — de bestaande tekst van deze training, per kopje. Dit is je\n"
    "uitgangsmateriaal én je maatstaf: dit is wat de training feitelijk is en belooft.\n"
    "Let vooral op de werkwoorden — \"maak je kennis met\", \"we introduceren\", \"we geven\n"
    "een overzicht\" beschrijven een ander niveau dan \"je bouwt\", \"je richt in\", \"je\n"
    "optimaliseert\". Beloof nooit meer dan hier staat.\n\n"
    "Een kopje dat op \"(leeg)\" staat ontbrak in de bron; dat vul je aan uit wat de andere\n"
    "kopjes zeggen, niet uit wat je aannemelijk vindt.\n\n"
    "Eén uitzondering, en die gaat vóór: de GOEDGEKEURDE ACTUALISERINGEN hierboven. Die voer\n"
    "je uit, ook al staan ze hier niet — dat is precies waarom ze bestaan."
)


# De opdracht per herschrijfniveau. Deze tekst staat in de USER-message en niet in de
# system-prefix: die prefix is één gecachet blok van ~20k tokens, en een modus-afhankelijke
# prefix zou daar vier varianten van maken.
#
# Wat hier NIET in staat: de actualiseringen. Die lopen op elk niveau mee -- zie
# `ACTUALISEREN_ONGEACHT_MODUS` hieronder en de toelichting bij `RewriteBriefing.modus`.
MODUS_UITLEG: dict[str, str] = {
    "stijl": (
        "OPDRACHT — BIJWERKEN NAAR DE ACTUELE SCHRIJFREGELS.\n"
        "Je bent hier redacteur, geen auteur. De inhoud van deze training klopt en is\n"
        "compleet; wat niet meer klopt is de formulering. Herschrijf zin voor zin naar de\n"
        "regels in de spec hierboven — de 'je'-vorm, de verplichte openingszinnen, het\n"
        "stijlregister, het causale verband, weg met marketingtaal en verboden woorden.\n\n"
        "Verander NIET wát er staat. Geen onderwerpen toevoegen, geen onderwerpen weglaten,\n"
        "geen modules samenvoegen of splitsen, geen volgorde omgooien, geen doelen erbij\n"
        "verzinnen. Elk feit, elk onderwerp en elke belofte in jouw versie staat ook in de\n"
        "huidige versie hieronder. Kom je een kopje tegen dat inhoudelijk rammelt, laat het\n"
        "dan rammelen en meld het in `notities` — dat is een besluit voor een mens."
    ),
    "format": (
        "OPDRACHT — BIJWERKEN NAAR HET ACTUELE FORMAT.\n"
        "De inhoud van deze training klopt, maar de vorm niet: er ontbreken kopjes, of de\n"
        "structuur past niet op het format. Breng hem in vorm en pas daarbij ook de actuele\n"
        "schrijfregels toe.\n\n"
        "Wat je MAG: herindelen, modules samenvoegen of splitsen zodat je op 4-6 modules\n"
        "uitkomt, de volgorde aanpassen, en de ontbrekende kopjes schrijven — maar die leid\n"
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
        f"KERN ({herkomst}) — hierin staat het NIVEAU van de training; schrijf nooit boven dat\n"
        f"niveau, ook niet als een kopje om meer tekst vraagt:\n{b.kern_definitief}\n\n"
        f"{kern_gezag}\n\n"
        f"Te verwerken feiten (bruikbaar):\n{_opsomming(b.bruikbaar)}\n\n"
        f"Weglaten (strippen):\n{_opsomming(b.strippen)}\n\n"
        f"Gaten (vul plausibel waar afleidbaar):\n{_opsomming(b.gaten)}\n\n"
        f"{BESLISSING_UITLEG}\n\n"
        "ACTUALISERINGEN — door de reviewer goedgekeurd. Voer deze uit; staat er een\n"
        "VOORWAARDE bij, dan is die bindend en gaat hij vóór de actietekst:\n"
        f"{_opsomming(x.als_instructie() for x in b.goedgekeurd)}\n\n"
        "NIET DOEN — door de reviewer afgewezen. Voer deze NIET uit, ook niet als de\n"
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
        "LET OP — deze training is bewust ALLEEN bijgewerkt naar de actuele schrijfregels.\n"
        "De opdracht was: de formulering aanpassen, de inhoud ongemoeid laten. Beoordeel hem\n"
        "daarop.\n\n"
        "Dat betekent twee dingen. Reken het concept NIET af omdat het dicht bij de huidige\n"
        "versie blijft, dezelfde onderwerpen in dezelfde volgorde behandelt of weinig is\n"
        "veranderd — dat was de opdracht, niet een tekortkoming. En reken het WÉL af op het\n"
        "omgekeerde: elk onderwerp, feit, doel of belofte in het concept moet herleidbaar\n"
        "zijn tot de huidige versie hieronder of tot een goedgekeurde actualisering. Wat er\n"
        "los van staat is drift, en drift is hier een fail."
    ),
    "format": (
        "LET OP — deze training is bewust ALLEEN bijgewerkt naar het actuele format.\n"
        "De opdracht was: in vorm brengen, ontbrekende kopjes afleiden uit wat er al stond,\n"
        "en de inhoud verder ongemoeid laten.\n\n"
        "Herindelen hoort er dus bij: samengevoegde of gesplitste modules, een andere\n"
        "volgorde en nieuw geschreven Doelgroep-, Voorkennis- of Doelen-kopjes zijn geen\n"
        "fout. Wat wél een fout is: een onderwerp dat nergens in de huidige versie voorkomt,\n"
        "of een kopje dat is volgeschreven met inhoud die niet uit de andere kopjes volgt.\n"
        "Dat is de fout die deze modus moet voorkomen — let er scherper op dan normaal."
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
        f"KERN ({herkomst}) — hierin staat het niveau waarop de training hoort te liggen:\n"
        f"{b.kern_definitief}\n\n"
        f"Feiten (bruikbaar):\n{_opsomming(b.bruikbaar)}\n\n"
        "Weggelaten (strippen) — deze mogen niet terug zijn in het concept:\n"
        f"{_opsomming(b.strippen)}\n\n"
        "Gaten — hierover zweeg de bron. Wat het concept hier invult is constructie: geen\n"
        "feitfout, wél reden om de output als thin te markeren:\n"
        f"{_opsomming(b.gaten)}\n\n"
        f"{BESLISSING_UITLEG}\n\n"
        "Goedgekeurde actualiseringen (moeten verwerkt zijn):\n"
        f"{_opsomming(x.als_instructie() for x in b.goedgekeurd)}\n\n"
        "Afgewezen actualiseringen (mogen NIET terugkomen):\n"
        f"{_opsomming(x.actie for x in b.afgewezen)}\n\n"
        f"{materiaal}\n\n"
        f"CONCEPT — dit is wat je beoordeelt:\n{uit.render_markdown(document, b.nieuwe_titel)}"
    )


# ---------------------------------------------------------------------------
# 7. API-CALL (tool-output, retry met budgetverdubbeling; zelfde geest als de scorer)
# ---------------------------------------------------------------------------

def _extract_tool_input(response, tool_name: str) -> dict | None:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return block.input
    return None


def _call_tool(client, system, user_text: str, tools: list[dict], tool_name: str,
               max_tokens: int = MAX_TOKENS, model: str = MODEL,
               thinking: dict | None = THINKING) -> dict | None:
    """Roept het model tot het `tool_name` aanroept. Verdubbelt budget bij afkapping.

    `model`/`thinking` staan los zodat de goedkope keuzes (vervolgtrainingen) op een
    klein model zonder thinking kunnen draaien, met dezelfde retry-logica.
    """
    messages = [{"role": "user", "content": user_text}]
    budget = max_tokens
    extra = {"thinking": thinking} if thinking else {}
    for _ in range(3):
        resp = client.messages.create(
            model=model, max_tokens=budget, system=system,
            messages=messages, tools=tools, **extra,
        )
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
        # Gemarkeerde vorm: `*...*` is de cursivering uit het template. `render_markdown`
        # zet hem ongewijzigd neer, `render_aanpak` maakt er <em> van voor het CMS.
        "aanpak": (sjabloon.AANPAK_ALINEA_1.format(invulling=invulling)
                   + "\n\n" + sjabloon.AANPAK_ALINEA_2_MARKUP),
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


def rewrite_one(client, b: RewriteBriefing, catalog: list[dict],
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
    ctx = {"catalog_titles": catalog_titles(catalog) if catalog else None,
           "naam": b.nieuwe_titel, "dagen": b.dagen}
    writer_system = build_writer_system()
    base_user = build_writer_user(b)

    notes: list[str] = []
    document: dict = {}
    last_judgment: dict = {}
    for attempt in range(MAX_REVISIONS + 1):
        user_text = base_user if not notes else base_user + "\n\n---\nHERSTEL:\n" + "\n".join(notes)
        writer_out = _call_tool(client, writer_system, user_text, [SUBMIT_REWRITE], "submit_rewrite")
        if not rewrite_input_complete(writer_out):
            notes = ["De submit_rewrite-output was onvolledig; lever alle verplichte kopjes."]
            continue

        titel = bepaal_titel(writer_out, b)
        issues = checks.check_rewrite(
            build_check_input(writer_out, titels, titel, groepen), ctx)
        hard = checks.hard_fails(issues)
        if hard:
            notes = ["Los deze code-check fouten op:"] + [str(i) for i in hard]
            continue

        document = assemble_document(writer_out, b, titels, groepen)
        flags = [str(i) for i in checks.flags(issues)]

        judgment = judge_document(client, b, document)
        last_judgment = judgment
        verdict = judgment.get("verdict", HUMAN_QUEUE)
        gedeeld = dict(document=document, flags=flags, judgment=judgment,
                       toegepaste_acties=toegepast, oude_titel=b.titel, writer_out=writer_out,
                       modus=b.modus, modus_voorstel=b.modus_voorstel,
                       spec_versie=spec_versie(), goud_voorbeelden=actieve_goud_voorbeelden())
        if verdict == APPROVED:
            return RewriteResult(b.training_id, titel, APPROVED, reden="",
                                 thin=b.thin or judgment.get("feitgetrouw", {}).get("thin", False),
                                 **gedeeld)
        if verdict == NEEDS_REVISION and attempt < MAX_REVISIONS:
            notes = ["Judge-revisie:"] + list(judgment.get("revisie_notities", []))
            continue
        # human-queue of revisies op -> mens
        reden = judgment.get("human_reden") or "judge: needs-revision na max revisies"
        return RewriteResult(b.training_id, titel, HUMAN_QUEUE, reden=reden,
                             thin=b.thin, **gedeeld)

    return RewriteResult(b.training_id, b.nieuwe_titel, HUMAN_QUEUE,
                         reden="geen valide concept na max pogingen",
                         document=document, judgment=last_judgment, thin=b.thin,
                         toegepaste_acties=toegepast, oude_titel=b.titel,
                         modus=b.modus, modus_voorstel=b.modus_voorstel,
                         spec_versie=spec_versie(), goud_voorbeelden=actieve_goud_voorbeelden())


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

    ctx = {"catalog_titles": catalog_titles(catalog) if catalog else None,
           "naam": b.nieuwe_titel, "dagen": b.dagen}
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
        opdracht += ["", f"AANWIJZING VAN DE REVIEWER — dit moet er anders:\n{comment.strip()}"]
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
        document=nieuw_document, flags=flags, judgment=judgment, thin=b.thin,
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
_REVIEWER_KOLOMMEN: tuple[str, ...] = ("modus_reviewer", "kern_reviewer",
                                       "guidance_reviewer", "modules_nb_reviewer")


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
                               "praktijk — denk aan generatieve AI, cloudplatformen of "
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

- `overnemen`  — de tekst voldoet al aan deze regels. Kies dit alleen als je bij het
  nalezen niets zou veranderen.
- `stijl`      — alle kopjes staan er en de inhoud klopt; alleen de formulering voldoet
  niet aan de regels (u-vorm, marketingtaal, ontbrekende openingszinnen, verboden woorden,
  geen causaal verband, verkeerd register voor de persona).
- `format`     — er ontbreken kopjes, of de structuur klopt niet (modules niet als titel
  met sub-bullets, verkeerde aantallen, inhoud die in het verkeerde kopje staat).
- `volledig`   — de bestaande tekst is als basis onbruikbaar en de training moet vanaf de
  brontekst opnieuw worden opgebouwd.

Je krijgt een ONDERGRENS van een deterministische controle mee. Die controle vindt alleen
wat met code te betrappen is; hij kan bewijzen dat iets niet voldoet, nooit dat het wél
voldoet. Ga daarom nooit onder die ondergrens zitten, maar voel je vrij erboven te gaan als
je iets ziet wat code niet ziet.

Beoordeel de tekst op de regels hierboven, niet op je eigen smaak.

Je bepaalt daarnaast welke NB onder het kopje Modules hoort. Dat staat volledig los van de
modus: het gaat niet over de kwaliteit van de tekst maar over het onderwerp.

- `stabiel`  — de default, en de juiste keuze voor verreweg de meeste trainingen. Het
  programma is wat het is; de NB nodigt uit tot afstemming op de eigen praktijksituatie.
- `actueel`  — alleen als het expertisegebied zo snel beweegt dat de programmabeschrijving
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


def modus_voorstellen(scored_path: str, source_path: str, out_path: str | None = None,
                      met_llm: bool = True, verbose: bool = True):
    """Vult `modus_voorstel` en `modus_reden` voor elke training in het scoresheet.

    Dit is de stap die de reviewer voorbereidt, net zoals `besluiten.write_besluiten_sheet`
    dat doet voor `actie_besluit`: de code doet het voorwerk, de mens kijkt na en beslist.
    Bestaande waarden in `modus_reviewer` blijven ongemoeid -- die zijn per definitie
    leidend.

    Met `met_llm=False` blijft het bij de deterministische ondergrens (geen API-key nodig).
    Dat is bruikbaar als kalibratie, niet als voorstel: de ondergrens stelt nooit
    `overnemen` voor, dus elke training zou minstens een stijlronde krijgen.

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

    client = make_client() if met_llm else None

    if verbose:
        print("Modules-NB: de vaste zin onder kopje Modules. 'voorbehoud-zin' = de variant "
              "die zegt\ndat de inhoud kan afwijken door snelle ontwikkelingen; die hoort de "
              "uitzondering te zijn.\nStaat los van de actualiseringen uit de besluitenronde; "
              "overrulen doe je in `modules_nb_reviewer`.\n")

    voorstellen, redenen, ondergrenzen = [], [], []
    nb_voorstellen, nb_redenen = [], []
    for _, srow in scored.iterrows():
        tid = srow["training_id"]
        src_row = src_by_id.get(tid)
        content = parse_content(src_row[cols["content"]]) if src_row is not None else {}
        naam = str(srow.get("titel") or (src_row[cols["name"]] if src_row is not None else "") or "")
        dagen = bepaal_dagen(content, srow.get("aantal_dagen_bron"))
        verdict = str(srow.get("verdict", "") or "")
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
            print(f"  {tid:>6}  {naam[:42]:42} -> {uitkomst['modus']:9}{afwijking}{nb}")
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
    for kolom in ("modus_reviewer", "guidance_reviewer", "modules_nb_reviewer"):
        if kolom not in scored.columns:
            scored[kolom] = ""

    if verbose:
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

    for pad in sorted(glob.glob(os.path.join(trainingen_dir, "*.json"))):
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
    """
    rij = {
        "training_id": res.training_id, "titel": res.titel,
        "oude_titel": res.oude_titel, "status": res.status,
        "modus": res.modus, "modus_voorstel": res.modus_voorstel,
        "spec_versie": res.spec_versie,
        # Onder welke few-shot is deze tekst geschreven? Valt een hele batch op dezelfde
        # manier tegen, dan is dit meestal de verklaring en niet de spec.
        "goud_voorbeelden": " | ".join(res.goud_voorbeelden),
        "reden": res.reden, "thin": res.thin,
        "n_flags": len(res.flags), "flags": " | ".join(res.flags),
        "judge_confidence": (res.judgment or {}).get("judge_confidence", ""),
        "toegepaste_acties": " | ".join(res.toegepaste_acties),
        "approve_edit": "",   # reviewer vult in: approve / edit / reject
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
    user_text = (
        f"Titel: {titel}\n"
        f"Persona: {b.persona}\n"
        f"Aantal dagen: {b.dagen if b.dagen is not None else 'onbekend'}\n\n"
        f"KERN — het niveau van deze training; schrijf daar nooit boven:\n"
        f"{b.kern_definitief}\n\n"
        f"{BESLISSING_UITLEG}\n\n"
        "GOEDGEKEURDE ACTUALISERINGEN — dit is het enige wat er mag veranderen. Staat er een\n"
        "VOORWAARDE bij, dan is die bindend:\n"
        f"{_opsomming(x.als_instructie() for x in b.goedgekeurd)}\n\n"
        "NIET DOEN — afgewezen door de reviewer:\n"
        f"{_opsomming(x.als_instructie() for x in b.afgewezen)}\n\n"
        + (f"Aanwijzing van de reviewer: {b.guidance_reviewer}\n\n"
           if b.guidance_reviewer.strip() else "")
        + "OPDRACHT — deze training staat al in de nieuwe stijl en wordt NIET herschreven.\n"
          "Voer alleen de goedgekeurde actualiseringen hierboven door. Lever uitsluitend de\n"
          "kopjes die daardoor veranderen, elk in zijn geheel en in dezelfde stijl als nu.\n"
          "Raakt een actie maar één kopje, lever dan ook maar één kopje. Verander niets aan\n"
          "de kopjes die je weglaat — die blijven letterlijk staan.\n\n"
        + "HUIDIGE VERSIE:\n" + huidige_versie_blok(content, titel)
    )
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
    if content.get("follow_up"):
        content["follow_up"], gewijzigd = normaliseer_follow_up(content["follow_up"])
        flags += [f"vervolgstappen-titel aangepast: {g}" for g in gewijzigd]
    if naam != titel:
        flags.append(f"titel aangepast: {naam} -> {titel}")

    # Vaste teksten altijd verversen, ook op dit pad. "Overnemen" gaat over de geschreven
    # tekst; de boilerplate is van ons en volgt het template. Zonder dit zou een training die
    # niemand aanraakt met de vorige generatie vaste teksten in het CMS blijven staan.
    content, ververst = uit.ververs_vaste_teksten(content, titel, b.modules_nb)
    flags += [f"vaste tekst bijgewerkt: {v}" for v in ververst]

    reden = "voldoet al aan het actuele format"
    toegepast: list[str] = []
    if b.goedgekeurd:
        content, aangepast = actualiseer_content(client, b, content, titel)
        toegepast = [f"{x.nr}. {x.actie}" + (f" [{x.voorwaarde}]" if x.voorwaarde else "")
                     for x in b.goedgekeurd]
        if aangepast:
            flags += [f"geactualiseerd: {a}" for a in aangepast]
            reden = f"{reden}; {len(b.goedgekeurd)} actualisering(en) doorgevoerd"
        else:
            flags.append("goedgekeurde actualiseringen leverden geen wijziging op")

    # Modules tellen hier sinds kort mee: zolang `goud_naar_check_input` ze oversloeg, gaf
    # elke overgenomen training een misleidend schone lijst. Training 328 bleek zo modules
    # met 0 en 1 sub-bullets te hebben zonder dat iemand dat zag.
    rw = goud_naar_check_input(content, titel)
    flags += [str(i) for i in checks.check_rewrite(rw, {"naam": titel})]
    res = RewriteResult(b.training_id, titel, OVERGENOMEN, reden=reden,
                        flags=flags, oude_titel=naam, modus="overnemen",
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


def schrijf_training_artefacten(json_dir: str, tid: Any, res: RewriteResult,
                                content_uit: dict) -> dict[str, str | None]:
    """De twee artefacten per training: de lossless JSON en het leesbare markdown-document.

    Eén plek, zodat batch, hergeneratie en de losse notebook-cel hetzelfde wegschrijven.
    De markdown is exact de weergave die de judge beoordeelt en die het notebook onder de
    cel toont -- om terug te lezen zonder de JSON open te klappen. Zonder document
    (`error`/`rejected`) is er niets te renderen; een oudere .md van dezelfde training zou
    dan bij een nieuwere JSON gaan liggen, dus die gaat weg.

    Geeft de paden terug ({"json": ..., "md": ... of None}).
    """
    os.makedirs(json_dir, exist_ok=True)
    json_pad = os.path.join(json_dir, f"{tid}.json")
    _schrijf_atomisch(json_pad, lambda f: json.dump({
        "training_id": tid, "titel": res.titel, "oude_titel": res.oude_titel,
        "status": res.status, "reden": res.reden, "thin": res.thin, "flags": res.flags,
        "modus": res.modus, "modus_voorstel": res.modus_voorstel,
        "spec_versie": res.spec_versie, "goud_voorbeelden": res.goud_voorbeelden,
        "toegepaste_acties": res.toegepaste_acties,
        # writer_out is wat de schrijver letterlijk leverde; nodig om later één kopje te
        # hergenereren (aanpak_invulling zit ingebakken in de vaste Aanpak-alinea).
        "writer_out": res.writer_out,
        "document": res.document, "content": content_uit,
        "judgment": res.judgment,
    }, f, ensure_ascii=False, indent=2, default=_json_default))

    md_pad = os.path.join(json_dir, f"{tid}.md")
    if res.document:
        _schrijf_atomisch(md_pad, lambda f: f.write(uit.render_markdown(res.document, res.titel)))
    else:
        if os.path.exists(md_pad):
            os.remove(md_pad)
        md_pad = None
    return {"json": json_pad, "md": md_pad}


def bewaar_training(out_dir: str, res: RewriteResult,
                    content_bron: dict | None = None) -> dict[str, str | None]:
    """Eén los resultaat wegschrijven naar `<out_dir>/trainingen/`.

    Voor de notebook-cel die één training herschrijft: zonder dit blijft dat resultaat in
    het geheugen en staat de markdown die je onder de cel leest nergens op schijf.
    `herschreven.xlsx` blijft ongemoeid -- dat sheet vullen de batch (sectie 6) en
    `hergenereer_kopje_op_schijf` (sectie 8).
    """
    content_uit = uit.document_to_content(res.document, content_bron or {}) if res.document else {}
    return schrijf_training_artefacten(os.path.join(out_dir, "trainingen"),
                                       res.training_id, res, content_uit)


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


def hergenereer_kopje_op_schijf(scored_path: str, source_path: str, training_id: Any,
                                kopje: str, comment: str = "", *, besluiten_path: str,
                                out_dir: str = "herschreven", judge: bool = True,
                                verbose: bool = True) -> RewriteResult:
    """Hergenereert één kopje van een al herschreven training en slaat het resultaat op.

    Zonder `comment` is het een gewone retry; met `comment` stuur je gericht bij
    ("de modules overlappen, voeg 2 en 4 samen"). Werkt de per-training-artefacten
    (JSON + markdown) en de rij in `herschreven.xlsx` bij.
    """
    json_dir = os.path.join(out_dir, "trainingen")
    pad = os.path.join(json_dir, f"{training_id}.json")
    if not os.path.exists(pad):
        raise FileNotFoundError(f"{pad} bestaat niet; herschrijf deze training eerst.")
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

    # hervatten: rijen die al in de output staan tellen niet mee in de wachtrij
    klaar: set = set()
    out_path = os.path.join(out_dir, "herschreven.xlsx")
    if append and skip_existing and os.path.exists(out_path):
        bestaand_review = pd.read_excel(out_path, sheet_name=None).get("review")
        if bestaand_review is not None:
            klaar = set(bestaand_review["training_id"])

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
                 alleen_ids=None):
    """Herschrijft de trainingen en schrijft de artefacten in `out_dir`.

    - trainingen/<id>.json   lossless: document + CMS-content + oordeel
    - trainingen/<id>.md     het leesbare document (kopstructuur van het template)
    - herschreven.xlsx       tabblad `cms` (id/name/content) + tabblad `review`
    """
    import pandas as pd
    os.makedirs(out_dir, exist_ok=True)
    json_dir = os.path.join(out_dir, "trainingen")
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

    if not len(gekozen):
        # niets te doen: geen client openen en het bestaande sheet met rust laten
        for regel in _wachtrij_waarschuwingen(q, start, limit, alleen_ids):
            if not verbose:
                print(regel)
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
        res, content_uit = neem_over(b, client)
        cms_records.append({"id": tid, "name": res.titel,
                            "content": json.dumps(content_uit, ensure_ascii=False, default=_json_default)})
        review_records.append(_review_rij(res, content_uit, content_bron))
    if verbose and len(overnemen):
        print(f"{len(overnemen)} trainingen op modus 'overnemen' doorgezet")

    # 2. de trainingen die wél herschreven moeten worden (modus stijl/format/volledig)
    for n, (_, srow) in enumerate(scored_sel.iterrows(), start=1):
        scored_dict = {k: srow[k] for k in scored_sel.columns}
        tid = scored_dict.get("training_id")
        naam = str(scored_dict.get("titel", "") or "")
        src_row = src_by_id.get(tid)
        content_bron = parse_content(src_row[cols["content"]]) if src_row is not None else {}

        if scored_dict.get("ok") is False:
            res = RewriteResult(tid, naam, "error", reden="scoring mislukt")
        else:
            if src_row is None and verbose:
                print(f"  (geen bron gevonden voor id {tid}; alleen scorer-feiten)")
            if not naam and src_row is not None:
                naam = str(src_row[cols["name"]])
            b = build_briefing(scored_dict, content_bron, naam, per_training.get(tid, []))
            res = rewrite_one(client, b, catalog, boom)

        content_uit = uit.document_to_content(res.document, content_bron) if res.document else {}

        schrijf_training_artefacten(json_dir, tid, res, content_uit)

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
    a = p.parse_args()
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
        if not a.geen_llm and not os.getenv("ANTHROPIC_API_KEY"):
            raise SystemExit("Zet ANTHROPIC_API_KEY, of gebruik --geen-llm.")
        modus_voorstellen(a.scored, a.source, a.scan_modus, met_llm=not a.geen_llm)
        return
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("Zet ANTHROPIC_API_KEY (in een .env-bestand of je omgeving).")
    rewrite_file(a.scored, a.source, a.out_dir, besluiten_path=a.besluiten,
                 start=a.start, limit=a.limit, append=not a.no_append, alleen_ids=a.ids)


if __name__ == "__main__":
    main()
