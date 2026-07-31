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

MODEL = "claude-opus-5"          # generatie profiteert van Opus; makkelijk te wisselen
KLEIN_MODEL = "claude-haiku-4-5"   # keuze uit een shortlist; geen generatie
MAX_TOKENS = 16000
THINKING = {"type": "adaptive"}    # adaptieve thinking voor schrijf-/oordeelskwaliteit
MAX_REVISIONS = 2                  # code-check + judge revisies vóór mens-wachtrij
N_SHORTLIST = 30                   # kandidaten die Python uit de catalogus voorselecteert
N_VERVOLG = 6                      # vervolgtrainingen die uiteindelijk in de tekst komen

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
BEOORDELINGSSPEC = os.path.join(_HERE, "beoordelingsspec_herschrijven_v1.md")
CATALOG_PATH = os.path.join(_HERE, "vervolgtraining.json")
TREE_PATH = os.path.join(_HERE, "vervolgtrainingen_tree.json")

# statussen voor routing
APPROVED = "approved"
NEEDS_REVISION = "needs-revision"
HUMAN_QUEUE = "human-queue"
OVERGENOMEN = "overgenomen"   # stond al in de nieuwe stijl; ongewijzigd doorgezet

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
                "description": "Eén of twee groepen; samen 3-6 titels.",
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
(bijvoorbeeld verdiepen versus verbreden).

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
            groepen.append({"intro": str(groep.get("intro") or "").strip(),
                            "titels": titels[:N_VERVOLG]})
    return groepen[:2]


# ---------------------------------------------------------------------------
# 4. INFO-PASSING: scorer-rij + brontekst -> RewriteBriefing
# ---------------------------------------------------------------------------

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

    @property
    def thin(self) -> bool:
        return self.verdict in ("dun", "redelijk")

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
                "description": "Kopje Overzicht. Één alinea, 55-65 woorden, begint met 'Wil je …'. Geen bullets."},
            "inleiding": {"type": "string",
                "description": "Kopje Inleiding. 180-210 woorden, verdiepend op Overzicht. "
                               "Schrijf NIET het bedrijfstrainingblok; dat plaatst de code."},
            "modules": {
                "type": "object",
                "description": "Kopje Modules. 4-6 modules; per module 3-6 sub-bullets, aantal moet variëren.",
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
                "description": "Kopje Doelgroep. Één zin, begint met 'Deze training is voor …'. Geen functietitels/'professionals'."},
            "voorkennis": {"type": "string",
                "description": "Kopje Voorkennis. Één zin. Laat leeg als geen voorkennis nodig is (code plaatst de fallbackzin)."},
            "aanpak_invulling": {"type": "string",
                "description": "Kopje Aanpak. Alleen de [.....]-invulling: één woord of enkele woorden."},
            "doelen": {"type": "array", "items": {"type": "string"},
                "description": "Kopje Doelen. 4-5 doelen in de infinitief MET 'te', aansluitend op de "
                               "vaste introzin 'Na deze training ben je in staat om:' — dus "
                               "'Dashboards te bouwen die de juiste vraag beantwoorden', niet "
                               "'Dashboards bouwen'. Herhaal 'in staat' niet; dat staat al in de "
                               "introzin. Hoofdletter aan het begin, zonder de introzin."},
            "kortste_omschrijving": {"type": "string",
                "description": "Kopje Kortste omschrijving. Max 200 tekens, begint met 'Wil je …'. Ingedikte versie van Overzicht."},
            "nieuwe_titel": {"type": "string",
                "description": "Optioneel. De code maakt zelf al een titel in de nieuwe stijl "
                               "('Cursus XML' -> 'Training XML'). Lever hier alleen iets als dat "
                               "mechanische resultaat krom loopt. Nooit 'cursus' of 'opleiding'."},
            "notities": {"type": "string",
                "description": "Optioneel: signaleer 'thin' (dunne bron, veel geconstrueerd) of een structurele twijfel."},
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


