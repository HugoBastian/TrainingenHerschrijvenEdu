"""
rewrite_checks.py
=================
Deterministische code-check voor een herschreven training (de 9 kopjes).
Geen LLM: plain Python-functies die een lijst `Issue`-objecten teruggeven,
gesplitst in HARD-FAIL (moet terug naar de schrijver) en FLAG (mag mee naar
judge/review). Elke flag heeft daarnaast een tier (hoog/mechanisch/laag) die zegt
hoeveel aandacht van een mens hij vraagt; zie "Tiers" verderop. Zelfde geest als
`finalize_scores` in score_trainings.py: de code beslist deterministisch, het model
schrijft alleen.

Verwachte input `rewrite` (de gestructureerde schrijver-output, `submit_rewrite`):
    {
      "overzicht":  "Wil je ... (richtlijn 55-65 woorden, 1 alinea)",
      "inleiding":  "... (richtlijn 180-210 woorden, schuift mee met het aantal dagen)",
      "modules":   { "modules": [ {"titel": "...", "bullets": ["...", "..."]}, ... ] },
      "aanpak_invulling":        "... (alleen de [....]-invulling)",
      "doelgroep":              "Deze training is voor ...",
      "voorkennis":             "... (1 zin) of de vaste fallbackzin",
      "doelen":                 ["... te ...en", "... te ...en", ...],    # 4-5 bullets, te-infinitief
      "vervolgstappen_titels": ["Titel A", "Titel B", ...],              # uit de catalogus
      "vervolgstappen_groepen": [{"intro": "...", "titels": [...]}, ...],  # uit de retrieval
      "kortste_omschrijving":   "Wil je ... (<=200 tekens; harde grens)",
      "nieuwe_titel":           "Training ...",                          # nooit cursus/opleiding
    }

Lengtes zijn richtlijnen met een vangrail eromheen: buiten de richtlijn is het een FLAG,
pas buiten de vangrail een HARD-FAIL. Zie "Lengtebanden" verderop. De 200 tekens van de
Kortste omschrijving zijn de uitzondering -- die grens komt van Edudex en is wél hard.

Context `ctx` (optioneel):
    { "catalog_titles": {"Titel A", ...}, "naam": "Trainingsnaam", "dagen": 3,
      "acties": ["refresh: benoem ...", ...] }   # de goedgekeurde actualiseringen, kaal

Gebruik:
    issues = check_rewrite(rewrite, ctx)
    if hard_fails(issues):
        # terug naar de schrijver met format_issues(hard_fails(issues))
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Severity + Issue
# ---------------------------------------------------------------------------

HARD = "hard"   # moet gerepareerd worden -> terug naar schrijver
FLAG = "flag"   # signaal -> mee naar judge / menselijke review


@dataclass(frozen=True)
class Issue:
    section: str      # kopje-sleutel of "algemeen"
    severity: str     # HARD | FLAG
    code: str         # korte machine-code, bv. "lengte_woorden"
    message: str      # leesbare uitleg (gaat mee terug naar de schrijver)

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.section}: {self.message}"


def hard_fails(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == HARD]


def flags(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == FLAG]


def format_issues(issues: list[Issue]) -> str:
    """Bundelt issues tot tekst die als revisie-instructie de schrijver in kan."""
    return "\n".join(f"- {i}" for i in issues)


# ---------------------------------------------------------------------------
# Tiers: hoeveel aandacht van een mens vraagt deze flag?
# ---------------------------------------------------------------------------
#
# HARD/FLAG bepaalt wie een issue oplost (de schrijver of een mens). De tier beantwoordt een
# andere vraag: wat moet de reviewer lezen? Over de eerste 16 herschreven trainingen stonden
# 34 flags, waarvan 13 keer `lengte_richtlijn` (11 van de 16 trainingen) en 8 keer
# `zwakke_formulering`. Samen 62% van alles wat een mens onder ogen kreeg. Geen van die 13
# lengtes zat in de buurt van de vangrail -- Overzicht 81 t/m 86 op een band van 55-80 met
# vangrail 110, Inleiding 212 t/m 231 op 180-210 met vangrail 260, twee ervan één woord over
# de grens. Een kolom die voor twee derde uit "een woord over de richtlijn" bestaat, leest
# niemand meer met aandacht; vandaar drie tiers in plaats van één lijst.
#
#   hoog        de reviewer moet de tekst lezen en oordelen; dit kan een echte fout zijn
#   mechanisch  eenduidig te repareren zonder de tekst te wegen: vervang dit woord
#   laag        een meting buiten de richtlijn maar binnen de vangrail, of telemetrie
#
# Alles wat hier niet in staat is `hoog`. Dat is bewust de goede kant om op te falen: een
# nieuwe check komt binnen als iets waar een mens naar kijkt en zakt pas als de meting laat
# zien dat hij vaak vuurt zonder dat er iets mis is. Dezelfde volgorde als bij HARD/FLAG.
TIER_HOOG = "hoog"
TIER_MECHANISCH = "mechanisch"
TIER_LAAG = "laag"
TIERS: tuple[str, ...] = (TIER_HOOG, TIER_MECHANISCH, TIER_LAAG)

TIER_PER_CODE: dict[str, str] = {
    # Metingen. De boodschap zegt het zelf al ("alleen bijstellen als de tekst er beter van
    # wordt"); buiten de vangrail is het geen flag meer maar een HARD en komt het hier niet
    # langs. `invulling_voegwoord` meldt bovendien iets dat de code al heeft weggehaald --
    # dat is telemetrie over de prompt en geen opdracht aan een mens. `catalogus_ontbreekt`
    # vuurt overal zodra er zonder catalogus gemeten wordt.
    "lengte_richtlijn": TIER_LAAG,
    "zin_lang": TIER_LAAG,
    "voorkennis_lang": TIER_LAAG,
    "invulling_voegwoord": TIER_LAAG,
    "catalogus_ontbreekt": TIER_LAAG,
    # Eén woord vervangen, het alternatief staat in de boodschap. De reviewer hoeft de zin
    # er niet voor te wegen -- de je-vorm en het Nederlandse woord zijn voorgeschreven.
    "anglicisme": TIER_MECHANISCH,
    "contactzin_zonder_dan": TIER_MECHANISCH,
    "soortwoord_hoofdletter": TIER_MECHANISCH,
    "meeting": TIER_MECHANISCH,
    "u_vorm": TIER_MECHANISCH,
    "dubbel_in_staat": TIER_MECHANISCH,
}


def tier(issue: Issue) -> str:
    """De tier van dit issue; onbekende codes zijn `hoog`."""
    return TIER_PER_CODE.get(issue.code, TIER_HOOG)


def per_tier(issues: list[Issue]) -> dict[str, list[str]]:
    """Groepeert issues per tier tot leesbare regels: één regel per opmerking.

    Dezelfde opmerking in twee kopjes is voor een reviewer één ding om over te beslissen en
    geen twee: training 27 kreeg "zelfstandig" in het Overzicht én de Inleiding, 3159 in de
    Modules én de Doelen. Die vouwen samen tot "overzicht + inleiding: ...". Vergelijken
    gaat hoofdletterongevoelig, want de boodschap citeert het gevonden woord en dat staat
    aan het begin van een zin met een hoofdletter ('Zelfstandig' naast 'zelfstandig').

    Geeft alle drie de tiers terug, ook de lege -- de aanroeper hoeft niet te weten welke
    tiers er bestaan.
    """
    gebundeld: dict[tuple[str, str, str], tuple[Issue, list[str]]] = {}
    for i in issues:
        sleutel = (i.severity, i.code, i.message.lower())
        if sleutel in gebundeld:
            secties = gebundeld[sleutel][1]
            if i.section not in secties:
                secties.append(i.section)
        else:
            gebundeld[sleutel] = (i, [i.section])
    uit: dict[str, list[str]] = {t: [] for t in TIERS}
    for eerste, secties in gebundeld.values():
        uit[tier(eerste)].append(
            f"[{eerste.severity.upper()}] {' + '.join(secties)}: {eerste.message}")
    return uit


# ---------------------------------------------------------------------------
# Tekst-helpers
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]+(?:['-][A-Za-zÀ-ÿ0-9]+)*")
_HTML_RE = re.compile(r"<[^>]+>")
_SENTENCE_END_RE = re.compile(r"[.!?]+(?:\s|$)")
# placeholder-resten: [....], [naam training], {{ oplnaam }}, {iets}
_PLACEHOLDER_RE = re.compile(r"\[[^\]]*\.{2,}[^\]]*\]|\[naam[^\]]*\]|\{\{[^}]*\}\}|\{\s*\.{2,}\s*\}")
_BULLET_PREFIX_RE = re.compile(r"^\s*[-*•]\s+", re.M)
# "u"/"uw" als los tweede-persoons-woord (niet "u-vormig" e.d.)
_U_VORM_RE = re.compile(r"(?<![\w-])[Uu]w?(?![\w-])")

# Verboden LLM-frasen (uit humanisering_nl.md), hoofdletter-ongevoelig -> FLAG
BANNED_PATTERNS = [
    r"in de (?:snel veranderende|hedendaagse|moderne|dynamische) wereld van",
    r"in het (?:huidige|digitale) landschap",
    r"of het nu gaat om",
    r"duiken we (?:dieper )?in",
    r"ontdek de kracht van",
    r"ontgrendel het potentieel",
    r"naar een hoger niveau",
    r"niet alleen .{0,60}? maar ook",
    r"een (?:breed|ruim) scala aan",
    r"een schat aan",
    r"in een handomdraai",
    r"in no[- ]?time",
    r"waar wacht je nog op",
    r"zet (?:vandaag )?(?:nog )?de eerste stap",
    r"\b(?:naadloos|moeiteloos|cruciaal|essentieel|baanbrekend|revolutionair|ongekend)\b",
    r"\b(?:simpelweg|daadwerkelijk|gewoonweg)\b",
]
_BANNED_RE = [re.compile(p, re.I) for p in BANNED_PATTERNS]

# Marketingtaal / superlatieven -> FLAG
MARKETING_WORDS = ["de beste", "uniek", "gegarandeerd", "ongeëvenaard", "toonaangevend",
                   "wereldklasse", "state-of-the-art", "next-level", "game-changer"]

# Anglicismen en onnodige leenwoorden (humanisering_nl.md Sectie G) -> FLAG.
#
# Twee soorten in één lijst, met de Nederlandse tegenhanger erbij zodat de boodschap bruikbaar
# is: structureel vertaalde constructies ("werk je door de categorieen") en leenwoorden waar een
# gewoon Nederlands woord staat ("skills", "stakeholders").
#
# Bewust conservatief en op de eigen catalogus gemeten. Wat een echte vakterm kan zijn blijft
# eruit: "best practices" (13/78 trainingen, en het staat in onze eigen schrijfspec Sectie 1a),
# "governance" (15/78), "compliance" (7/78), "performance" (8/78), "impact" (15/78), "scope"
# (4/78). De structurele patronen hieronder vuren op het goud 0 tot 2 keer; de leenwoorden
# raken samen ongeveer een kwart van de trainingen. Vandaar FLAG en nooit HARD.
ANGLICISMEN: tuple[tuple[str, str], ...] = (
    (r"werk(?:t|en)?\s+(?:je|we)\s+door\s+de", "neem je de … door"),
    (r"onderscheid\b[^.]{0,30}\bkennen\b", "onderscheid maken tussen"),
    (r"\bin lijn met\b", "volgens / passend bij"),
    (r"op (?:een )?(?:dagelijkse|wekelijkse|regelmatige) basis", "dagelijks / wekelijks / regelmatig"),
    (r"\badresseer(?:t|en|de|d)?\b", "aanpakken / behandelen"),
    (r"\bimpacteer(?:t|en|de|d)?\b", "raken / beïnvloeden"),
    (r"\bcontrole (?:te )?nemen over\b", "de regie nemen over"),
    (r"\bzo snel als mogelijk\b", "zo snel mogelijk"),
    (r"\b(?:support|deliver|challeng|shar|align|committ)(?:en|t|de)\b",
     "ondersteunen / opleveren / bevragen / delen / afstemmen / vastleggen"),
    (r"\bskills\b", "vaardigheden"),
    (r"\bstakeholders?\b", "belanghebbenden / betrokkenen"),
    (r"\bmindset\b", "houding / denkwijze"),
    (r"\binsights?\b", "inzichten"),
    (r"\bchallenges\b", "uitdagingen / knelpunten"),
    (r"\btooling\b", "gereedschap / hulpmiddelen"),
    (r"\bhands[- ]on\b", "praktisch"),
    (r"\bissues\b", "knelpunten"),
    (r"\bawareness\b", "bewustzijn"),
    (r"\bownership\b", "eigenaarschap"),
    (r"\blearnings\b", "lessen / inzichten"),
    (r"\balignment\b", "afstemming"),
    (r"\bdeep[- ]dive\b", "verdieping"),
    (r"\bend[- ]to[- ]end\b", "van begin tot eind"),
)
_ANGLICISME_RE = [(re.compile(p, re.I), nl) for p, nl in ANGLICISMEN]

# Liggende streepjes: em-dash (U+2014) en en-dash (U+2013) -> HARD, zie `check_em_dash`.
# Het gewone koppelteken (-) staat er bewust niet bij: dat hoort in "data-analyse" en in
# "hands-on". Alleen de lange varianten, want die zijn nooit een koppelteken.
#
# Als escape geschreven en niet als teken. De boodschap van een HARD-issue gaat letterlijk
# terug naar de schrijver (`notes` in `rewrite_one`), dus een bestand dat het teken toont op
# de plek waar het verboden wordt, demonstreert het alsnog. Zelfde reden waarom de
# promptbestanden "[liggend streepje]" uitschrijven.
_EM_DASH_RE = re.compile("[\\u2014\\u2013]")

# Een voorwaardelijke bijzin met een opsomming in de voorwaarde -> FLAG, zie `check_reikwijdte`.
# De voorwaarde loopt van het werkwoord tot de komma vóór "dan" of tot een dubbele punt; verder
# dan één zin kijken we niet.
_REIKWIJDTE_RE = re.compile(
    r"\b(?:Werk|Ben|Zit|Houd|Sta|Doe)\s+je\b(?P<voorwaarde>[^.?!:]{0,140}?)\s*[,:]\s*dan\b",
    re.I)

# De deelnemer brengt zelf werkmateriaal mee -> HARD, zie `check_eigen_case`.
#
# Uit de review op training 3036 (Change Management voor DAMA-DMBOK), die het twee keer beloofde:
# "past alles toe op je eigen praktijkcase" en de modulebullet "Een eigen veranderopgave rond
# datamanagement inbrengen". Dat kan alleen bij een bedrijfstraining en dus nooit in de standaard
# beschrijving. De bron verleidt ertoe: daar staat "jouw praktijkcase", maar dat betekent dat je
# een praktijkcase *krijgt* om aan te werken, niet dat je er zelf een aanlevert.
#
# Drie vormen, alle drie hard, want de schrijver kan ze alle drie zelf repareren:
#
# `_INBRENG_RE` -- een inbreng-werkwoord met eigen werkmateriaal, in beide volgordes en ook
# gescheiden ("brengt … in", "levert … aan"). Bewust alleen op materiaal (case, opgave, opdracht,
# vraagstuk, dataset, project, document, proces, werkvraag, data, code) en niet op "situatie",
# "voorbeeld" of "vraag": daar gaat het over het gesprek in de zaal, en dat belooft
# `sjabloon.AANPAK_ALINEA_1` zelf al ("veel ruimte voor jouw vragen en werksituatie"). Met die
# grens vuurt hij 1 keer over de 78 goud-trainingen; mét "situatie" erbij 5 keer, en dat zijn
# dan vier terechte zinnen.
#
# `_EIGEN_CASE_RE` -- een bezittelijk voornaamwoord op een case. In een open inschrijving is een
# case er altijd een die wij leveren, dus "een praktijkcase" mag en "je eigen praktijkcase" niet;
# juist dat bezittelijke woord maakt de belofte die we niet waar kunnen maken. 7 van de 78, onder
# meer "jullie eigen casussen" en "jullie eigen praktijkcase" -- het goud maakt de fout zelf, dus
# dit is een fout en geen corpusconventie.
#
# `_CASE_HERKOMST_RE` -- de herkomst in plaats van het bezit: "casussen uit je eigen praktijk",
# "materiaal uit je eigen werk". Hetzelfde probleem met het bezittelijke woord één zelfstandig
# naamwoord verderop, waar de twee patronen hierboven niet komen. 0 van de 78.
#
# Twee vallen zitten erin verwerkt. "in kaart brengen" is de idioomval bij de gescheiden vorm en
# staat er met een lookahead uit. En de woordklassen gebruiken `[\w-]` en niet `\w`, want een
# koppelteken is geen woordteken: zonder dat glipten "AI-toepassingscasus" en "je use-case"
# erlangs. Die twee kwamen aan het licht toen bleek dat twee van de vier few-shot-voorbeelden
# (3127 en 796) deze fout zelf demonstreerden terwijl de check zweeg.
#
# Wat bewust NIET vuurt, want dat is precies wat we wél leveren: materiaal dat de deelnemer
# tíjdens de training maakt ("een eigen applicatie ontwerpen", "een roadmap voor je eigen
# organisatie opstellen") en alles wat over toepassen ná de training gaat ("in te zetten in je
# eigen projecten"). Zie schrijfspec Sectie 0.15: het eindproduct dat de deelnemer zelf bouwt en
# meeneemt is uitdrukkelijk toegestaan.
_BEZIT = r"(?:eigen|jouw|jullie|je)"
_CASE_FAMILIE = r"[\w-]*(?:case|cases|casus|casussen)\b"
_MATERIAAL = (r"[\w-]*(?:case|cases|casus|casussen|opgave|opgaven|opdracht|opdrachten|vraagstuk|"
              r"vraagstukken|probleemstelling|probleemstellingen|dataset|datasets|project|"
              r"projecten|materiaal|document|documenten|proces|processen|werkvraag|werkvragen)"
              r"[\w-]*|(?:data|code)\b")
_EIGEN_MATERIAAL = rf"{_BEZIT}\s+(?:\w+\s+){{0,2}}?(?:{_MATERIAAL})"
_INBRENG_VAST = (r"in\s?te\s?brengen|inbreng\w*|ingebracht|mee\s?te\s?brengen|meebreng\w*|"
                 r"mee\s?te\s?nemen|mee\s?nemen|meenemen|meeneemt|aan\s?te\s?leveren|aanlever\w*|"
                 r"aan\s?te\s?dragen|aandrag\w*|voor\s?te\s?leggen|voorleg\w*")
_INBRENG_SCHEIDBAAR = r"breng\w*|neem\w*|lever\w*|draag\w*|draagt|leg\w*|legt"
_INBRENG_PARTIKEL = r"in(?!\s+kaart)|mee|aan|voor"
_INBRENG_RE = re.compile(
    rf"(?:{_EIGEN_MATERIAAL}[^.;:!?]{{0,50}}?\s(?:{_INBRENG_VAST})"
    rf"|(?:{_INBRENG_VAST})[^.;:!?]{{0,40}}?\s(?:van\s+)?{_EIGEN_MATERIAAL}"
    rf"|\b(?:{_INBRENG_SCHEIDBAAR})\s+(?:\w+\s+){{0,2}}?{_EIGEN_MATERIAAL}"
    rf"[^.;:!?]{{0,30}}?\s(?:{_INBRENG_PARTIKEL})\b)", re.I)
_EIGEN_CASE_RE = re.compile(
    rf"\b(?:{_BEZIT}\s+)?eigen\s+{_CASE_FAMILIE}"
    rf"|\b(?:jouw|jullie|je)\s+{_CASE_FAMILIE}", re.I)
_CASE_HERKOMST_RE = re.compile(
    rf"\b[\w-]*(?:case|cases|casus|casussen|materiaal|materialen)\b[^.;:!?]{{0,40}}?"
    rf"\b(?:uit|van)\s+{_BEZIT}\s+(?:eigen\s+)?"
    rf"(?:praktijk\w*|organisatie\w*|werk\w*|bedrijf\w*)", re.I)

# Formuleringen "aan de onderkant" (humanisering_nl.md Sectie D, schrijfspec Sectie 0.19) -> FLAG.
#
# De grootste groep opmerkingen uit reviewronde 2: de belofte klopt wel, maar hij is zo mager
# geformuleerd dat er geen training uit spreekt. Per patroon staat het sterkere alternatief in
# de boodschap -- dat is het hele punt, want het niveau mag niet omhoog, alleen de formulering.
#
# Gemeten op het goud: "plaatsen" 9/78, "zelfstandig" 11/78, "in elkaar zit" en "meepraten"
# allebei 0/78. Flag en geen hard fail: "plaatsen" kan legitiem zijn ("modules plaatsen in een
# tijdlijn"), en of "zelfstandig" hier iets toevoegt is een oordeel.
ZWAKKE_FORMULERINGEN: tuple[tuple[str, str], ...] = (
    (r"\bplaats(?:en|t)\b",
     "'plaatsen' zegt niet wat de deelnemer kan; schrijf 'de opbouw van X doorgronden', "
     "'het verschil tussen X en Y benoemen' of 'weten wanneer je X inzet'"),
    (r"\bin elkaar (?:zit|zitten|steekt)\b",
     "schrijf 'hoe X is opgebouwd' -- dezelfde belofte, wél een respectabele constructie"),
    (r"\b(?:mee te praten|meepraten|mee praten)\b",
     "'meepraten' is geen belofte waarvoor iemand betaalt; zeg binnen dezelfde scope wat de "
     "deelnemer écht overhoudt ('een stevige basis leggen in …', 'de structuur van X volledig "
     "begrijpen')"),
    (r"\bzelfstandig\b",
     "'zelfstandig' voegt alleen iets toe als de deelnemer daarna géén derde partij meer nodig "
     "heeft; is dat zo, schrijf dat dan ('zonder tussenkomst van derden')"),
)
_ZWAK_RE = [(re.compile(p, re.I), uitleg) for p, uitleg in ZWAKKE_FORMULERINGEN]

# Alles heet een training. "Examentraining" bevat geen van deze woorden en mag dus blijven.
_SOORTWOORD_RE = re.compile(
    r"\b(?:cursus(?:sen|se)?|opleiding(?:en)?|gebruikerscursus|examencursus|leergang(?:en)?)\b",
    re.I)

# Doelen staan in de te-infinitief, aansluitend op "Na deze training ben je in staat om:".
# Twee vormen: aaneengesloten ("te formuleren") en gesplitst ("voor te bereiden"). De meeste
# infinitieven eindigen op -en; de onregelmatige korte vormen staan er expliciet bij.
_TE_INFINITIEF_RE = re.compile(
    r"\bte\s+(?:\w+en|zijn|doen|gaan|staan|slaan|zien|hebben)\b", re.I)

# "In staat zijn om ..." is een van de aanbevolen causale constructies, maar hij zit al in de
# introzin: als bullet levert hij "ben je in staat om in staat te zijn om ...". Flag, geen hard
# fail -- de zin is niet fout, alleen dubbel.
_IN_STAAT_RE = re.compile(r"\bin staat\b", re.I)

# Hetzelfde woord twee keer achter elkaar. Ontstaat vooral op de naad tussen vaste tekst en
# geschreven tekst: "... ervaar je hoe" + invulling "hoe een data-analysetraject ..." gaf bij
# training 2347 "ervaar je hoe hoe ...". `sjabloon.schoon_invulling` haalt dat er nu af, maar de
# naad is niet de enige plek waar het kan ontstaan, dus staat de check op alle tekstvelden.
#
# Hard: een dubbel woord is nooit bedoeld en nooit een stijlkwestie. De uitzonderingen zijn
# verdubbelingen die in het Nederlands gewoon correct zijn, en "je je" is daarvan de enige die
# hier ook echt voorkomt: na inversie volgt het wederkerend voornaamwoord op het onderwerp
# ("maak je je de materie eigen", "verdiep je je verder"). Hij staat in onze eigen
# `AANPAK_ALINEA_1` en in 115 velden van het bestaande goud. "dat dat", "die die" en "het het"
# staan erbij omdat ze in een bijzin net zo correct zijn ("of het het waard is").
#
# Verder is de lijst bewust kort: elke andere verdubbeling is een fout. Op het goud vindt deze
# check precies één echte ("logging toepassen in in automatiseringsscripts").
_DUBBEL_WOORD_UITZONDERING = frozenset(("je", "dat", "die", "het"))
_DUBBEL_WOORD_RE = re.compile(r"\b([A-Za-zÀ-ÿ]+)\s+\1\b", re.I)


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def char_count(text: str) -> int:
    return len(text or "")


def sentence_count(text: str) -> int:
    t = (text or "").strip()
    if not t:
        return 0
    n = len(_SENTENCE_END_RE.findall(t))
    return n if n > 0 else 1  # tekst zonder eindteken telt als 1 zin


def zinnen(text: str) -> list[str]:
    """Tekst -> losse zinnen. Ruw: splitst op eindteken, kent geen afkortingen.

    "bijv." of "3.11" splitst dus ten onrechte, maar dat maakt een zin alleen kórter dan hij
    is. Voor waar we dit voor gebruiken -- een uitschieter naar boven signaleren -- valt de
    fout dus de goede kant op.
    """
    return [z.strip() for z in _SENTENCE_END_RE.split((text or "").strip()) if z.strip()]


def _norm(text) -> str:
    return (text or "").strip() if isinstance(text, str) else ""


def _startswith_ci(text: str, prefix: str) -> bool:
    return _norm(text).lower().startswith(prefix.lower())


# ---------------------------------------------------------------------------
# Generieke checks over alle tekstvelden (HTML, placeholders, u-vorm, LLM-taal)
# ---------------------------------------------------------------------------

def _all_text_fields(rw: dict) -> list[tuple[str, str]]:
    """(sectie, tekst) voor elk tekstueel veld, incl. modules- en doelen-onderdelen.

    Dit zijn uitsluitend de velden die de *schrijver* levert. Vaste sjabloonteksten komen
    hier bewust niet in voor, en dat is geen omissie: een paar ervan overtreden onze eigen
    regels. `sjabloon.AANPAK_ALINEA_2` bevat "niet alleen ... maar ook" (een BANNED_PATTERN
    hieronder) plus "essentiele" en "waardevolle"; `VERVOLG_ALINEA_1` eindigt op een
    uitroepteken. Die teksten zijn letterlijk aangeleverd door de schrijfstijl-eigenaar en
    het template is daarin leidend.

    De asymmetrie is dus het ontwerp: de patronen mogen hard op eigen proza vuren juist
    omdat vaste tekst nooit langs deze functie komt. Breid dit niet uit naar het
    samengestelde document -- dan flagt elke training voor altijd zijn eigen boilerplate.
    """
    out: list[tuple[str, str]] = []
    for key in ("overzicht", "inleiding", "aanpak_invulling",
                "doelgroep", "voorkennis", "kortste_omschrijving"):
        out.append((key, _norm(rw.get(key))))
    for mod in _modules(rw):
        out.append(("modules", _norm(mod.get("titel"))))
        for b in mod.get("bullets", []) or []:
            out.append(("modules", _norm(b)))
    for b in _doelen(rw):
        out.append(("doelen", _norm(b)))
    return [(s, t) for s, t in out if t]


def check_generic(rw: dict) -> list[Issue]:
    issues: list[Issue] = []
    for section, text in _all_text_fields(rw):
        if _HTML_RE.search(text):
            issues.append(Issue(section, HARD, "html", "bevat HTML-tags; lever platte tekst."))
        if _PLACEHOLDER_RE.search(text):
            issues.append(Issue(section, HARD, "placeholder",
                                "onvervulde placeholder ([....] / {{ oplnaam }}) blijven staan."))
        dubbel = _DUBBEL_WOORD_RE.search(text)
        if dubbel and dubbel.group(1).lower() not in _DUBBEL_WOORD_UITZONDERING:
            issues.append(Issue(section, HARD, "dubbel_woord",
                                f"'{dubbel.group(0)}' -- hetzelfde woord staat er twee keer."))
        if _U_VORM_RE.search(text):
            issues.append(Issue(section, FLAG, "u_vorm",
                                "gebruikt mogelijk de 'u'-vorm; schrijf in 'je'-vorm."))
        for rx in _BANNED_RE:
            m = rx.search(text)
            if m:
                issues.append(Issue(section, FLAG, "llm_taal",
                                    f"LLM-frase gevonden: '{m.group(0)}' (zie humanisering_nl.md)."))
        low = text.lower()
        for w in MARKETING_WORDS:
            if w in low:
                issues.append(Issue(section, FLAG, "marketing", f"marketingtaal: '{w}'."))
    return issues


# De schrijfspec noemt ±20 woorden per zin. Dat is een gemiddelde, geen plafond: op het goud
# is de mediane zin 19 woorden, maar 41% zit erboven en p90 ligt op 27. Een harde grens zou
# dus de bijzin wegsnijden die de gedachte compleet maakt -- en juist de causale constructie
# uit schrijfspec §0.12 ("doordat we X doen, kun jij Y") maakt zinnen langer.
#
# Daarom staat hier geen grens maar een signaal, en pas ver voorbij de richtlijn. Boven de 35
# woorden zit 1% van het goud; daar gaat het bijna altijd om twee gedachten in één zin. FLAG,
# nooit HARD: dit gaat naar de menselijke review en nooit terug naar de schrijver.
ZIN_RICHTLIJN = 20
ZIN_SIGNAAL = 35

# Kopjes met lopende tekst. Bullets en de [....]-invulling zijn geen zinnen en tellen niet mee.
_PROZA_VELDEN = ("overzicht", "inleiding", "doelgroep", "voorkennis")

# De duur van een training hoort niet in de lopende tekst: het aantal dagen staat als apart veld
# bij de training en wordt met enige regelmaat bijgesteld. Een vermelding middenin een alinea
# gaat dan mee de mist in en wordt bij zo'n aanpassing makkelijk over het hoofd gezien.
#
# Alleen op de prozavelden plus de Kortste omschrijving -- in een module-bullet kan "tweedaagse
# implementatie" over de stof gaan in plaats van over onze training.
_DUUR_VELDEN = _PROZA_VELDEN + ("kortste_omschrijving",)
_DUUR_RE = re.compile(
    r"\b(?:één|een|twee|drie|vier|vijf|zes|zeven|acht|negen|tien|\d+)[\s-]*"
    r"(?:daagse|dagdelen|dagdeel|dagen|dag)\b", re.I)


def check_duurvermelding(rw: dict, ctx: dict | None = None) -> list[Issue]:
    """Geen "In deze training van twee dagen ..." in de tekst. Zie `_DUUR_RE`."""
    issues: list[Issue] = []
    for key in _DUUR_VELDEN:
        m = _DUUR_RE.search(_norm(rw.get(key)))
        if m:
            issues.append(Issue(key, HARD, "duur_in_tekst",
                                f"noemt de duur van de training ('{m.group(0)}'). Het aantal "
                                f"dagen staat als apart veld bij de training en verandert soms; "
                                f"laat het uit de lopende tekst."))
    return issues


def check_anglicismen(rw: dict, ctx: dict | None = None) -> list[Issue]:
    """Zuiver Nederlands: geen letterlijk vertaalde constructies, geen onnodige leenwoorden.

    Expliciet gevraagd in reviewronde 2 ("er moet in de reviewrondes een anglicismecheck
    komen"). De judge doet het inhoudelijke deel -- die ziet een anglicisme dat hier niet op
    staat; deze lijst vangt de vormen die vaak genoeg terugkomen om ze op te schrijven.

    Hooguit één issue per veld: een tekst met drie leenwoorden heeft één probleem, niet drie.
    """
    issues: list[Issue] = []
    for section, text in _all_text_fields(rw):
        for rx, nederlands in _ANGLICISME_RE:
            m = rx.search(text)
            if m:
                issues.append(Issue(section, FLAG, "anglicisme",
                                    f"'{m.group(0)}' -- schrijf Nederlands: {nederlands} "
                                    f"(zie humanisering_nl.md Sectie G)."))
                break
    return issues


def check_em_dash(rw: dict, ctx: dict | None = None) -> list[Issue]:
    """Geen liggende streepjes in gegenereerde tekst. HARD.

    De em-dash is de duidelijkste LLM-tic die er is en stond al in `humanisering_nl.md` §C,
    maar alleen als instructie -- en in reviewronde 4 stonden er alsnog twee in één training.
    Een instructie die het model op twee plaatsen negeert is een check waard.

    Hard mag hier, om twee redenen. De reparatie is triviaal (punt, komma of haakjes) en
    kost de schrijver geen inhoud, en deze check komt nooit langs vaste sjabloontekst: hij
    draait op `_all_text_fields`, dat uitsluitend schrijverstekst oplevert (zie de docstring
    daar). De groep-intro's van Vervolgstappen komen niet van de schrijver en gaan daarom
    niet hierlangs; die worden in `kies_vervolgtrainingen` deterministisch opgeschoond.
    """
    issues: list[Issue] = []
    velden = _all_text_fields(rw) + [("nieuwe_titel", _norm(rw.get("nieuwe_titel")))]
    for section, text in velden:
        if text and _EM_DASH_RE.search(text):
            issues.append(Issue(section, HARD, "em_dash",
                                "bevat een liggend streepje (em-dash of en-dash). Gebruik een "
                                "punt, een komma of haakjes; een gedachtestreepje is hier "
                                "nooit de juiste keuze (humanisering_nl.md Sectie D)."))
    return issues


def check_reikwijdte(rw: dict, ctx: dict | None = None) -> list[Issue]:
    """Signaleert een opsomming die van breedte naar afbakening is gekanteld. FLAG.

    Uit reviewronde 4. De bron schreef "Of je nu in communicatie, beleid, HR, klantcontact of
    projectmanagement werkt: na deze training …" -- een opsomming die laat zien hoe bréed de
    training inzetbaar is. Het concept maakte er "Werk je in communicatie, beleid, HR,
    klantcontact of projectmanagement, dan …" van, en dat zegt iets wezenlijk anders: het
    sluit iedereen daarbuiten uit.

    Code kan dat niet beslissen -- daarvoor moet je de bron ernaast leggen -- dus dit is een
    leesbril voor de reviewer en nooit een hard fail. De voorwaardelijke constructie zelf is
    prima en in Sectie 9 zelfs voorgeschreven; alleen de combinatie met een opsomming van
    drie of meer velden, rollen of sectoren is verdacht.
    """
    issues: list[Issue] = []
    for key in _PROZA_VELDEN:
        for m in _REIKWIJDTE_RE.finditer(_norm(rw.get(key))):
            voorwaarde = m.group("voorwaarde")
            if voorwaarde.count(",") >= 2 or (voorwaarde.count(",") >= 1 and " of " in voorwaarde):
                issues.append(Issue(key, FLAG, "reikwijdte",
                                    f"'{m.group(0)[:70]}…' -- een voorwaarde met een opsomming "
                                    f"erin. Toont de bron hier juist de bréedte van de "
                                    f"training, houd de opsomming dan insluitend ('Of je nu "
                                    f"in X, Y of Z werkt, …')."))
                break
    return issues


def check_eigen_case(rw: dict, ctx: dict | None = None) -> list[Issue]:
    """De deelnemer brengt zelf een case of opdracht mee. HARD.

    Een open inschrijving kan dat niet waarmaken: werken aan materiaal dat de deelnemer zelf
    aanlevert is een bedrijfstraining, en die staat als apart blok onder de Inleiding
    (`sjabloon.BEDRIJFSTRAINING_TEKST`) precies omdat het daar wél kan. Belooft de standaard
    beschrijving het ook, dan verkopen we iets anders dan we leveren.

    Wat hier níét onder valt en gewoon mag: dat de training aansluit op je werksituatie, dat er
    ruimte is voor je vragen en eigen situaties, en dat je aan *een* praktijkcase werkt. Alleen
    het bezit verschuift de belofte, en daarom kijkt deze check op het bezittelijke woord en niet
    op het onderwerp.

    Zie de meetcijfers boven `_INBRENG_RE`: 1 respectievelijk 6 van de 78 goud-trainingen.
    """
    issues: list[Issue] = []
    for section, text in _all_text_fields(rw):
        m = _INBRENG_RE.search(text)
        if m:
            issues.append(Issue(section, HARD, "eigen_case_inbrengen",
                                f"'{m.group(0)[:70]}' belooft dat de deelnemer zelf materiaal "
                                f"meebrengt; dat kan alleen bij een bedrijfstraining. Schrijf dat "
                                f"je aan een praktijkcase werkt, of dat de training aansluit op "
                                f"je werksituatie."))
            continue
        m = _EIGEN_CASE_RE.search(text)
        if m:
            issues.append(Issue(section, HARD, "eigen_case",
                                f"'{m.group(0)[:70]}' suggereert een case van de deelnemer zelf; "
                                f"in een open inschrijving leveren wij de case. Laat het "
                                f"bezittelijke woord weg: 'een praktijkcase'."))
            continue
        m = _CASE_HERKOMST_RE.search(text)
        if m:
            issues.append(Issue(section, HARD, "eigen_case_herkomst",
                                f"'{m.group(0)[:70]}' laat de case uit de praktijk van de "
                                f"deelnemer komen; in een open inschrijving leveren wij hem. "
                                f"Schrijf 'herkenbare casussen uit de praktijk'."))
    return issues


def check_zwakke_formulering(rw: dict, ctx: dict | None = None) -> list[Issue]:
    """De belofte klopt, maar hij staat "aan de onderkant" (schrijfspec Sectie 0.19).

    Dit gaat níét over het niveau -- dat blijft precies zo hoog als de kern zegt. Het gaat over
    het werkwoord waarmee je dat niveau opschrijft. "Begrippen kunnen plaatsen" en "de opbouw
    van het model doorgronden" beloven hetzelfde; alleen het tweede leest als een training.
    """
    issues: list[Issue] = []
    for section, text in _all_text_fields(rw):
        for rx, uitleg in _ZWAK_RE:
            m = rx.search(text)
            if m:
                issues.append(Issue(section, FLAG, "zwakke_formulering",
                                    f"'{m.group(0)}': {uitleg}."))
                break
    return issues


# De contactzin krijgt altijd "dan": "Mocht je hier vragen over hebben, neem dan gerust contact
# met ons op." Zonder dat woord hangt de hoofdzin los van de voorwaarde ervoor. Onze eigen
# `VERVOLG_ALINEA_1` doet het al goed; de Voorkennis-tekst van de schrijver niet.
#
# Op het goud staat de vorm zonder "dan" in 36 van de 78 trainingen -- dat is geschreven tekst
# uit de vorige generatie en geen boilerplate, dus die verandert alleen bij een herschrijving.
_CONTACTZIN_RE = re.compile(r"\bneem\s+(?!dan\b)\w*\s*gerust\s+contact\b", re.I)


def check_contactzin(rw: dict, ctx: dict | None = None) -> list[Issue]:
    issues: list[Issue] = []
    for section, text in _all_text_fields(rw):
        m = _CONTACTZIN_RE.search(text)
        if m:
            issues.append(Issue(section, FLAG, "contactzin_zonder_dan",
                                f"'{m.group(0)}…' -- schrijf 'neem dan gerust contact met ons "
                                f"op'; het 'dan' hoort bij de voorwaarde ervoor."))
    return issues


def check_zinlengte(rw: dict, ctx: dict | None = None) -> list[Issue]:
    issues: list[Issue] = []
    for key in _PROZA_VELDEN:
        for zin in zinnen(_norm(rw.get(key))):
            n = word_count(zin)
            if n > ZIN_SIGNAAL:
                fragment = zin if len(zin) <= 60 else zin[:57].rstrip() + "…"
                issues.append(Issue(key, FLAG, "zin_lang",
                                    f"zin van {n} woorden (richtlijn ±{ZIN_RICHTLIJN}): "
                                    f"\"{fragment}\". Meestal zitten hier twee gedachten in "
                                    f"één zin. Splitsen mag, maar alleen als de zin daar "
                                    f"beter van wordt -- niet om het aantal te halen."))
    return issues


# ---------------------------------------------------------------------------
# Normalisatie van samengestelde velden
# ---------------------------------------------------------------------------

def _modules(rw: dict) -> list[dict]:
    prog = rw.get("modules")
    if isinstance(prog, dict):
        mods = prog.get("modules")
    elif isinstance(prog, list):
        mods = prog
    else:
        mods = None
    return [m for m in (mods or []) if isinstance(m, dict)]


def _doelen(rw: dict) -> list[str]:
    d = rw.get("doelen")
    if isinstance(d, dict):
        d = d.get("bullets")
    if isinstance(d, list):
        return [x for x in d if isinstance(x, str) and x.strip()]
    return []


# Vervolgstappen: een groep-intro die minder dan zoveel trainingen aankondigt, hoort er niet
# te staan. Zie `_check_groepen`. Dit is een kopie van `sjabloon.MIN_TITELS_PER_GROEP`; deze
# module importeert bewust niets uit het project, zodat de checks los te draaien zijn.
MIN_TITELS_PER_GROEP = 2


def _titels(rw: dict) -> list[str]:
    t = rw.get("vervolgstappen_titels")
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str) and x.strip()]
    return []


# ---------------------------------------------------------------------------
# Lengtebanden: richtlijn met vangrail
# ---------------------------------------------------------------------------
#
# Elk lengte-kopje heeft twee banden. De DOELBAND is de lengte uit de schrijfspec: haalt de
# tekst die, dan is hij zo lang als bedoeld. Erbuiten -> FLAG: zichtbaar bij review, maar de
# schrijver gaat er niet voor terug. De VANGRAIL is de buitengrens; pas daarbuiten is de
# tekst echt uit de hand gelopen en moet hij opnieuw -> HARD.
#
# Waarom niet één harde band: op het goudcorpus haalt 35% van de 78 trainingen de doelband
# van het Overzicht en 23% die van de Inleiding (meet dit met `lengtes_over_goud()` in
# rewrite_trainings.py; mediaan 63 resp. 220 woorden). De vorm die we imiteren past dus zelf
# niet in een strak venster. Een harde grens dwingt de schrijver dan tot inkorten op het
# laatste woord, en dat kost de zin precies zijn ritme en precisie -- de reden dat deze
# band bestaat.
#
# De vangrails liggen rond p85 van het goud: ruim genoeg om een goedgeschreven kopje niet
# terug te sturen, strak genoeg om een tekst die ontspoort wél te vangen. Ze zijn
# asymmetrisch. Te lang is een stijlkwestie die een mens bij review ziet; te kort betekent
# dat er inhoud ontbreekt, en dat repareert alleen de schrijver.
#
# Uitzondering: de Kortste omschrijving heeft géén doelband maar een echte harde grens van
# 200 tekens -- die komt van Edudex en niet van ons.


@dataclass(frozen=True)
class Band:
    doel_lo: int      # richtlijn uit de schrijfspec
    doel_hi: int
    rail_lo: int      # buitengrens; daarbuiten terug naar de schrijver
    rail_hi: int


# Het Overzicht stond op 55-65 en dat was smaller dan de eigen catalogus: van de 78 trainingen
# in de nieuwe stijl haalden er 29 die band, terwijl de mediaan op 64 woorden ligt, p75 op 77 en
# p90 op 94. De reviewer verwoordde in ronde 2 hetzelfde van de andere kant: "lengtebeperking is
# geen doel op zich -- liever wat langer, maar een complete intro in de materie, dan korter door
# de bocht". Een van de drie Overzichten miste daardoor een heel onderwerp uit de training.
#
# 55-80 volgt het corpus tot p75 (dekking van 37% naar 65%); de vangrail schuift mee naar 110.
# De ondergrens blijft staan: te kort betekent nog steeds dat er inhoud ontbreekt.
BANDEN: dict[str, Band] = {
    "overzicht": Band(55, 80, 45, 110),
    "inleiding": Band(180, 210, 150, 260),
}

# Meer dagen = meer inhoud om te beschrijven, dus schuift de doelband van de Inleiding mee.
# Het Overzicht niet: dat is de aanhaakalinea, geen inhoudsopgave -- die blijft even lang,
# of de training nu één dag duurt of vijf.
_INLEIDING_PER_DAGEN: tuple[tuple[int, Band], ...] = (
    (1, Band(170, 200, 150, 260)),      # 1 dag of korter
    (3, Band(180, 210, 150, 260)),      # 2-3 dagen: zoals voorgeschreven
    (99, Band(190, 230, 150, 280)),     # 4 dagen of meer
)


def lengteband(kopje: str, dagen: int | None = None) -> Band:
    """De band voor dit kopje, eventueel bijgesteld op het aantal trainingsdagen."""
    if kopje == "inleiding" and dagen:
        for grens, band in _INLEIDING_PER_DAGEN:
            if dagen <= grens:
                return band
    return BANDEN[kopje]


def _lengte_issues(section: str, aantal: int, band: Band) -> list[Issue]:
    """Woordaantal -> hooguit één issue: HARD buiten de vangrail, FLAG buiten de doelband."""
    richtlijn = f"richtlijn is {band.doel_lo}-{band.doel_hi} woorden"
    if aantal < band.rail_lo:
        return [Issue(section, HARD, "lengte_woorden",
                      f"{aantal} woorden is te kort ({richtlijn}); er ontbreekt inhoud. "
                      f"Vul aan met een gedachte die er nog niet staat, niet met vulwoorden.")]
    if aantal > band.rail_hi:
        return [Issue(section, HARD, "lengte_woorden",
                      f"{aantal} woorden is te lang ({richtlijn}). Schrap een hele gedachte "
                      f"of zin; knip geen zinnen af en gooi geen bijzinnen weg die de "
                      f"formulering precies maken.")]
    if not (band.doel_lo <= aantal <= band.doel_hi):
        return [Issue(section, FLAG, "lengte_richtlijn",
                      f"{aantal} woorden; {richtlijn}. Binnen de marge -- alleen bijstellen "
                      f"als de tekst er beter van wordt.")]
    return []


# ---------------------------------------------------------------------------
# Per-kopje checks
# ---------------------------------------------------------------------------

# Alle per-kopje checks hebben dezelfde signatuur `(rw, ctx=None)`, ook waar `ctx` niet
# gebruikt wordt. Zo kan elke aanroeper (check_rewrite, CHECKS_PER_KOPJE in
# rewrite_trainings.py) de context blind doorgeven zonder per kopje uit te zoeken of hij
# hem nodig heeft -- en werkt de Inleiding-band op dagen ook bij een losse hergeneratie.

# voorkennis staat hier bewust NIET in: een lege voorkennis is geldig -> de code
# voegt dan de vaste fallbackzin in (zie assemble_document / sjabloon.VOORKENNIS_FALLBACK).
REQUIRED_SECTIONS = ["overzicht", "inleiding", "modules",
                     "doelgroep", "doelen", "kortste_omschrijving"]


def check_presence(rw: dict) -> list[Issue]:
    issues = []
    for key in REQUIRED_SECTIONS:
        val = rw.get(key)
        empty = val is None or (isinstance(val, str) and not val.strip()) \
            or (isinstance(val, (list, dict)) and not val)
        if empty:
            issues.append(Issue(key, HARD, "ontbreekt", "kopje ontbreekt of is leeg."))
    return issues


# De zin die de openingsvraag beantwoordt noemt de training. Dat is geen nieuwe regel maar een
# bestaand catalogus-patroon dat we kwijt waren: 73 van de 78 goud-trainingen openen hun tweede
# zin met "In deze training" of "Tijdens deze training". Geen van de few-shots deed dat nog
# ("Je leert …", "Je werkt met …"), en de output nam dat een-op-een over -- waardoor de tweede
# zin los kwam te staan van de vraag erboven.
#
# "masterclass" en "workshop" mogen erin, want schrijfspec §0.0 laat die titels staan, en er
# mag een bijvoeglijk naamwoord tussen ("In deze interactieve training", "In deze driedaagse
# training") -- het gaat erom dat de zin de training noemt, niet om de exacte woordvolgorde.
# FLAG en geen HARD: de regel is "nagenoeg elke tweede zin", niet "elke".
_TWEEDE_ZIN_RE = re.compile(
    r"^(?:in|tijdens)\s+(?:deze|de)\s+(?:\w+\s+){0,2}?(?:training|masterclass|workshop)\b",
    re.I)


def check_overzicht(rw: dict, ctx: dict | None = None) -> list[Issue]:
    t = _norm(rw.get("overzicht"))
    if not t:
        return []
    issues = _lengte_issues("overzicht", word_count(t), lengteband("overzicht"))
    if not _startswith_ci(t, "wil je"):
        issues.append(Issue("overzicht", HARD, "opening",
                            'moet beginnen met een vraag die start met "Wil je …".'))
    if _BULLET_PREFIX_RE.search(t):
        issues.append(Issue("overzicht", HARD, "opsomming",
                            "mag geen opsomming/bullets bevatten."))
    zin2 = (zinnen(t)[1:2] or [""])[0]
    if zin2 and not _TWEEDE_ZIN_RE.match(zin2):
        issues.append(Issue(
            "overzicht", FLAG, "tweede_zin",
            f'de zin die de openingsvraag beantwoordt begint met "{zin2.split(",")[0][:40]}…"; '
            f'begin met "In deze training leer je …" (of "Tijdens deze training …" waar '
            f'"leer je" niet past). Een kale "Je leert …" laat de zin los staan van de vraag.'))
    return issues


def check_inleiding(rw: dict, ctx: dict | None = None) -> list[Issue]:
    t = _norm(rw.get("inleiding"))
    if not t:
        return []
    band = lengteband("inleiding", (ctx or {}).get("dagen"))
    return _lengte_issues("inleiding", word_count(t), band)


# Meer dagen = meer programma, dus schuift het aantal modules mee -- net als de lengteband van
# de Inleiding hierboven. De medianen in het oude goud lopen op met de duur: 1 dag -> 5,
# 2-3 dagen -> 6, 4 dagen en meer -> 7; de banden hieronder dekken 71% van dat corpus.
#
# Dat percentage lag eerder op 85%, met 4-7 voor 2-3 dagen en 5-9 vanaf 4 dagen. Dat is
# teruggedraaid, en dat is een REDACTIEBESLUIT en geen corpusmeting: het programma werd in de
# praktijk te lang. Wie dit later ziet, moet niet denken dat de band per ongeluk onder de
# catalogus is gezakt -- de bovenkant van de catalogus is bewust niet meer de bovenkant van de
# band. De ondergrens is ongewijzigd.
#
# Blijft een vangrail, geen doel. De tool-description noemt daarnaast één typisch aantal in
# plaats van een bereik: een model dat een bereik krijgt kiest stelselmatig de bovenkant. Dat
# is over de hele batch te zien -- Overzicht 76/76/76/78 woorden bij een band tot 80, modules
# 6/6/6/7 bij een band tot 7, sub-bullets 4-5 bij een band tot 6.
_MODULES_PER_DAGEN: tuple[tuple[int, tuple[int, int]], ...] = (
    (1, (4, 6)),       # 1 dag of korter
    (3, (4, 6)),       # 2-3 dagen
    (99, (5, 8)),      # 4 dagen of meer
)
_MODULES_BAND_DEFAULT = (4, 6)


def modulesband(dagen: int | None = None) -> tuple[int, int]:
    """(min, max) aantal modules voor dit aantal dagen. Zonder dagen: de brede middenband."""
    if not dagen:
        return _MODULES_BAND_DEFAULT
    for grens, band in _MODULES_PER_DAGEN:
        if dagen <= grens:
            return band
    return _MODULES_BAND_DEFAULT


def check_modules(rw: dict, ctx: dict | None = None) -> list[Issue]:
    mods = _modules(rw)
    issues = []
    lo, hi = modulesband((ctx or {}).get("dagen"))
    if not (lo <= len(mods) <= hi):
        issues.append(Issue("modules", HARD, "modules_aantal",
                            f"{len(mods)} modules; moet {lo}-{hi} zijn."))
    bullet_counts = []
    for idx, m in enumerate(mods, start=1):
        bullets = [b for b in (m.get("bullets") or []) if isinstance(b, str) and b.strip()]
        n = len(bullets)
        bullet_counts.append(n)
        if not (3 <= n <= 6):
            issues.append(Issue("modules", HARD, "bullets_aantal",
                                f"module {idx} heeft {n} sub-bullets; moet 3-6 zijn."))
    if len(bullet_counts) >= 2 and len(set(bullet_counts)) == 1:
        issues.append(Issue("modules", HARD, "bullets_variatie",
                            "aantal sub-bullets moet variëren tussen modules; nu overal gelijk."))
    return issues


def check_doelgroep(rw: dict, ctx: dict | None = None) -> list[Issue]:
    t = _norm(rw.get("doelgroep"))
    if not t:
        return []
    issues = []
    # "bedoeld voor" i.p.v. "is voor": dat zegt dat wij de training op deze lezer hebben
    # gericht. Het verschil tussen een constatering en een uitnodiging.
    if not _startswith_ci(t, "deze training is bedoeld voor"):
        issues.append(Issue("doelgroep", HARD, "opening",
                            'moet beginnen met "Deze training is bedoeld voor …".'))
    # "professionals" stond hier als losse doelgroep-regel; die geldt inmiddels voor élk
    # kopje en zit in check_verboden_woorden, inclusief de uitzondering op de trainingstitel.
    if sentence_count(t) > 1:
        issues.append(Issue("doelgroep", FLAG, "een_zin", "moet één compacte zin zijn."))
    return issues


# Voorkennis is zo kort als de inhoud toelaat, maar niet per se één zin: het aanbevolen
# antwoord uit de schrijfspec ("Enige ervaring met [....] is vereist. Mocht je hier vragen over
# hebben, neem gerust contact met ons op.") bestaat zelf uit twee zinnen. De eerdere
# zin-telling flagde dus precies het modelantwoord. Wat overblijft is een signaal op écht
# uitlopen -- boven dit aantal woorden staat er meer dan een voorwaarde plus een contactzin.
VOORKENNIS_SIGNAAL = 45


def check_voorkennis(rw: dict, ctx: dict | None = None) -> list[Issue]:
    t = _norm(rw.get("voorkennis"))
    if not t:
        return []
    n = word_count(t)
    if n > VOORKENNIS_SIGNAAL:
        return [Issue("voorkennis", FLAG, "voorkennis_lang",
                      f"{n} woorden; houd het compact (richtlijn tot "
                      f"{VOORKENNIS_SIGNAAL} woorden: de voorwaarde en eventueel een "
                      f"contactzin).")]
    return []


def check_aanpak_invulling(rw: dict, ctx: dict | None = None) -> list[Issue]:
    """De [....]-invulling past achter "... en ervaar je hoe ".

    Alleen een signaal, geen hard fail: `sjabloon.schoon_invulling` haalt het leidende
    voegwoord er al af, dus de tekst die het CMS in gaat klopt sowieso. De flag bestaat om te
    zien of de schrijver de instructie oppikt -- blijft hij vuren, dan werkt de tool-description
    niet en niet de code.
    """
    t = _norm(rw.get("aanpak_invulling"))
    woorden = t.split()
    if len(woorden) > 1 and woorden[0].lower() in ("hoe", "dat", "wat"):
        return [Issue("aanpak", FLAG, "invulling_voegwoord",
                      f"invulling begint met '{woorden[0]}'; de vaste zin eindigt al op "
                      f"'ervaar je hoe'. De code heeft het weggehaald.")]
    return []


def check_doelen(rw: dict, ctx: dict | None = None) -> list[Issue]:
    bullets = _doelen(rw)
    issues = []
    if not (4 <= len(bullets) <= 5):
        issues.append(Issue("doelen", HARD, "aantal", f"{len(bullets)} doelen; moet 4-5 zijn."))
    for idx, b in enumerate(bullets, start=1):
        first = b.strip().split()[0] if b.strip().split() else ""
        if first and not first[0].isupper():
            issues.append(Issue("doelen", HARD, "hoofdletter",
                                f"doel {idx} begint niet met een hoofdletter."))
        if not _TE_INFINITIEF_RE.search(b):
            issues.append(Issue("doelen", HARD, "geen_te_infinitief",
                                f"doel {idx} staat niet in de infinitief met 'te'; het moet "
                                f"aansluiten op \"Na deze training ben je in staat om:\" "
                                f"(bv. 'Dashboards te bouwen die de juiste vraag beantwoorden')."))
        if re.search(r"\binzicht toepassen\b", b, re.I):
            issues.append(Issue("doelen", FLAG, "vaag", f"doel {idx} is vaag ('inzicht toepassen')."))
        if _IN_STAAT_RE.search(b):
            issues.append(Issue("doelen", FLAG, "dubbel_in_staat",
                                f"doel {idx} herhaalt 'in staat'; dat staat al in de introzin "
                                f"(\"... ben je in staat om in staat te zijn om ...\")."))
    return issues


# De Kortste omschrijving is een vraag plus het antwoord erop, en dat antwoord begint met "Na
# deze training …". Drie keer expliciet gevraagd in reviewronde 2, met "structureel" erbij: de
# lezer ziet dit fragment vaak zonder de rest van de pagina, dus het moment waarop de opbrengst
# er is moet in de zin zelf staan.
#
# FLAG en geen HARD: geen van de 77 bestaande omschrijvingen doet dit (het is een nieuwe regel,
# geen corpuspatroon) en de mediaan zit op 181 van de maximaal 200 tekens. Botst de constructie
# met die harde grens, dan wint de grens -- Edudex kapt af.
_NA_DEZE_TRAINING_RE = re.compile(r"\bna\s+(?:afloop van\s+)?(?:deze|de)\s+training\b", re.I)


def check_kortste_omschrijving(rw: dict, ctx: dict | None = None) -> list[Issue]:
    t = _norm(rw.get("kortste_omschrijving"))
    if not t:
        return []
    issues = []
    n = len(t)
    # Het enige echte plafond in de spec: Edudex kapt langere tekst af. Geen marge dus.
    if n > 200:
        issues.append(Issue("kortste_omschrijving", HARD, "lengte_tekens",
                            f"{n} tekens; mag maximaal 200 zijn (harde grens van Edudex)."))
    if not _startswith_ci(t, "wil je"):
        issues.append(Issue("kortste_omschrijving", HARD, "opening",
                            'moet beginnen met een vraag die start met "Wil je …".'))
    if not _NA_DEZE_TRAINING_RE.search(t):
        issues.append(Issue("kortste_omschrijving", FLAG, "geen_na_deze_training",
                            'de zin na de openingsvraag begint met "Na deze training …" '
                            '(bv. "Na deze training weet je hoe je …"). Past dat niet binnen '
                            'de 200 tekens, dan gaat de grens voor.'))
    return issues


def check_vervolgstappen(rw: dict, ctx: dict | None = None) -> list[Issue]:
    # De groepen staan los van de catalogus: die controle gaat door, ook zonder catalogus.
    issues = _check_groepen(rw)
    titels = _titels(rw)
    catalog = (ctx or {}).get("catalog_titles")
    if catalog is None:
        if titels:
            issues.append(Issue("vervolgstappen", FLAG, "catalogus_ontbreekt",
                                "geen catalogus geladen; titels niet te valideren."))
        return issues
    catalog_norm = {c.strip().lower() for c in catalog}
    for titel in titels:
        if titel.strip().lower() not in catalog_norm:
            issues.append(Issue("vervolgstappen", HARD, "titel_onbekend",
                                f"'{titel}' staat niet in de catalogus; verzin geen titels."))
    return issues


def _check_groepen(rw: dict) -> list[Issue]:
    """Elke groep-intro kondigt minstens twee trainingen aan. FLAG.

    Een introzin die één bullet aankondigt leest als een fout; hij belooft een richting en
    levert één titel. Uit reviewronde 4 (training 2347).

    FLAG en geen HARD, omdat de schrijver deze groepen niet schrijft: ze komen uit de
    retrieval-call in `kies_vervolgtrainingen`, en daar worden ondermaatse groepen ook
    deterministisch opgeruimd. Deze regel is er voor het geval een groep buiten die weg om
    binnenkomt -- bij een hergeneratie van een oud document bijvoorbeeld.
    """
    groepen = rw.get("vervolgstappen_groepen")
    if not isinstance(groepen, list):
        return []
    issues = []
    for groep in groepen:
        if not isinstance(groep, dict):
            continue
        aantal = len([t for t in (groep.get("titels") or []) if str(t or "").strip()])
        if aantal < MIN_TITELS_PER_GROEP:
            intro = str(groep.get("intro") or "").strip()
            issues.append(Issue("vervolgstappen", FLAG, "groep_te_klein",
                                f"de groep '{intro[:60]}…' kondigt {aantal} training(en) aan; "
                                f"een introzin hoort er minstens "
                                f"{MIN_TITELS_PER_GROEP} aan te kondigen."))
    return issues


def check_verboden_woorden(rw: dict, ctx: dict | None = None) -> list[Issue]:
    """De verbodslijst uit humanisering_nl.md Sectie D.

    Draait bewust alléén over de prozavelden, dus zonder `nieuwe_titel`: 18 van de 779
    catalogustitels bevatten "Professional" ("Training PHP Professional", "Training C#
    Professional"). Een harde titelcheck zou die trainingen onherstelbaar laten falen.

    Om dezelfde reden degradeert het verbod naar een flag zodra de trainingsnaam zelf het
    woord bevat: zo'n training moet zichzelf in de lopende tekst kunnen noemen.
    """
    naam = (ctx or {}).get("naam") or ""
    prof_severity = FLAG if re.search(r"professional", naam, re.I) else HARD
    regels = (
        (re.compile(r"\bprofessionals?\b", re.I), prof_severity, "professionals",
         'gebruik het woord "professional(s)" niet; schrijf waar iemand naartoe wil.'),
        (re.compile(r"\bje\s+hou[dt]t?\s+je\s+bezig\s+met\b", re.I), HARD, "bezig_met",
         '"je houdt je bezig met" zegt niets; noem de handeling zelf.'),
        (re.compile(r"\bmeetings?\b", re.I), FLAG, "meeting",
         'vermijd "meeting"; gebruik "overleg", "sessie" of "bijeenkomst".'),
    )
    issues = []
    for section, text in _all_text_fields(rw):
        for rx, severity, code, boodschap in regels:
            if rx.search(text):
                issues.append(Issue(section, severity, code, boodschap))
    return issues


# Het lerende aspect (schrijfspec Sectie 0.15): "kunnen", "leert ... te ...", "in staat".
# Bewust ruim: elke vorm die het verworven vermogen zichtbaar maakt telt mee.
_LEREND_RE = re.compile(
    r"\b(?:kun|kunt|kunnen|kunje|kan|leer|leert|leren|geleerd|in staat|weten hoe"
    r"|inzicht (?:te )?(?:krijgen|ontwikkelen))\b",
    re.I,
)

# "de Training X" midden in een zin. Daar is het soortwoord een gewoon zelfstandig naamwoord
# en geen deel van een titel. Kop 1 valt hier niet onder: dat is geen tekstveld.
_SOORTWOORD_HOOFDLETTER_RE = re.compile(
    r"\b(?:de|het|een|deze|die)\s+(Training|Masterclass|Workshop|Examentraining)\b"
)


def check_lerend_aspect(rw: dict, ctx: dict | None = None) -> list[Issue]:
    """Overzicht en Kortste omschrijving maken het leren expliciet.

    Wij trainen; de deelnemer levert het resultaat. "Wil je je eigen website bouwen?" belooft
    dat wij de site bouwen, "... kunnen bouwen" niet. In de eerste review-ronde ontbrak dit
    in vier van de vier Overzichten -- vandaar een check.

    FLAG en niet HARD: de uitzondering uit Sectie 0.15 bestaat echt (de deelnemer bouwt het
    eindproduct tijdens de training en neemt het mee), maar hij is zeldzaam genoeg om hem
    bij de menselijke review langs te laten komen.
    """
    issues = []
    for key in ("overzicht", "kortste_omschrijving"):
        t = _norm(rw.get(key))
        if t and not _LEREND_RE.search(t):
            issues.append(Issue(key, FLAG, "lerend_aspect",
                                "maakt het lerende aspect niet expliciet: geen 'kunnen', "
                                "'leert ... te ...' of 'in staat'. Wij trainen, de deelnemer "
                                "levert het resultaat (schrijfspec Sectie 0.15)."))
    return issues


def check_soortwoord_hoofdletter(rw: dict, ctx: dict | None = None) -> list[Issue]:
    """"de training PHP Professional", niet "de Training PHP Professional".

    De code-gegenereerde Modules-openingszin doet dit al goed (`lopende_aanduiding`); dit
    vangt de schrijver die het in de Inleiding of het Overzicht alsnog met een hoofdletter
    zet, meestal door de titel letterlijk over te nemen.
    """
    issues = []
    for section, text in _all_text_fields(rw):
        m = _SOORTWOORD_HOOFDLETTER_RE.search(text)
        if m:
            issues.append(Issue(section, FLAG, "soortwoord_hoofdletter",
                                f"'{m.group(0)}': midden in een zin krijgt het soortwoord een "
                                f"kleine letter ('de {m.group(1).lower()} ...')."))
    return issues


def check_soortwoorden(rw: dict) -> list[Issue]:
    """Niks heet nog een opleiding of een cursus -- alles is een training.

    De brontekst zit er vol mee, dus dit is precies het soort woord dat de schrijver
    ongemerkt overneemt. Hard, ook in de titel. "Examentraining" mag wel.
    """
    issues = []
    velden = _all_text_fields(rw) + [("nieuwe_titel", _norm(rw.get("nieuwe_titel")))]
    for section, text in velden:
        m = _SOORTWOORD_RE.search(text or "")
        if m:
            issues.append(Issue(section, HARD, "soortwoord",
                                f"gebruikt '{m.group(0)}'; noem het een training."))
    return issues


# ---------------------------------------------------------------------------
# Actualiseringen: uitgevoerd op het niveau van hun eigen werkwoord?
# ---------------------------------------------------------------------------

# Kopieën van wat `besluiten.py` en de scorer weten; deze module importeert bewust niets uit
# het project. `refresh:` en `BESLISSING NODIG:` zijn de twee prefixen die
# `score_trainings.actie_voor_rewriter` wegschrijft.
_ACTIE_PREFIX_RE = re.compile(r"^\s*(?:refresh|BESLISSING NODIG)\s*:\s*", re.I)

# De onderste trede van de werkwoordladder (schrijfspec Sectie 12). Alleen hierop vuurt deze
# check: bij "behandel"/"voeg toe"/"vervang" is een leeractiviteit juist de bedoeling.
_NOEM_WERKWOORD_RE = re.compile(r"^(?:benoem|noem|vermeld|verwijs naar)\b", re.I)

# Een term uit de actietekst: een acroniem (SOAR, RLS, DORA), een merknaam of CamelCase
# (PostgreSQL, CloudWatch, Tableau), of een reeks daarvan (SQL Server, Row Level Security,
# ISO/IEC 27002:2022).
_ACTIE_TERM_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:[-/:][A-Za-z0-9]+)*"
                            r"(?: [A-Z][A-Za-z0-9]*(?:[-/:][A-Za-z0-9]+)*)*")

# Woorden die met een hoofdletter beginnen omdat ze vooraan de zin staan, niet omdat het
# termen zijn. Zonder deze lijst wordt "Benoem" zelf een term.
_ACTIE_TERM_STOP = {
    "benoem", "noem", "vermeld", "verwijs", "refresh", "beslissing", "nodig", "training",
    "trainingen", "de", "het", "een", "en", "of", "als", "bij", "in", "op", "naar", "voor",
    "van", "met", "dat", "die", "deze", "dit", "expliciet", "concrete", "moderne", "kort",
}

# De bovenste trede: de deelnemer doet er iets mee. Bewust alleen vormen met "je" als
# onderwerp, want "van toepassing" is een idioom en geen leeractiviteit -- dat leverde in de
# meting een vals-positief op ("de wetgeving is volledig van toepassing").
_TOEPASSEN_RE = re.compile(
    r"\b(?:pas(?:t)? je\b[^.]{0,40}?\btoe"
    r"|je (?:past|toepast)\b[^.]{0,40}?\btoe"
    r"|(?:werk|bouw|oefen|configureer|implementeer|schrijf|ontwerp|beheer|test) je\b"
    r"|je (?:werkt|bouwt|oefent|configureert|implementeert|schrijft|ontwerpt|beheert|test)\b"
    r"|richt je\b[^.]{0,30}?\bin\b"
    r"|aan de slag met\b"
    r"|toepassen op\b)", re.I)


def check_actie_escalatie(rw: dict, ctx: dict | None = None) -> list[Issue]:
    """Een "benoem"-actie die een leeractiviteit is geworden. FLAG.

    Uit training 27 (SQL). De actie luidde "refresh: benoem concrete SQL-platformen (bv.
    PostgreSQL, SQL Server, cloud data warehouses) als context bij de training"; de schrijver
    maakte er "De SQL die je leert, pas je direct toe op verschillende platformen, van
    PostgreSQL en SQL Server tot cloud data warehouses" van. De reviewer-voorwaarde was
    gerespecteerd, het werkwoord niet: die platformen komen in de training niet voor. De judge
    liet het door, want zijn spec verbood hem toen om een passage uit een goedgekeurde actie af
    te rekenen als te hoge belofte.

    Geen randgeval: 11 van de 16 trainingen met output hebben minstens één goedgekeurde
    noem-actie. Gemeten over datzelfde corpus vuurt deze check op 1 van de 16, en dat is 27.
    Zonder het frequentiefilter hieronder zijn het er 3, waarvan 2 vals: 2660 ("noem OLS
    expliciet naast RLS" in een training die Power BI Security héét) en 2669, waar de term uit
    de actie de bestaande inhoud aanduidt en niet de toevoeging ("... als aanvulling op de
    klassieke ML-algoritmen").

    Het frequentiefilter heeft een blinde vlek die je moet kennen: een schrijver die een
    genoemde term door de héle training weeft, komt er juist door de herhaling onderuit. Dat is
    bewust. Deze check is een smal net voor de fout die niemand ziet -- één zin die een
    vermelding tot een belofte maakt -- en niet voor een onderwerp dat overal opduikt; dat
    laatste ziet de judge, en een mens ook.

    FLAG en geen HARD, om dezelfde reden als `check_reikwijdte`: code kan niet beslissen of
    "je werkt met X" hier noemen of behandelen is -- daarvoor moet je de actie en de brontekst
    ernaast leggen. Een vals-positief zou anders twee revisierondes kosten.
    """
    acties = (ctx or {}).get("acties") or []
    termen = _noem_termen(acties)
    if not termen:
        return []

    velden = _all_text_fields(rw)
    naam = _norm((ctx or {}).get("naam")).lower()
    # Een term die het onderwerp van de training zélf is, zegt niets: dat de deelnemer met
    # CloudWatch werkt in een CloudWatch-training is geen escalatie. Twee signalen daarvoor:
    # de term staat in de titel, of hij komt in de tekst zo vaak terug dat hij het onderwerp
    # draagt in plaats van als voorbeeld genoemd te worden.
    alle_tekst = " ".join(t for _, t in velden).lower()
    termen = [t for t in termen
              if t.lower() not in naam and alle_tekst.count(t.lower()) <= 2]

    issues: list[Issue] = []
    for section, text in velden:
        for zin in zinnen(text):
            gevonden = [t for t in termen if t.lower() in zin.lower()]
            if not gevonden or not _TOEPASSEN_RE.search(zin):
                continue
            fragment = zin if len(zin) <= 90 else zin[:87].rstrip() + "…"
            issues.append(Issue(section, FLAG, "actie_escalatie",
                                f"\"{fragment}\" -- de actualisering vroeg om {gevonden[0]} te "
                                f"benoemen, deze zin belooft dat de deelnemer ermee werkt. Het "
                                f"werkwoord van de actie is de bovengrens (schrijfspec Sectie "
                                f"12); noem de term als context zonder er een leeractiviteit "
                                f"van te maken."))
            return issues   # één signaal per training is genoeg; de reviewer leest de rest zelf
    return issues


def _noem_termen(acties) -> list[str]:
    """De concrete termen uit de goedgekeurde acties die alleen om noemen vragen."""
    uit: list[str] = []
    for actie in acties:
        kaal = _ACTIE_PREFIX_RE.sub("", _norm(actie))
        if not _NOEM_WERKWOORD_RE.match(kaal):
            continue
        for m in _ACTIE_TERM_RE.finditer(kaal):
            term = m.group(0).strip()
            if len(term) > 2 and term.lower() not in _ACTIE_TERM_STOP and term not in uit:
                uit.append(term)
    return uit


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def check_rewrite(rewrite: dict, ctx: dict | None = None) -> list[Issue]:
    """Draait alle checks en geeft één gecombineerde issue-lijst terug (HARD + FLAG)."""
    rw = rewrite or {}
    issues: list[Issue] = []
    issues += check_presence(rw)
    issues += check_overzicht(rw, ctx)
    issues += check_inleiding(rw, ctx)
    issues += check_modules(rw, ctx)
    issues += check_doelgroep(rw, ctx)
    issues += check_voorkennis(rw, ctx)
    issues += check_aanpak_invulling(rw, ctx)
    issues += check_doelen(rw, ctx)
    issues += check_kortste_omschrijving(rw, ctx)
    issues += check_vervolgstappen(rw, ctx)
    issues += check_soortwoorden(rw)
    issues += check_soortwoord_hoofdletter(rw, ctx)
    issues += check_verboden_woorden(rw, ctx)
    issues += check_lerend_aspect(rw, ctx)
    issues += check_generic(rw)
    issues += check_zinlengte(rw, ctx)
    issues += check_duurvermelding(rw, ctx)
    issues += check_anglicismen(rw, ctx)
    issues += check_em_dash(rw, ctx)
    issues += check_reikwijdte(rw, ctx)
    issues += check_eigen_case(rw, ctx)
    issues += check_zwakke_formulering(rw, ctx)
    issues += check_contactzin(rw, ctx)
    issues += check_actie_escalatie(rw, ctx)
    return issues


if __name__ == "__main__":
    # Mini-demo (zonder API-key). Voer test_rewrite.py uit voor de echte tests.
    demo = {
        # Afwisselende vulwoorden: twee keer hetzelfde woord achter elkaar is een harde fout.
        "overzicht": "Wil je " + " ".join(("woord", "term")[i % 2] for i in range(58)) + "?",
        "inleiding": " ".join(("zin", "regel")[i % 2] for i in range(195)),
        "modules": {"modules": [
            {"titel": "M1", "bullets": ["a", "b", "c"]},
            {"titel": "M2", "bullets": ["a", "b", "c", "d"]},
            {"titel": "M3", "bullets": ["a", "b", "c"]},
            {"titel": "M4", "bullets": ["a", "b", "c", "d", "e"]},
        ]},
        "doelgroep": "Deze training is voor professionals die data beter willen benutten.",
        "voorkennis": "Specifieke voorkennis voor het volgen van deze training is niet noodzakelijk.",
        "doelen": ["Dashboards te bouwen die de juiste vraag beantwoorden",
                   "Datasets op te schonen en samen te voegen voor analyse",
                   "Trends te analyseren en te vertalen naar keuzes",
                   "Resultaten te presenteren aan je team"],
        "vervolgstappen_titels": ["Training Power BI"],
        "kortste_omschrijving": "Wil je slimmer met data werken en betere keuzes maken?",
        "nieuwe_titel": "Training Data",
    }
    ctx = {"catalog_titles": {"Training Power BI"}, "naam": "Training Data"}
    for issue in check_rewrite(demo, ctx):
        print(issue)
    print("hard fails:", len(hard_fails(check_rewrite(demo, ctx))))

    # Dezelfde tekst, maar nu heet de training zelf "Professional": het verbod degradeert
    # naar een flag, zodat de training zichzelf mag noemen.
    ctx_prof = dict(ctx, naam="Training PHP Professional")
    print("hard fails met 'Professional' in de titel:",
          len(hard_fails(check_rewrite(demo, ctx_prof))))
