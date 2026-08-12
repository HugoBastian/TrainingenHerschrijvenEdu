"""
sjabloon.py
===========
De vaste teksten en de kopstructuur uit `Template trainingen nieuwe opbouw.md`, als code.

Eén plek voor alles wat de code deterministisch invult, zodat de schrijfspec, de schrijver,
de judge en de CMS-output niet uit elkaar kunnen lopen. Verander je hier een zin, dan
verandert hij overal mee.

Kopstructuur van het template:

    # <trainingstitel>                                     -- kop 1
    ## Overzicht / Inleiding / Modules / ...               -- kop 2
    ### Deze training als bedrijfstraining ...?            -- kop 3 (binnen Inleiding)

LET OP -- de vaste teksten hieronder zijn aangeleverd door de schrijfstijl-eigenaar en staan
er letterlijk zoals aangeleverd. Een paar ervan overtreden regels die voor de *schrijver*
wél gelden: `AANPAK_ALINEA_2` bevat "niet alleen ... maar ook" (humanisering_nl.md SectieC),
"essentiele" en "waardevolle" (SectieB), en `VERVOLG_ALINEA_1` eindigt op een uitroepteken.
Dat is geen slordigheid maar een bewuste keuze: het template is leidend voor onze eigen
boilerplate. Daarom worden vaste teksten nergens door `rewrite_checks.py` gescand en beoordeelt
de judge ze niet -- zie `_all_text_fields()` en de beoordelingsspec. Kopieer de constructies
hier dus niet naar gegenereerde tekst.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 1. DE TIEN KOPJES: naam, veld in de writer-output, veld in de CMS-content
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Kopje:
    kop: str          # zoals het in het document boven de sectie staat (kop 2)
    veld: str         # sleutel in het samengestelde document
    cms: str          # sleutel in de content-JSON van het CMS
    html: bool = True     # False = platte tekst in het CMS-veld
    generatief: bool = True   # False = volledig door de code ingevuld


KOPJES: tuple[Kopje, ...] = (
    Kopje("Overzicht",           "overzicht",           "summary",         html=False),
    Kopje("Inleiding",           "inleiding",           "intro"),
    Kopje("Modules",             "modules",             "modules"),
    Kopje("Doelgroep",           "doelgroep",           "target_audience"),
    Kopje("Voorkennis",          "voorkennis",          "prior_knowledge"),
    Kopje("Aanpak",              "aanpak",              "setup"),
    Kopje("Doelen",              "doelen",              "objectives"),
    Kopje("Vervolgstappen",      "vervolgstappen",      "follow_up"),
    Kopje("Kortste omschrijving", "kortste_omschrijving", "summary_edudex", html=False),
    # De kop heette een tijd lang "Deelnamecertificaat"; dat was een fout in het template en
    # is teruggedraaid naar "Certificatie". `cms` blijft `certification`: dat is het
    # CMS-contract en de sleutel in alle bestaande JSON op schijf.
    Kopje("Certificatie",        "certificatie",        "certification", generatief=False),
)

KOP_PER_VELD = {k.veld: k.kop for k in KOPJES}
CMS_PER_VELD = {k.veld: k.cms for k in KOPJES}

# `days` schrijven we niet, maar nemen we ongewijzigd over uit de bron
BEHOUDEN_UIT_BRON = ("days",)


# ---------------------------------------------------------------------------
# 2. VASTE TEKSTEN (code plaatst deze -- de schrijver niet)
# ---------------------------------------------------------------------------

# Kopje 2 -- Inleiding: vast blok onder een kop 3, na de geschreven inleiding.
BEDRIJFSTRAINING_KOP = "Deze training als bedrijfstraining voor jou en je team?"
BEDRIJFSTRAINING_TEKST = (
    "Elk vraagstuk en elke situatie is anders; daarom staan tijdens een bedrijfstraining jouw "
    "organisatie en (bedrijfs)vraagstukken centraal. In goed overleg stellen we een "
    "lesprogramma samen dat volledig aansluit bij jouw (jullie!) specifieke uitdaging, wensen "
    "en dagelijkse werkpraktijk."
)

# Kopje 3 -- Modules: openingszin vóór de modulelijst, in twee varianten.
#
# De NB verschilt per training en die keuze is inhoudelijk, niet stilistisch:
#
#   stabiel  (DEFAULT) -- het programma is wat het is. De NB nodigt uit tot afstemming.
#   actueel            -- het vakgebied beweegt zo snel dat de pagina snel achterloopt.
#                         Alleen dan de voorbehoud-NB; hij doet anders afbreuk aan het geheel.
#
# Volgorde van gezag bij het kiezen: reviewerkolom -> modelvoorstel -> default "stabiel".
#
# De NB staat in een eigen alinea -- drie reviewers schreven er onafhankelijk van elkaar
# "nieuwe alinea" bij. De `\n\n` is de alineagrens die `_paragrafen()` in `rewrite_output.py`
# omzet naar een tweede <p>; precies zoals de bestaande CMS-content zijn alinea's maakt (de
# `intro` van 69 van de 78 trainingen bestaat uit drie of vier <p>-blokken, zonder <br> en
# zonder witregel ertussen).
#
# "over de inhoud", niet "over de actuele inhoud": de actualiteit is precies wat de variant
# `actueel` hieronder afdekt. Staat het in beide, dan roept de stabiele variant een vraag op
# die hij zelf niet beantwoordt -- waarom zou er een andere inhoud zijn dan deze?
MODULES_NB_STABIEL = (
    "Tijdens {aanduiding} komen onderstaande onderwerpen aan bod.\n\n"
    "NB: Mocht je vragen hebben over de inhoud of deze aangepast willen zien op jouw "
    "specifieke praktijksituatie of trainingsbehoefte, bel ons dan gerust: we spreken de "
    "mogelijkheden graag met je door."
)
MODULES_NB_ACTUEEL = (
    "Tijdens {aanduiding} komen onderstaande onderwerpen aan bod.\n\n"
    "NB: Afhankelijk van snelle ontwikkelingen op dit expertisegebied, kan de werkelijke "
    "trainingsinhoud hier van afwijken. Bel ons gerust voor meer informatie over de actuele "
    "inhoud."
)
MODULES_NB_VARIANTEN: dict[str, str] = {
    "stabiel": MODULES_NB_STABIEL,
    "actueel": MODULES_NB_ACTUEEL,
}
MODULES_NB_DEFAULT = "stabiel"

# Soortwoorden die een titel mag houden. Alles wat hier NIET in staat wordt door
# `nieuwe_titel` vervangen door "Training".
TOEGESTANE_SOORTWOORDEN = ("training", "examentraining", "masterclass", "workshop")

# Soortwoorden die een titel verliest: "Cursus XML" -> "Training XML".
VERBODEN_SOORTWOORDEN = ("opleiding", "cursus", "gebruikerscursus", "examencursus", "leergang")


def vervang_soortwoord(naam: str) -> str:
    """Vervangt alléén een leidend verboden soortwoord. Laat de rest met rust.

    "Cursus XML"    -> "Training XML"
    "Power BI"      -> "Power BI"        (geen soortwoord: niet aanraken)

    Dit is de voorzichtige variant, voor tekst die we verder ongewijzigd overnemen --
    bijvoorbeeld de vervolgstappen-lijst van een training die al herschreven was. Daar
    staan regels tussen die helemaal geen titel zijn ("Trainingen voor specifieke
    databasesystemen zoals ..."), en die moeten ongemoeid blijven.
    """
    naam = (naam or "").strip()
    if not naam:
        return ""
    woorden = naam.split()
    if woorden[0].lower() not in VERBODEN_SOORTWOORDEN:
        return naam
    rest = " ".join(woorden[1:]).strip()
    return f"Training {rest}" if rest else "Training"


def nieuwe_titel(naam: str) -> str:
    """Bron-titel -> titel in de nieuwe stijl. Niks heet nog opleiding of cursus.

    "Opleiding PHP Professional" -> "Training PHP Professional"
    "Cursus XML"                 -> "Training XML"
    "Gebruikerscursus Sitecore"  -> "Training Sitecore"
    "Masterclass PHP"            -> "Masterclass PHP"    (toegestaan soortwoord)
    "Excel"                      -> "Training Excel"     (geen soortwoord -> voorvoegsel)

    Voor een trainingstitel, die altijd een soortwoord hoort te hebben. Voor losse regels
    in bestaande tekst is `vervang_soortwoord` de juiste functie.
    """
    naam = (naam or "").strip()
    if not naam:
        return ""
    if naam.split()[0].lower() in TOEGESTANE_SOORTWOORDEN:
        return naam
    vervangen = vervang_soortwoord(naam)
    if vervangen != naam:
        return vervangen
    return f"Training {naam}"


# Woorden waarmee een titel niet begint. Staat er zo'n woord in kleine letters direct
# achter het soortwoord, dan is de regel een lopende zin ("Training op maat") en geen
# titel. Voorzorg: op de echte catalogus en het goud vuurt deze guard nergens --
# "Training Van Excel naar Power BI" is wél een titel, met een hoofdletter.
GEEN_TITELSTART = frozenset((
    "op", "voor", "in", "met", "over", "van", "bij", "en", "of", "om",
    "naar", "als", "die", "dat", "de", "het", "een", "aan", "uit", "per",
))


def vervolgtitel(naam: str) -> str:
    """Titel zoals hij in de Vervolgstappen-lijst staat: zonder "Training" ervoor.

    "Cursus PowerPoint"       -> "PowerPoint"
    "Training Power BI"       -> "Power BI"
    "Power BI"                -> "Power BI"
    "Masterclass PHP"         -> "Masterclass PHP"     (afwijkende vorm blijft staan)
    "Examentraining CEH"      -> "Examentraining CEH"

    De lijst staat al onder het kopje Vervolgstappen; "Training" ervoor is bij elke regel
    ruis. Een afwijkende vorm (masterclass, workshop, examentraining) is wél informatie
    over de training en blijft staan, precies zoals in de gewone titel.

    Ook de poort voor gekopieerde regels: wat geen titelvorm heeft, gaat door
    `vervang_soortwoord` en houdt dus hooguit het verboden woord niet.
    """
    naam = (naam or "").strip()
    woorden = naam.split()
    if len(woorden) < 2 or woorden[0].lower() not in ("training",) + VERBODEN_SOORTWOORDEN:
        return vervang_soortwoord(naam)
    if woorden[1][:1].islower() and woorden[1].lower() in GEEN_TITELSTART:
        return vervang_soortwoord(naam)
    return " ".join(woorden[1:])


def lopende_aanduiding(naam: str) -> str:
    """De training zoals je haar midden in een zin noemt: soortwoord met kleine letter.

    "Training PHP Professional"  -> "de training PHP Professional"
    "Cursus XML"                 -> "de training XML"      (via `nieuwe_titel`)
    "Masterclass PHP"            -> "de masterclass PHP"
    "Power BI"                   -> "de training Power BI"
    ""                           -> "deze training"

    Midden in een lopende zin is "de Training PHP Professional" een hoofdletterfout: het
    soortwoord is daar geen deel van een titel maar een gewoon zelfstandig naamwoord.
    Alleen dát eerste woord gaat naar onderkast; de rest van de titel blijft ongemoeid,
    anders wordt "de training Power BI" ineens "de training power bi".
    """
    titel = nieuwe_titel(naam)
    if not titel:
        return "deze training"
    woorden = titel.split()
    woorden[0] = woorden[0].lower()
    return "de " + " ".join(woorden)


def modules_opening(naam: str, variant: str = MODULES_NB_DEFAULT) -> str:
    """De openingszin van kopje Modules, met een lopende aanduiding van de training.

    Draait op de nieuwe titel, zodat er nooit "de training opleiding PHP Professional"
    ontstaat: die begint na normalisatie met een toegestaan soortwoord.

    `variant` is "stabiel" (default) of "actueel"; zie MODULES_NB_VARIANTEN. Een onbekende
    of lege variant valt terug op de default in plaats van te knallen -- deze zin hoort
    altijd in het document te staan, ook als de kolom in de sheet rommelig is ingevuld.
    """
    sjabloon = MODULES_NB_VARIANTEN.get(
        (variant or "").strip().lower(), MODULES_NB_VARIANTEN[MODULES_NB_DEFAULT]
    )
    return sjabloon.format(aanduiding=lopende_aanduiding(naam))

# Kopje 6 -- Aanpak: twee vaste alinea's; de schrijver levert alleen de [....]-invulling.
AANPAK_ALINEA_1 = (
    "De training is praktisch en interactief van opzet, met veel ruimte voor jouw vragen en "
    "werksituatie. Je gaat aan de slag met passende praktijkvoorbeelden. Door actief te "
    "oefenen, te analyseren en te evalueren, maak je je de materie stap voor stap eigen en "
    "ervaar je hoe {invulling}."
)
# De ontbrekende "te" in "een waardevolle vertaalslag te maken" is de enige correctie die we
# op de aangeleverde tekst hebben gedaan; verder staat hij er letterlijk zoals aangeleverd.
#
# De eerste zin luidde "in hun dagelijks werk expert op hun trainingsonderwerp" en is in
# reviewronde 2 vervangen door de vaste woordenschat uit schrijfspec Sectie 0.20: "dagelijks
# werkzaam op dit expertisegebied". Dat is dezelfde formulering die `correcties_nl.md` Sectie 10
# al voorschreef voor gegenereerde tekst -- de boilerplate liep daar op achter.
#
# NADRUK. Twee delen krijgen nadruk: "kennis" en de deelzin "toepassing binnen jouw organisatie
# en werksituatie". Dat is de kern van de alinea -- de vertaalslag van het een naar het ander --
# en die nadruk hoort in élke uitvoer te staan, markdown zowel als HTML.
#
# Tot augustus 2026 stond dat cursief: `*kennis*` in de markdown, <em>kennis</em> in de
# CMS-content. Onze site en de leerportalen geven die cursivering niet goed weer, dus staan er nu
# enkele aanhalingstekens. Die zitten in de tekst zélf en niet in de opmaak, en overleven daarmee
# elke weergave; de gemarkeerde bronvorm (`AANPAK_ALINEA_2_MARKUP`) en het afleiden van de platte
# vorm zijn daarmee vervallen. Eén constante, die letterlijk zo naar buiten gaat.
AANPAK_ALINEA_2 = (
    "Onze trainers zijn, naast trainer, dagelijks werkzaam op dit expertisegebied. "
    "Ze beschikken dus niet alleen over de meest actuele kennis, maar "
    "hebben ook essentiële praktijkervaring. Hierdoor zijn ze in staat om een waardevolle "
    "vertaalslag te maken van ‘kennis’ naar ‘toepassing binnen jouw organisatie en "
    "werksituatie’."
)


# De documenten die vóór die wissel zijn weggeschreven dragen de sterretjes nog in hun
# `aanpak`-veld (32 op het moment van wisselen). Ze worden opnieuw gerenderd zodra iemand ze naar
# goud promoveert of hun content herbouwt, en zonder deze omzetting toont zo'n herrender
# letterlijke sterretjes in plaats van de cursivering die er ooit uit kwam.
#
# `*...*` zonder newline ertussen: de nadruk liep nooit over een alineagrens.
_OUDE_CURSIEF_RE = re.compile(r"\*([^*\n]+)\*")


def verquote_cursief(tekst: str) -> str:
    """Oude cursiefmarkering `*x*` -> ‘x’. Alleen voor documenten van vóór augustus 2026."""
    return _OUDE_CURSIEF_RE.sub(r"‘\1’", tekst or "")


AANPAK_FALLBACK = "je dit toepast in de praktijk"

# De schrijver ziet in zijn tool alleen "lever de [....]-invulling" en niet de zin eromheen.
# Levert hij dan een invulling die zelf met "hoe" begint, dan staat er "ervaar je hoe hoe ...".
# Dat is precies wat er bij training 2347 gebeurde. De prompt zegt het inmiddels expliciet,
# maar dat is een instructie en geen garantie -- dus knippen we het er hier alsnog af.
#
# Ook "dat" en "wat" staan in de lijst: dezelfde fout, andere voegwoord. Wat er daarna staat
# blijft ongemoeid; alleen dat ene leidende woord gaat eraf.
_DUBBEL_VOEGWOORD = ("hoe", "dat", "wat")


def schoon_invulling(invulling: str) -> str:
    """De [....]-invulling zoals hij achter "... en ervaar je hoe " past.

    "hoe je XML toepast"  -> "je XML toepast"
    "je XML toepast"      -> "je XML toepast"
    "hoe"                 -> "hoe"          (niets over: laat staan, de check vangt het)
    """
    tekst = (invulling or "").strip()
    woorden = tekst.split()
    if len(woorden) > 1 and woorden[0].lower() in _DUBBEL_VOEGWOORD:
        return " ".join(woorden[1:])
    return tekst

# Kopje 5 -- Voorkennis: zin als er geen voorkennis nodig is.
VOORKENNIS_FALLBACK = (
    "Specifieke voorkennis voor het volgen van deze training is niet noodzakelijk."
)

# Kopje 7 -- Doelen: vaste introzin boven de bullets.
#
# "ben je in staat om" i.p.v. het eerdere "heb je handvatten om": stelliger, en daarmee een
# directer causaal verband tussen de training en de opbrengst. De te-infinitief blijft precies
# zo doorlopen ("... in staat om datasets te ontsluiten"), dus de harde doelen-check verandert
# niet mee. Keerzijde is dat deze zin méér belooft; de schrijfspec vangt dat op met de regel
# over vergrotende trappen bij begripsgerichte trainingen (zie schrijfspec Sectie 8).
DOELEN_INTRO = "Na deze training ben je in staat om:"

# Kopje 8 -- Vervolgstappen: drie vaste alinea's rond de catalogustitels.
VERVOLG_ALINEA_1 = (
    "Binnen dit expertisegebied beschikken wij over ruime kennis en praktijkervaring. Zoek je "
    "meer diepgang of een (compleet) andere insteek? Neem dan gerust contact met ons op voor "
    "een vrijblijvende verkenning. We denken graag met je mee!"
)
VERVOLG_ALINEA_2 = (
    "Er zijn verschillende vervolgtrainingen, die aansluiten op specifieke onderwerpen, "
    "toepassingen en werkcontexten."
)
# Aankondiging boven één ongegroepeerde lijst. Levert de retrieval groepen met een
# eigen intro-zin, dan gebruikt de code die in plaats hiervan.
VERVOLG_LIJST_INTRO = "Zo bieden we onder andere:"
# Een groep-intro kondigt een richting aan; met één training eronder leest dat als een fout.
# Reviewronde 4. `rewrite_checks` houdt bewust een eigen kopie van dit getal -- die module
# importeert niets uit dit project.
MIN_TITELS_PER_GROEP = 2
# Vervallen: stond niet in het template en herhaalde grotendeels wat alinea 1 al zegt over
# contact opnemen. Leeg laten, niet verwijderen -- `document_to_content` slaat een lege
# afsluiter over en de constante houdt de keuze zichtbaar.
VERVOLG_AFSLUITER = ""

# Kopje 10 -- Certificatie: volledig vast.
CERTIFICATIE = "Na afronding van deze training ontvang je een certificaat van deelname."


# ---------------------------------------------------------------------------
# 3. ALLE VASTE TEKSTEN OP EEN RIJ
# ---------------------------------------------------------------------------
#
# Eén bron voor code die iets over de vaste teksten als geheel wil weten:
#   - `test_rewrite.py` controleert dat de citaten in de schrijfspec hier nog mee kloppen
#     (de spec citeert ze letterlijk, dus die twee kunnen uit elkaar lopen);
#   - `scan_vorm()` herkent hiermee een training die nog de vorige generatie vaste tekst
#     draagt en dus minstens een format-herschrijving nodig heeft.
#
# De {}-placeholders staan er bewust in: waar een consument letterlijke tekst nodig heeft,
# vergelijkt hij op het deel vóór de placeholder.
VASTE_TEKSTEN: tuple[str, ...] = (
    BEDRIJFSTRAINING_KOP,
    BEDRIJFSTRAINING_TEKST,
    MODULES_NB_STABIEL,
    MODULES_NB_ACTUEEL,
    AANPAK_ALINEA_1,
    AANPAK_ALINEA_2,
    VOORKENNIS_FALLBACK,
    DOELEN_INTRO,
    VERVOLG_ALINEA_1,
    VERVOLG_ALINEA_2,
    VERVOLG_LIJST_INTRO,
    CERTIFICATIE,
)

# Vaste teksten uit de vórige generatie van het template. Een bestaande training die deze
# nog draagt, is per definitie niet "al in de nieuwe stijl", hoe schoon hij verder ook is.
VERVALLEN_VASTE_TEKSTEN: tuple[str, ...] = (
    "Deze training bieden we ook als bedrijfstraining voor jou en je team",
    "De inhoud stemmen we dan af op jullie werksituatie, systemen en concrete vraagstukken",
    "komen in basis onderstaande onderwerpen aan bod",
    "Afhankelijk van ontwikkelingen op het vakgebied",
    "De training is interactief en praktijkgericht opgezet",
    "De training wordt verzorgd door trainers uit de praktijk",
    "Binnen dit vakgebied beschikken wij over ruime praktijkervaring",
    "Zo kies je een vervolgstap die past bij jouw rol, interesses en werksituatie",
    "Na het volledig afronden van deze training",
    "Na deze training heb je handvatten om:",
)