# De vier trainingen uit het goud-corpus die élke harde check halen (lengtes, openingszinnen,
# doelen in te-infinitief). De andere 74 zijn historisch materiaal van wisselende kwaliteit --
# 59 falen de Inleiding-lengte, 50 het Overzicht -- en zijn dus geen voorbeeld.
# Draai `checks_over_goud()` opnieuw als je een regel verandert; die meting levert deze lijst.
GOUD_VOORBEELDEN = (2730, 3046, 3101, 3125)
GOUD_DIR = os.path.join(_HERE, "herschreven", "goud")


# Het goud dateert van vóór de huidige introzin: 47 van de 78 trainingen openen hun doelen met
# "Na deze training heb je handvatten om:". Als few-shot demonstreert dat precies de zin die de
# schrijfspec verbiedt. De bullets eronder zijn al te-infinitief en lopen ongewijzigd door op de
# nieuwe zin, dus alleen de introregel hoeft om.
_GOUD_DOELEN_INTRO_RE = re.compile(r"^Na deze training[^\n]*", re.I)


def _actualiseer_doelen_intro(tekst: str) -> str:
    return _GOUD_DOELEN_INTRO_RE.sub(sjabloon.DOELEN_INTRO, tekst, count=1)


def goud_voorbeelden(n: int = 2, goud_dir: str = GOUD_DIR) -> str:
    """Twee voorbeelden uit het goud, als tekstblok voor de gecachete system-prefix.

    Vaste selectie, niet per training: een wisselende prefix maakt de prompt-cache waardeloos.
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
        blok = [f"### {d.get('titel', '')}"]
        for kop, sleutel in (("Overzicht", "summary"), ("Modules", "modules"),
                             ("Doelen", "objectives")):
            tekst = clean_text(c.get(sleutel, ""), d.get("titel", ""))
            if sleutel == "objectives":
                tekst = _actualiseer_doelen_intro(tekst)
            if tekst:
                blok.append(f"**{kop}**\n{tekst}")
        delen.append("\n\n".join(blok))
    if not delen:
        return ""
    return ("VOORBEELDEN — trainingen die al in de nieuwe stijl staan en alle regels halen.\n"
            "Neem de vorm over, niet de inhoud.\n\n" + "\n\n---\n\n".join(delen))


def build_writer_system() -> list[dict]:
    prefix = "\n\n---\n\n".join([_read(SCHRIJFSPEC), _read(HUMANISERING), _read(STIJLREGISTER)])
    voorbeelden = goud_voorbeelden()
    if voorbeelden:
        prefix += "\n\n---\n\n" + voorbeelden
    instr = ("Je herschrijft één training naar de nieuwe stijl. Volg de schrijfspec hierboven "
             "letterlijk (lengtes, verplichte openingszinnen, persona-toon, 'je'-vorm). Schrijf "
             "ALLEEN de generatieve kopjes en roep tot slot het tool `submit_rewrite` aan. Verzin "
             "geen feiten (versies/vendors/cijfers) die niet in de bron of de feiten staan.")
    return [{"type": "text", "text": instr + "\n\n---\n\n" + prefix,
             "cache_control": {"type": "ephemeral"}}]


def build_judge_system() -> list[dict]:
    """Beoordelingsspec + dezelfde stijlbestanden als de schrijver.

    De beoordelingsspec verwees al naar `humanisering_nl.md` zonder dat de judge dat bestand
    ooit te zien kreeg -- hij kon LLM-frasen en verboden woorden dus niet handhaven. Schrijver
    en judge horen tegen dezelfde definitie van "goed" te oordelen, dus krijgen ze hier
    letterlijk dezelfde stijlteksten. Het goud gaat níét mee: dat is schrijfmateriaal.
    """
    prefix = "\n\n---\n\n".join([_read(BEOORDELINGSSPEC), _read(HUMANISERING),
                                 _read(STIJLREGISTER)])
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


def build_writer_user(b: RewriteBriefing) -> str:
    dagen = str(b.dagen) if b.dagen is not None else "ONBEKEND (schat plausibel)"
    return (
        f"Titel: {b.nieuwe_titel}\n"
        f"Persona: {b.persona}\n"
        f"Aantal dagen: {dagen}\n"
        f"Verdict scorer: {b.verdict}{'  (THIN: markeer constructie)' if b.thin else ''}\n"
        f"Kern: {b.kern}\n\n"
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
        f"Rewrite-guidance: {b.rewrite_guidance or '(geen)'}\n\n"
        f"Brontekst:\n{b.source_text}"
    )


def build_judge_user(b: RewriteBriefing, document: dict) -> str:
    return (
        f"Persona: {b.persona}\n"
        f"Feiten (bruikbaar): " + (" | ".join(b.bruikbaar) or "(geen)") + "\n\n"
        f"{BESLISSING_UITLEG}\n\n"
        "Goedgekeurde actualiseringen (moeten verwerkt zijn):\n"
        f"{_opsomming(x.als_instructie() for x in b.goedgekeurd)}\n\n"
        "Afgewezen actualiseringen (mogen NIET terugkomen):\n"
        f"{_opsomming(x.actie for x in b.afgewezen)}\n\n"
        f"CONCEPT:\n{uit.render_markdown(document, b.nieuwe_titel)}"
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
    invulling = str(writer_out.get("aanpak_invulling", "")).strip() or sjabloon.AANPAK_FALLBACK
    voorkennis = str(writer_out.get("voorkennis", "") or "").strip() or sjabloon.VOORKENNIS_FALLBACK
    titel = bepaal_titel(writer_out, b)
    return {
        "titel": titel,
        "overzicht": str(writer_out.get("overzicht", "")).strip(),
        "inleiding": str(writer_out.get("inleiding", "")).strip(),
        "modules": {
            "opening": sjabloon.modules_opening(titel),
            "modules": (writer_out.get("modules") or {}).get("modules", []),
        },
        "doelgroep": str(writer_out.get("doelgroep", "")).strip(),
        "voorkennis": voorkennis,
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


def build_check_input(writer_out: dict, titels: list[str], titel: str = "") -> dict:
    """Platte structuur voor rewrite_checks (op de door de LLM geschreven velden)."""
    return {
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
                             thin=b.thin, oude_titel=b.titel)

    # audit-spoor: welke actualiseringen zijn meegegaan, en onder welke voorwaarde
    toegepast = [f"{x.nr}. {x.actie}" + (f" [{x.voorwaarde}]" if x.voorwaarde else "")
                 for x in b.goedgekeurd]

    titels, groepen = bepaal_vervolgstappen(client, b, catalog, boom)
    ctx = {"catalog_titles": catalog_titles(catalog) if catalog else None, "naam": b.nieuwe_titel}
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
        issues = checks.check_rewrite(build_check_input(writer_out, titels, titel), ctx)
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
                       toegepaste_acties=toegepast, oude_titel=b.titel, writer_out=writer_out)
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
                         toegepaste_acties=toegepast, oude_titel=b.titel)


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
        invulling = aanpak[len(prefix):].split("\n\n")[0].rstrip(". ")
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
    groepen = list(vervolg.get("groepen") or [])
    if not titels and catalog:
        titels, groepen = bepaal_vervolgstappen(client, b, catalog, boom)

    ctx = {"catalog_titles": catalog_titles(catalog) if catalog else None, "naam": b.nieuwe_titel}
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
        issues = (check(kandidaat) if check else []) + checks.check_soortwoorden(
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
        build_check_input(writer_out, titels, bepaal_titel(writer_out, b)), ctx)
    flags = [str(i) for i in checks.flags(alle_issues)]
    judgment = judge_document(client, b, nieuw_document) if judge else {}
    status = judgment.get("verdict", APPROVED) if judge else APPROVED
    return RewriteResult(
        b.training_id, bepaal_titel(writer_out, b),
        APPROVED if status == APPROVED else HUMAN_QUEUE,
        reden="" if status == APPROVED else judgment.get("human_reden", status),
        document=nieuw_document, flags=flags, judgment=judgment, thin=b.thin,
        toegepaste_acties=list(resultaat.get("toegepaste_acties") or []),
        oude_titel=b.titel, writer_out=writer_out)


def judge_document(client, b: RewriteBriefing, document: dict) -> dict:
    system = build_judge_system()
    user_text = build_judge_user(b, document)
    out = _call_tool(client, system, user_text, [SUBMIT_JUDGMENT], "submit_judgment")
    if not isinstance(out, dict) or "verdict" not in out:
        return {"verdict": HUMAN_QUEUE, "human_reden": "judge leverde geen bruikbaar oordeel"}
    return out


# ---------------------------------------------------------------------------
# 10. I/O (scored + source joinen; per-training JSON + samenvattings-xlsx)
# ---------------------------------------------------------------------------

def _load_scored(path: str):
    import pandas as pd
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]
    return df


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
        with open(os.path.join(goud_dir, f"{tid}.json"), "w", encoding="utf-8") as f:
            json.dump({"training_id": tid, "titel": str(row[cols["name"]]),
                       "content": parse_content(row[cols["content"]])},
                      f, ensure_ascii=False, indent=2)
        n += 1
    if verbose:
        print(f"Goud-corpus: {n} trainingen in {goud_dir}/")
    return n


_LI_RE = re.compile(r"<li>(.*?)(?=<li>|</li>)", re.S)


def goud_naar_check_input(content: dict, titel: str = "") -> dict:
    """Goud-content (HTML) -> de platte writer-vorm, zodat de checks erover kunnen.

    Ruwe benadering: goed genoeg om te tellen welke regels het goud haalt, niet om mee te
    genereren. De modules-structuur laten we leeg -- geneste <ul> betrouwbaar terugparsen
    levert meer valkuilen op dan het antwoord waard is.
    """
    from score_trainings import clean_text
    plat = {k: clean_text(v, titel) if isinstance(v, str) else v for k, v in content.items()}
    intro = plat.get("intro", "").replace(sjabloon.BEDRIJFSTRAINING_KOP, "")
    intro = intro.replace(sjabloon.BEDRIJFSTRAINING_TEKST, "").strip()
    doelen = [clean_text(m, titel) for m in _LI_RE.findall(content.get("objectives", "") or "")]
    return {
        "overzicht": plat.get("summary", ""),
        "inleiding": intro,
        "doelgroep": plat.get("target_audience", ""),
        "voorkennis": plat.get("prior_knowledge", ""),
        "doelen": [d for d in doelen if d],
        "kortste_omschrijving": plat.get("summary_edudex", ""),
        "nieuwe_titel": titel,
    }


def checks_over_goud(goud_dir: str = GOUD_DIR, verbose: bool = True) -> dict:
    """Kalibratie: hoe vaak faalt elke harde regel op het goud-corpus?

    Het goud is referentie, geen norm -- daarom staat dit hier en niet in test_rewrite.py.
    Valt een regel bij meer dan de helft van het corpus om, dan is die regel verdacht en
    niet de training. De trainingen die álles halen zijn de few-shot-kandidaten
    (`GOUD_VOORBEELDEN`).
    """
    import glob
    from collections import Counter
    tellingen: Counter = Counter()
    schoon: list[tuple[Any, str]] = []
    bestanden = sorted(glob.glob(os.path.join(goud_dir, "*.json")))
    for pad in bestanden:
        with open(pad, encoding="utf-8") as f:
            d = json.load(f)
        titel = d.get("titel", "")
        rw = goud_naar_check_input(d.get("content") or {}, titel)
        # modules zitten er bewust niet in; die checks zouden altijd falen
        hard = [i for i in checks.hard_fails(checks.check_rewrite(rw)) if i.section != "modules"]
        for issue in hard:
            tellingen[f"{issue.section}: {issue.code}"] += 1
        if not hard:
            schoon.append((d.get("training_id"), titel))
    if verbose:
        print(f"{len(bestanden)} goud-trainingen; aantal dat elke harde regel NIET haalt:")
        for regel, n in tellingen.most_common():
            print(f"  {n:3d}  {regel}")
        print(f"\n{len(schoon)} halen alles -> kandidaat voor GOUD_VOORBEELDEN:")
        for tid, titel in schoon:
            print(f"  {tid:6} {titel}")
    return {"tellingen": dict(tellingen), "schoon": schoon, "totaal": len(bestanden)}


def _review_rij(res: RewriteResult, content: dict) -> dict:
    """Eén rij voor het review-tabblad: status + elk kopje in platte tekst."""
    rij = {
        "training_id": res.training_id, "titel": res.titel,
        "oude_titel": res.oude_titel, "status": res.status,
        "reden": res.reden, "thin": res.thin,
        "n_flags": len(res.flags), "flags": " | ".join(res.flags),
        "judge_confidence": (res.judgment or {}).get("judge_confidence", ""),
        "toegepaste_acties": " | ".join(res.toegepaste_acties),
        "approve_edit": "",   # reviewer vult in: approve / edit / reject
    }
    plat = uit.content_naar_platte_tekst(content, res.titel) if content else {}
    for kopje in sjabloon.KOPJES:
        rij[kopje.kop] = plat.get(kopje.cms, "")
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


def neem_over(tid: Any, naam: str, content_bron: dict) -> tuple[RewriteResult, dict]:
    """Een training die al in de nieuwe stijl staat, ongewijzigd doorzetten.

    Niet herschrijven (dat zou een goede tekst alleen maar slechter maken), maar wel in
    `herschreven.xlsx` zetten -- anders is dat sheet geen compleet CMS-document. De
    code-check draait er wel overheen, zodat afwijkingen zichtbaar worden in `flags`.
    Geeft (resultaat, CMS-content) terug.
    """
    content = dict(content_bron or {})
    titel = sjabloon.nieuwe_titel(naam)
    flags: list[str] = []
    if content.get("follow_up"):
        content["follow_up"], gewijzigd = normaliseer_follow_up(content["follow_up"])
        flags += [f"vervolgstappen-titel aangepast: {g}" for g in gewijzigd]
    if naam != titel:
        flags.append(f"titel aangepast: {naam} -> {titel}")
    rw = goud_naar_check_input(content, titel)
    flags += [str(i) for i in checks.check_rewrite(rw) if i.section != "modules"]
    res = RewriteResult(tid, titel, OVERGENOMEN, reden="stond al in de nieuwe stijl",
                        flags=flags, oude_titel=naam)
    return res, content


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
    with open(json_pad, "w", encoding="utf-8") as f:
        json.dump({
            "training_id": tid, "titel": res.titel, "oude_titel": res.oude_titel,
            "status": res.status, "reden": res.reden, "thin": res.thin, "flags": res.flags,
            "toegepaste_acties": res.toegepaste_acties,
            # writer_out is wat de schrijver letterlijk leverde; nodig om later één kopje te
            # hergenereren (aanpak_invulling zit ingebakken in de vaste Aanpak-alinea).
            "writer_out": res.writer_out,
            "document": res.document, "content": content_uit,
            "judgment": res.judgment,
        }, f, ensure_ascii=False, indent=2)

    md_pad = os.path.join(json_dir, f"{tid}.md")
    if res.document:
        with open(md_pad, "w", encoding="utf-8") as f:
            f.write(uit.render_markdown(res.document, res.titel))
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


def _werk_xlsx_rij_bij(out_path: str, res: RewriteResult, content_uit: dict, verbose=True):
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
                               "content": json.dumps(content_uit, ensure_ascii=False)}])
        cms = pd.concat([cms, nieuw], ignore_index=True).drop_duplicates(
            subset="id", keep="last")
    review = pd.concat([review, pd.DataFrame([_review_rij(res, content_uit)])],
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
    _werk_xlsx_rij_bij(os.path.join(out_dir, "herschreven.xlsx"), res, content_uit, verbose)
    if verbose:
        print(f"{res.titel} — kopje '{kopje}' opnieuw gegenereerd -> {res.status}"
              + (f" ({res.reden})" if res.reden else ""))
    return res


def rewrite_file(scored_path: str, source_path: str, out_dir: str, *,
                 besluiten_path: str | None = None, start: int = 0,
                 limit: int | None = None, skip_herschreven: bool = True,
                 append: bool = True, skip_existing: bool = True, verbose: bool = True):
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

    # Al herschreven trainingen niet opnieuw genereren, maar wél doorzetten: het scoresheet
    # is de bron van waarheid voor wat er in het CMS-document hoort.
    overnemen = scored.iloc[0:0]
    if skip_herschreven and "herschreven" in scored.columns:
        al_klaar = scored["herschreven"] == 1
        overnemen = scored[al_klaar]
        scored = scored[~al_klaar]

    # hervatten: rijen die al in de output staan overslaan
    bestaand_cms, bestaand_review = None, None
    if append and os.path.exists(out_path):
        vorige = pd.read_excel(out_path, sheet_name=None)
        bestaand_cms = vorige.get("cms")
        bestaand_review = vorige.get("review")
        if skip_existing and bestaand_review is not None:
            klaar = set(bestaand_review["training_id"])
            scored = scored[~scored["training_id"].isin(klaar)]
            overnemen = overnemen[~overnemen["training_id"].isin(klaar)]
            if verbose and klaar:
                print(f"{len(klaar)} trainingen stonden al in {out_path} -> overgeslagen")

    scored = scored.iloc[start:]
    if limit:
        scored = scored.iloc[:limit]

    catalog = load_catalog()
    if verbose and not catalog:
        print(f"LET OP: {CATALOG_PATH} ontbreekt -> Vervolgstappen-titels leeg/geflagd.")
    boom = load_tree(catalog)
    if verbose and catalog and not boom["paden"]:
        print(f"LET OP: {TREE_PATH} ontbreekt -> vervolgtrainingen alleen op keyword-overlap.")
    client = make_client() if len(scored) else None

    cms_records, review_records = [], []

    # 1. de al herschreven trainingen, ongewijzigd (geen API-calls)
    for _, srow in overnemen.iterrows():
        tid = srow["training_id"]
        src_row = src_by_id.get(tid)
        if src_row is None:
            if verbose:
                print(f"  (id {tid} staat op herschreven=1 maar heeft geen bron; overgeslagen)")
            continue
        naam = str(srow.get("titel") or src_row[cols["name"]] or "")
        res, content_uit = neem_over(tid, naam, parse_content(src_row[cols["content"]]))
        cms_records.append({"id": tid, "name": res.titel,
                            "content": json.dumps(content_uit, ensure_ascii=False)})
        review_records.append(_review_rij(res, content_uit))
    if verbose and len(overnemen):
        print(f"{len(overnemen)} trainingen met herschreven=1 ongewijzigd overgenomen")

    # 2. de trainingen die wél herschreven moeten worden
    for n, (_, srow) in enumerate(scored.iterrows(), start=1):
        scored_dict = {k: srow[k] for k in scored.columns}
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
                                "content": json.dumps(content_uit, ensure_ascii=False)})
        review_records.append(_review_rij(res, content_uit))
        if verbose:
            print(f"[{n}/{len(scored)}] {naam[:45]:45} -> {res.status}"
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
    p.add_argument("--source", required=True, help="bron-xlsx met content-JSON (brontekst)")
    p.add_argument("--besluiten", required=True, help="genormaliseerde besluiten.xlsx")
    p.add_argument("--out-dir", default="herschreven")
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-append", action="store_true", help="overschrijf i.p.v. hervatten")
    p.add_argument("--goud", action="store_true", help="exporteer alleen het goud-corpus")
    a = p.parse_args()
    if a.goud:
        export_goud_corpus(a.source, a.out_dir)
        return
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit("Zet ANTHROPIC_API_KEY (in een .env-bestand of je omgeving).")
    rewrite_file(a.scored, a.source, a.out_dir, besluiten_path=a.besluiten,
                 start=a.start, limit=a.limit, append=not a.no_append)


if __name__ == "__main__":
    main()
