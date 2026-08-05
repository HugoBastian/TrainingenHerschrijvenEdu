"""
rewrite_checks.py
=================
Deterministische code-check voor een herschreven training (de 9 kopjes).
Geen LLM: plain Python-functies die een lijst `Issue`-objecten teruggeven,
gesplitst in HARD-FAIL (moet terug naar de schrijver) en FLAG (mag mee naar
judge/review). Zelfde geest als `finalize_scores` in score_trainings.py:
de code beslist deterministisch, het model schrijft alleen.

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
      "kortste_omschrijving":   "Wil je ... (<=200 tekens; harde grens)",
      "nieuwe_titel":           "Training ...",                          # nooit cursus/opleiding
    }

Lengtes zijn richtlijnen met een vangrail eromheen: buiten de richtlijn is het een FLAG,
pas buiten de vangrail een HARD-FAIL. Zie "Lengtebanden" verderop. De 200 tekens van de
Kortste omschrijving zijn de uitzondering -- die grens komt van Edudex en is wél hard.

Context `ctx` (optioneel):
    { "catalog_titles": {"Titel A", ...}, "naam": "Trainingsnaam", "dagen": 3 }

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


BANDEN: dict[str, Band] = {
    "overzicht": Band(55, 65, 45, 90),
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
    return issues


def check_inleiding(rw: dict, ctx: dict | None = None) -> list[Issue]:
    t = _norm(rw.get("inleiding"))
    if not t:
        return []
    band = lengteband("inleiding", (ctx or {}).get("dagen"))
    return _lengte_issues("inleiding", word_count(t), band)


# Meer dagen = meer programma, dus schuift het aantal modules mee -- net als de lengteband van
# de Inleiding hierboven. De vorige vaste band van 4-6 stond smaller dan de eigen catalogus:
# van de 71 bestaande nieuwe-stijl trainingen met een genest programma viel 31% erbuiten, vrijwel
# allemaal erboven (7 t/m 10). De medianen lopen op met de duur: 1 dag -> 5, 2-3 dagen -> 6,
# 4 dagen en meer -> 7. Deze banden dekken 85% van dat corpus.
#
# Blijft een vangrail, geen doel: de schrijfspec en de tool-description noemen daarnaast een
# typisch aantal, want een model dat alleen een bereik krijgt kiest stelselmatig de bovenkant.
_MODULES_PER_DAGEN: tuple[tuple[int, tuple[int, int]], ...] = (
    (1, (4, 6)),       # 1 dag of korter
    (3, (4, 7)),       # 2-3 dagen
    (99, (5, 9)),      # 4 dagen of meer
)
_MODULES_BAND_DEFAULT = (4, 7)


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
    return issues


def check_vervolgstappen(rw: dict, ctx: dict | None = None) -> list[Issue]:
    titels = _titels(rw)
    catalog = (ctx or {}).get("catalog_titles")
    if catalog is None:
        if titels:
            return [Issue("vervolgstappen", FLAG, "catalogus_ontbreekt",
                          "geen catalogus geladen; titels niet te valideren.")]
        return []
    catalog_norm = {c.strip().lower() for c in catalog}
    issues = []
    for titel in titels:
        if titel.strip().lower() not in catalog_norm:
            issues.append(Issue("vervolgstappen", HARD, "titel_onbekend",
                                f"'{titel}' staat niet in de catalogus; verzin geen titels."))
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
