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
    ### Deze training bieden we ook als bedrijfstraining   -- kop 3 (binnen Inleiding)
"""

from __future__ import annotations

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
BEDRIJFSTRAINING_KOP = "Deze training bieden we ook als bedrijfstraining voor jou en je team"
BEDRIJFSTRAINING_TEKST = (
    "De inhoud stemmen we dan af op jullie werksituatie, systemen en concrete vraagstukken, "
    "zodat de training direct aansluit op wat er binnen de organisatie speelt. Zo ontstaat "
    "een gerichte en praktische training waarmee je de volgende dag direct aan de slag kunt."
)

# Kopje 3 -- Modules: openingszin vóór de modulelijst.
MODULES_OPENING = (
    "Tijdens {aanduiding} komen in basis onderstaande onderwerpen aan bod. "
    "Afhankelijk van ontwikkelingen op het vakgebied, kan de feitelijke trainingsinhoud "
    "hier echter van afwijken. Bel ons gerust voor meer informatie over de actuele inhoud."
)

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


def modules_opening(naam: str) -> str:
    """De openingszin van kopje Modules, met een lopende aanduiding van de training.

    Draait op de nieuwe titel, zodat er nooit "de Training Opleiding PHP Professional"
    ontstaat: die begint na normalisatie met een toegestaan soortwoord.
    """
    titel = nieuwe_titel(naam)
    if not titel:
        return MODULES_OPENING.format(aanduiding="deze training")
    return MODULES_OPENING.format(aanduiding=f"de {titel}")

# Kopje 6 -- Aanpak: twee vaste alinea's; de schrijver levert alleen de [....]-invulling.
AANPAK_ALINEA_1 = (
    "De training is interactief en praktijkgericht opgezet. Je werkt actief aan herkenbare "
    "situaties, met veel ruimte voor vragen en eigen voorbeelden. Door te oefenen, bespreken "
    "en reflecteren ervaar je hoe {invulling}."
)
AANPAK_ALINEA_2 = (
    "De training wordt verzorgd door trainers uit de praktijk, die ervaring hebben in "
    "verschillende organisatiecontexten. We houden altijd rekening met jouw verwachtingen, "
    "zodat de training aansluit bij wat voor jou relevant is."
)
AANPAK_FALLBACK = "je dit toepast in de praktijk"

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
    "Binnen dit vakgebied beschikken wij over ruime praktijkervaring en specialistische "
    "kennis. Zoek je meer diepgang of een andere insteek? Neem gerust contact met ons op "
    "voor een vrijblijvende verkenning. We denken graag met je mee."
)
VERVOLG_ALINEA_2 = (
    "Er zijn verschillende vervolgtrainingen die aansluiten op specifieke onderwerpen, "
    "toepassingen en werkcontexten."
)
# Aankondiging boven één ongegroepeerde lijst. Levert de retrieval groepen met een
# eigen intro-zin (zoals in het goud), dan gebruikt de code die in plaats hiervan.
VERVOLG_LIJST_INTRO = "Zo bieden we onder andere:"
# Staat niet in het template, wel in de schrijfspec én in alle zes al herschreven
# trainingen. Bewust behouden; zet op "" om hem te laten vervallen.
VERVOLG_AFSLUITER = (
    "Zo kies je een vervolgstap die past bij jouw rol, interesses en werksituatie. Wil je "
    "verder verdiepen, verbreden of juist werken aan een specifieke vraag of eigen casus "
    "binnen je organisatie, dan denken we graag met je mee. Neem gerust contact met ons op "
    "om te verkennen welke vorm van training het beste aansluit bij jouw praktijk."
)

# Kopje 10 -- Certificatie: volledig vast.
CERTIFICATIE = (
    "Na het volledig afronden van deze training ontvang je een certificaat van deelname."
)
