"""
rewrite_output.py
=================
Zet het herschreven document om naar (a) de `content`-JSON die het CMS in gaat en
(b) een leesbaar document met de kopstructuur uit het template.

De bron-`content` heeft elf sleutels, waarvan er tien exact op de tien kopjes van
`Template trainingen nieuwe opbouw.md` mappen (zie `sjabloon.KOPJES`):

    summary          <- Overzicht             (platte tekst)
    intro            <- Inleiding             (<p> + <h3>-bedrijfstrainingblok)
    modules          <- Modules               (<p> + geneste <ul>)
    target_audience  <- Doelgroep             (<p>)
    prior_knowledge  <- Voorkennis            (<p>)
    setup            <- Aanpak                (<p>)
    objectives       <- Doelen                (<p> + <ul>)
    follow_up        <- Vervolgstappen        (<p> + <ul> + <p>)
    summary_edudex   <- Kortste omschrijving  (platte tekst)
    certification    <- Certificatie          (<p>, vaste tekst)
    days             <- ongewijzigd uit de bron

De HTML-vorm is afgekeken van de trainingen die al in de nieuwe stijl staan
(`herschreven=1`): platte tekst voor `summary`/`summary_edudex`, de rest HTML, en het
programma met geneste `<ul>` -- geen `<h3>` per module.
"""

from __future__ import annotations

import html
import re
from typing import Any

import sjabloon

# volgorde van de sleutels in de output, gelijk aan die van de bron
CONTENT_KEYS = ("days", "intro", "setup", "modules", "summary", "follow_up",
                "objectives", "certification", "summary_edudex", "prior_knowledge",
                "target_audience")


def _esc(tekst: Any) -> str:
    """Tekst -> HTML-veilige tekst. Alleen op de inhoud, nooit op de opmaak."""
    return html.escape(str(tekst or "").strip(), quote=False)


def _blokken(tekst: Any) -> list[str]:
    """De invoer van de schrijver: lege regels scheiden de alinea's. Blijft op `\\n\\n`."""
    return [b.strip() for b in str(tekst or "").split("\n\n") if b.strip()]


def _paragrafen(tekst: Any) -> str:
    """Platte tekst met lege regels -> losse <p>-blokken."""
    return "".join(f"<p>{_esc(b)}</p>" for b in _blokken(tekst))


def _lijst(items) -> str:
    regels = [f"<li>{_esc(i)}</li>" for i in items if str(i or "").strip()]
    return "<ul>" + "".join(regels) + "</ul>"


# ---------------------------------------------------------------------------
# Per kopje: platte tekst / structuur -> HTML
# ---------------------------------------------------------------------------

def render_inleiding(tekst: Any) -> str:
    """Kopje 2: de geschreven inleiding + het vaste bedrijfstrainingblok onder een <h3>."""
    return "".join([
        _paragrafen(tekst),
        f"<h3>{_esc(sjabloon.BEDRIJFSTRAINING_KOP)}</h3>",
        f"<p>{_esc(sjabloon.BEDRIJFSTRAINING_TEKST)}</p>",
    ])


def render_modules(opening: str, modules: list[dict]) -> str:
    """Kopje 3: openingszin + geneste <ul> (module -> sub-bullets).

    De opening gaat door `_paragrafen` en niet door één vaste <p>: sinds reviewronde 2 staat
    de NB in een eigen alinea (`sjabloon.MODULES_NB_*`), en dat is in het CMS gewoon een
    tweede <p>-blok.
    """
    delen = [_paragrafen(opening), "<ul>"]
    for module in modules or []:
        titel = _esc(module.get("titel", ""))
        bullets = [b for b in (module.get("bullets") or []) if str(b or "").strip()]
        if not titel and not bullets:
            continue
        delen.append(f"<li>{titel}")
        if bullets:
            delen.append(_lijst(bullets))
        delen.append("</li>")
    delen.append("</ul>")
    return "".join(delen)


def render_aanpak(tekst: Any) -> str:
    """Kopje 6: alinea's als <p>, met de oude cursiefmarkering omgezet naar aanhalingstekens.

    De nadruk op "kennis" en "toepassing binnen jouw organisatie en werksituatie" zit sinds
    augustus 2026 in de tekst zelf (`sjabloon.AANPAK_ALINEA_2`), dus voor nieuwe documenten
    doet dit kopje niets bijzonders meer. Documenten van vóór die wissel dragen nog `*...*`;
    die krijgen hier dezelfde aanhalingstekens in plaats van letterlijke sterretjes.

    Eerst escapen, dan omzetten: `html.escape` raakt sterretjes niet, dus in die volgorde kan
    brontekst geen tag binnensmokkelen.
    """
    return "".join(f"<p>{sjabloon.verquote_cursief(_esc(b))}</p>" for b in _blokken(tekst))


def render_doelen(intro: str, bullets: list[str]) -> str:
    """Kopje 7: vaste introzin + <ul> met de doelen."""
    return f"<p>{_esc(intro)}</p>{_lijst(bullets)}"


def bruikbare_groepen(groepen: list[dict] | None) -> list[dict]:
    """De groepen die een eigen introzin verdienen.

    Een intro kondigt een richting aan; met minder dan `sjabloon.MIN_TITELS_PER_GROEP`
    trainingen eronder leest dat als een fout (reviewronde 4). De pipeline snoeit zulke
    groepen al weg in `rewrite_trainings.snoei_groepen`, waar ook de titellijst meeloopt.
    Hier staat het nog een keer omdat er documenten op schijf liggen van vóór die regel: zo
    kan geen enkele weergave -- markdown, HTML of CMS -- er alsnog een tonen.
    """
    return [g for g in (groepen or [])
            if len([t for t in (g.get("titels") or []) if str(t or "").strip()])
            >= sjabloon.MIN_TITELS_PER_GROEP]


def render_vervolgstappen(alineas: list[str], titels: list[str], afsluiter: str,
                          groepen: list[dict] | None = None) -> str:
    """Kopje 8: vaste alinea's + de catalogustitels + afsluiter.

    Twee vormen. Leverde de retrieval `groepen` ([{intro, titels}]), dan krijgt elke groep
    een eigen intro-zin boven zijn lijst -- zo staat het in de al herschreven trainingen.
    Zonder groepen valt het terug op één lijst onder de vaste aankondiging.
    """
    delen = [f"<p>{_esc(a)}</p>" for a in alineas if str(a or "").strip()]
    schone_groepen = bruikbare_groepen(groepen)
    if schone_groepen:
        for groep in schone_groepen:
            intro = str(groep.get("intro") or "").strip() or sjabloon.VERVOLG_LIJST_INTRO
            delen.append(f"<p>{_esc(intro)}</p>{_lijst(groep['titels'])}")
    elif titels:
        delen.append(f"<p>{_esc(sjabloon.VERVOLG_LIJST_INTRO)}</p>{_lijst(titels)}")
    if str(afsluiter or "").strip():
        delen.append(f"<p>{_esc(afsluiter)}</p>")
    return "".join(delen)


# ---------------------------------------------------------------------------
# Document -> CMS-content
# ---------------------------------------------------------------------------

# Per kopje: hoe ziet dat ene CMS-veld eruit? Zelfde renderers als `document_to_content`,
# maar dan voor één veld tegelijk. Nodig om een goedgekeurde actualisering door te voeren in
# een training die verder ongemoeid blijft: dan wil je precies dat ene veld opnieuw
# renderen en elk ander veld byte-voor-byte laten staan.
_VELD_RENDER = {
    "overzicht": lambda w, ctx: str(w or "").strip(),
    "kortste_omschrijving": lambda w, ctx: str(w or "").strip(),
    "inleiding": lambda w, ctx: render_inleiding(w),
    "doelgroep": lambda w, ctx: _paragrafen(w),
    "voorkennis": lambda w, ctx: _paragrafen(w or sjabloon.VOORKENNIS_FALLBACK),
    "doelen": lambda w, ctx: render_doelen(sjabloon.DOELEN_INTRO, list(w or [])),
    "modules": lambda w, ctx: render_modules(
        ctx.get("modules_opening") or sjabloon.modules_opening(ctx.get("titel", "")),
        (w or {}).get("modules") if isinstance(w, dict) else (w or [])),
}

VELD_NAAR_CMS = {k.veld: k.cms for k in sjabloon.KOPJES}


def render_veld(veld: str, waarde: Any, ctx: dict | None = None) -> tuple[str, str]:
    """Eén kopje -> (CMS-sleutel, CMS-waarde). Werpt KeyError bij een onbekend kopje."""
    if veld not in _VELD_RENDER:
        raise KeyError(f"kopje {veld!r} is niet los te renderen; kies uit "
                       f"{sorted(_VELD_RENDER)}")
    return VELD_NAAR_CMS[veld], _VELD_RENDER[veld](waarde, ctx or {})


# ---------------------------------------------------------------------------
# Vaste teksten verversen in bestaande content
# ---------------------------------------------------------------------------

_H3_BLOK_RE = re.compile(r"\s*<h3>.*", re.S | re.I)
# De modulelijst begint bij de eerste <ul>; alles ervóór is de opening van het kopje.
_EERSTE_UL_RE = re.compile(r"<ul\b", re.I)
# De invulling loopt tot de eerste punt: "... ervaar je hoe |je XML in de praktijk toepast|."
# Niet tot het eind van de alinea -- daar staat alinea 2 achteraan geplakt.
_INVULLING_RE = re.compile(r"ervaar je hoe\s+([^.]+)\.", re.I)

# Blok-elementen op het hoogste niveau van een CMS-veld. De <ul>'s van Vervolgstappen zijn
# platte titellijsten zonder nesting, dus een niet-gulzige match is hier veilig.
_TOP_BLOK_RE = re.compile(r"<p\b.*?</p>|<ul\b.*?</ul>|<ol\b.*?</ol>", re.S | re.I)

# Vaste alinea's van het kopje Vervolgstappen, huidig én vervallen. Herkend op hun eerste
# woorden, want de staart van zo'n alinea is vaker met de hand bijgeschaafd dan de kop.
#
# De "Wil je je na deze training …"-opener staat er met vraagteken en al: dat maakt hem
# precies genoeg. 48 van de 78 bestaande trainingen openen er hun Vervolgstappen mee en die
# alinea doet exact wat VERVOLG_ALINEA_1/2 nu doen. Zonder vraagteken zou de prefix ook
# groep-intro's raken die wél inhoud dragen ("Wil je je na deze training verder verdiepen in
# digitale autonomie, …"), en die horen te blijven staan.
_VERVOLG_VASTE_OPENINGEN = (
    "Binnen dit expertisegebied beschikken wij",
    "Binnen dit vakgebied beschikken wij",
    "Binnen dit vakgebied hebben we ruime praktijkervaring",
    "Er zijn verschillende vervolgtrainingen",
    "Wil je je na deze training verder verdiepen of verbreden?",
    "Zo kies je een vervolgstap",
    "Neem gerust contact met ons op om te verkennen welke vorm",
    "Neem daarnaast ook gerust contact met ons op om te verkennen welke vorm",
)


# Witruimte rond de blokstructuur, in drie vormen. Geen ervan zit tussen twee woorden, dus
# geen ervan draagt betekenis:
#   1. tussen twee tags           "</p>\n\n<p>"        -> "</p><p>"
#   2. vóór een geneste lijst     "<li>Titel\n  <ul>"  -> "<li>Titel<ul>"
#   3. vóór een sluitende blok-tag "bullet\n</li>"     -> "bullet</li>"
_WITRUIMTE_REGELS = (
    (re.compile(r">\s+<"), "><"),
    (re.compile(r"\s+<(ul|ol)\b", re.I), r"<\1"),
    (re.compile(r"\s+</(li|ul|ol|p|h[1-6])>", re.I), r"</\1>"),
)


def _compacte_html(fragment: str) -> str:
    """Haalt de witregels rond de blok-tags weg; de tekst zelf blijft ongemoeid.

    Nodig omdat een deel van de content nog uit een eerdere generatie van deze code komt, die
    `</p>\\n\\n<p>` schreef. Het CMS maakt daar extra witruimte van in plaats van gewone
    paragraafspacing, en geen van de 78 trainingen die al in de nieuwe stijl staan heeft ook
    maar één newline.
    """
    tekst = fragment or ""
    for regel, vervanging in _WITRUIMTE_REGELS:
        tekst = regel.sub(vervanging, tekst)
    return tekst.strip()


def _tekst_uit(html_fragment: str) -> str:
    """Ruwe HTML -> platte tekst, voor het herkennen van vaste alinea's."""
    plat = re.sub(r"<[^>]+>", "", html_fragment or "")
    return re.sub(r"\s+", " ", html.unescape(plat)).strip()


def _top_blokken(fragment: str) -> list[str]:
    """HTML -> lijst van blok-elementen op het hoogste niveau.

    Splitst op de blokgrens en niet op een witregel. Dat scheelt niet alleen netheid: de
    content die in het CMS staat bevat geen enkele newline (nagemeten over alle 78 trainingen
    in `herschreven/goud/`), dus een split op `\\n\\n` levert daar één blok op en laat de
    vervanging van vaste teksten volledig langs zijn doel schieten.

    Tekst tussen de blokken -- losse tekst zonder tag -- blijft als eigen blok bewaard; hem
    stilzwijgend laten vallen zou inhoud wissen.
    """
    blokken: list[str] = []
    laatste = 0
    for m in _TOP_BLOK_RE.finditer(fragment or ""):
        tussen = (fragment or "")[laatste:m.start()].strip()
        if tussen:
            blokken.append(tussen)
        blokken.append(m.group(0))
        laatste = m.end()
    staart = (fragment or "")[laatste:].strip()
    if staart:
        blokken.append(staart)
    return blokken


def ververs_vaste_teksten(content: dict, titel: str,
                          modules_nb: str = sjabloon.MODULES_NB_DEFAULT) -> tuple[dict, list[str]]:
    """Zet de vaste sjabloonteksten in bestaande content terug op de actuele versie.

    Nodig voor het `overnemen`-pad. Dat pad laat `content` bewust byte-voor-byte staan, zodat
    een goede tekst niet slechter wordt van een herschrijfronde -- maar vaste teksten zijn
    geen tekst van de schrijver. Verandert het template, dan horen ze mee te veranderen, ook
    in een training die verder niemand aanraakt. Zonder deze stap belandt een training met de
    vorige generatie boilerplate ongewijzigd in het CMS.

    Alleen de deterministische delen worden vervangen; de geschreven tekst eromheen blijft
    staan. Geeft (nieuwe content, lijst met wat er is ververst) terug.
    """
    nieuw = dict(content or {})
    gewijzigd: list[str] = []

    def _zet(sleutel: str, waarde: str, label: str):
        waarde = _compacte_html(waarde)
        if nieuw.get(sleutel) != waarde and waarde:
            nieuw[sleutel] = waarde
            gewijzigd.append(label)

    # Witregels tussen de blok-tags weghalen, ook in de velden die verder niets vasts bevatten.
    # Anders houdt een training die met een eerdere versie van deze code is gegenereerd zijn
    # `</p>\n\n<p>` in precies die velden die we hier niet aanraken.
    for sleutel, waarde in list(nieuw.items()):
        if isinstance(waarde, str) and "\n" in waarde:
            compact = _compacte_html(waarde)
            if compact != waarde:
                nieuw[sleutel] = compact
                gewijzigd.append(f"witregels ({sleutel})")

    # Deelnamecertificaat: volledig vast, geen variabelen.
    _zet("certification", f"<p>{_esc(sjabloon.CERTIFICATIE)}</p>", "Deelnamecertificaat")

    # Inleiding: alles vanaf de <h3> is het bedrijfstrainingblok en wordt vervangen. De
    # geschreven inleiding erboven blijft ongemoeid.
    intro = nieuw.get("intro") or ""
    if "<h3" in intro.lower():
        geschreven = _H3_BLOK_RE.sub("", intro).strip()
        _zet("intro", "".join([
            geschreven,
            f"<h3>{_esc(sjabloon.BEDRIJFSTRAINING_KOP)}</h3>",
            f"<p>{_esc(sjabloon.BEDRIJFSTRAINING_TEKST)}</p>",
        ]), "bedrijfstrainingblok")

    # Modules: alleen de opening, niet de modulelijst eronder. De grens is de eerste <ul> en
    # niet de eerste </p>: de opening bestaat sinds reviewronde 2 uit twee alinea's (zin + NB),
    # en op een deel van de bestaande content staat er nog een derde <p> tussen. Alles vóór de
    # lijst wordt opnieuw opgebouwd, alles vanaf de lijst blijft byte-voor-byte staan.
    modules = nieuw.get("modules") or ""
    m_lijst = _EERSTE_UL_RE.search(modules)
    if m_lijst:
        opening = sjabloon.modules_opening(titel, modules_nb)
        _zet("modules", _paragrafen(opening) + modules[m_lijst.start():], "Modules-openingszin")

    # Aanpak: twee vaste alinea's rond één geschreven invulling. Die invulling zit achter
    # "ervaar je hoe ..." en is het enige wat we willen behouden.
    setup = nieuw.get("setup") or ""
    if setup:
        m = _INVULLING_RE.search(_tekst_uit(setup))
        invulling = (m.group(1).strip() if m else "") or sjabloon.AANPAK_FALLBACK
        if not m:
            gewijzigd.append("LET OP: Aanpak-invulling niet teruggevonden, fallback gebruikt")
        _zet("setup", render_aanpak(
            sjabloon.AANPAK_ALINEA_1.format(invulling=sjabloon.schoon_invulling(invulling))
            + "\n\n" + sjabloon.AANPAK_ALINEA_2), "Aanpak")

    # Vervolgstappen: de vaste alinea's ervoor en de vervallen afsluiter erachter. De
    # catalogustitels en hun groep-intro's blijven precies zoals ze staan.
    follow = nieuw.get("follow_up") or ""
    if follow and "<ul" in follow.lower():
        # Op inhoud filteren, niet op positie: een groep-intro staat soms boven zijn <ul> en
        # soms eronder, dus tellen levert de verkeerde grens op. Wat blijft staan is alles wat
        # geen vaste alinea is -- dus de titels en hun eigen intro's.
        staart = [b for b in _top_blokken(follow)
                  if not _tekst_uit(b).startswith(_VERVOLG_VASTE_OPENINGEN)]
        kop = [f"<p>{_esc(sjabloon.VERVOLG_ALINEA_1)}</p>",
               f"<p>{_esc(sjabloon.VERVOLG_ALINEA_2)}</p>"]
        _zet("follow_up", "".join(kop + staart), "Vervolgstappen-boilerplate")

    return nieuw, gewijzigd


def document_to_content(document: dict, source_content: dict | None = None) -> dict:
    """Het tien-kopjes-document -> de CMS-`content`-dict.

    `source_content` levert alleen wat we niet herschrijven (`days`). De certificatietekst
    komt uit het sjabloon, niet uit de bron -- die is in het template vastgelegd.
    """
    bron = source_content or {}
    modules = document.get("modules") or {}
    doelen = document.get("doelen") or {}
    vervolg = document.get("vervolgstappen") or {}

    content = {
        # platte tekst, precies zoals in de bron
        "summary": str(document.get("overzicht", "") or "").strip(),
        "summary_edudex": str(document.get("kortste_omschrijving", "") or "").strip(),
        # HTML
        "intro": render_inleiding(document.get("inleiding")),
        "modules": render_modules(modules.get("opening", ""), modules.get("modules") or []),
        "target_audience": _paragrafen(document.get("doelgroep")),
        "prior_knowledge": _paragrafen(document.get("voorkennis")),
        "setup": render_aanpak(document.get("aanpak")),
        "objectives": render_doelen(doelen.get("intro", ""), doelen.get("bullets") or []),
        "follow_up": render_vervolgstappen(vervolg.get("alineas") or [],
                                           vervolg.get("titels") or [],
                                           vervolg.get("afsluiter", ""),
                                           vervolg.get("groepen") or []),
        "certification": f"<p>{_esc(document.get('certificatie') or sjabloon.CERTIFICATIE)}</p>",
    }
    for sleutel in sjabloon.BEHOUDEN_UIT_BRON:
        if sleutel in bron:
            content[sleutel] = bron[sleutel]

    # zelfde sleutelvolgorde als de bron; onbekende extra sleutels blijven achteraan
    geordend = {k: content[k] for k in CONTENT_KEYS if k in content}
    geordend.update({k: v for k, v in content.items() if k not in geordend})
    return geordend


# ---------------------------------------------------------------------------
# Document -> leesbaar markdown (kopstructuur van het template)
# ---------------------------------------------------------------------------

def render_markdown(document: dict, titel: str) -> str:
    """Volledig document met de kopniveaus uit het template.

    Titel als kop 1, elk kopje als kop 2, het bedrijfstrainingblok als kop 3.
    Dit is wat de judge beoordeelt en wat een reviewer leest.
    """
    modules = document.get("modules") or {}
    doelen = document.get("doelen") or {}
    vervolg = document.get("vervolgstappen") or {}

    secties: dict[str, str] = {
        "overzicht": str(document.get("overzicht", "") or "").strip(),
        "inleiding": "\n\n".join(
            _blokken(document.get("inleiding"))
            + [f"### **{sjabloon.BEDRIJFSTRAINING_KOP}**", sjabloon.BEDRIJFSTRAINING_TEKST]),
        "doelgroep": str(document.get("doelgroep", "") or "").strip(),
        "voorkennis": str(document.get("voorkennis", "") or "").strip(),
        "aanpak": str(document.get("aanpak", "") or "").strip(),
        "kortste_omschrijving": str(document.get("kortste_omschrijving", "") or "").strip(),
        "certificatie": str(document.get("certificatie") or sjabloon.CERTIFICATIE),
    }

    regels = [modules.get("opening", ""), ""]
    for module in modules.get("modules") or []:
        regels.append(f"* {module.get('titel', '')}")
        regels += [f"  * {b}" for b in module.get("bullets") or []]
    secties["modules"] = "\n".join(regels).strip()

    secties["doelen"] = "\n\n".join([
        doelen.get("intro", ""),
        "\n".join(f"* {b}" for b in doelen.get("bullets") or []),
    ]).strip()

    vervolg_regels = list(vervolg.get("alineas") or [])
    groepen = bruikbare_groepen(vervolg.get("groepen"))
    if groepen:
        for groep in groepen:
            vervolg_regels.append(str(groep.get("intro") or "").strip()
                                  or sjabloon.VERVOLG_LIJST_INTRO)
            vervolg_regels.append("\n".join(f"* {t}" for t in groep["titels"]))
    elif vervolg.get("titels"):
        vervolg_regels.append(sjabloon.VERVOLG_LIJST_INTRO)
        vervolg_regels.append("\n".join(f"* {t}" for t in vervolg["titels"]))
    if str(vervolg.get("afsluiter") or "").strip():
        vervolg_regels.append(vervolg["afsluiter"])
    secties["vervolgstappen"] = "\n\n".join(vervolg_regels).strip()

    delen = [f"# {titel}".rstrip()]
    for kopje in sjabloon.KOPJES:
        delen.append(f"## {kopje.kop}\n\n{secties.get(kopje.veld, '').strip()}")
    return "\n\n---\n\n".join(delen) + "\n"


# De importer van Google Drive gokt zonder deze meta latin-1 en maakt dan van elke e-umlaut
# twee tekens. Eén regel, en `ë`/`é`/`ï` komen heel aan de andere kant.
_DOCS_HTML_KOP = '<html><head><meta charset="utf-8"><title>{titel}</title></head><body>'

# ---------------------------------------------------------------------------
# Opmaak van het Google Doc
# ---------------------------------------------------------------------------
# Deze stijl staat ALLEEN in de doc-HTML en nooit in de CMS-content: het CMS levert zijn eigen
# opmaak en zou van deze regels een tweede, botsende laag krijgen. Vandaar dat de attributen
# hier op het laatste moment in de fragmenten worden gezet, en niet in de renderers erboven.
#
# Twee dingen die je alleen merkt door het te uploaden:
#
# - **ruimte tussen alinea's moet als `margin-bottom` op elke <p> staan.** Docs zet "ruimte na
#   alinea" standaard op nul, dus zonder deze regel plakken alle alinea's van een kopje aan
#   elkaar tot één blok, en dat was precies de klacht na de eerste echte batch;
# - **tekengrootte en vet horen óók op een <span> binnen de kop.** Docs bewaart die twee als
#   tekenopmaak op de tekst zelf en niet als eigenschap van de alinea; dat is ook hoe de
#   HTML-export van Docs het schrijft. Zet je het alleen op de <h1>, dan kan de importer het
#   laten vallen en krijg je toch weer de standaard-kop.
ALINEA_RUIMTE = "10pt"
BULLET_RUIMTE = "4pt"

# kop -> (tekengrootte, gewicht, ruimte erboven). Kop 1 krijgt geen ruimte erboven: die staat
# bovenaan het document of vlak onder een horizontale lijn.
DOCS_KOPPEN = {
    "h1": ("20pt", "normal", "0"),
    "h2": ("16pt", "normal", "18pt"),
    "h3": ("14pt", "bold", "14pt"),
}

_H3_RE = re.compile(r"<h3>(.*?)</h3>", re.S | re.I)


def _ruimte(boven: str, onder: str) -> str:
    """Marges als losse eigenschappen, niet als `margin`-shorthand.

    Zo schrijft de HTML-export van Docs het zelf, en dat is de vorm waarvan we zeker weten dat
    de import hem terugleest. Alleen `margin` en niet ook `padding`: honoreert de importer ze
    allebei, dan staat er twee keer zoveel ruimte als bedoeld.
    """
    return f"margin-top:{boven};margin-bottom:{onder};"


def _docs_kop(niveau: str, inhoud: str) -> str:
    """Een kop met de opmaak op zowel de alinea als de tekst erin; zie de toelichting hierboven."""
    grootte, gewicht, boven = DOCS_KOPPEN[niveau]
    teken = f"font-size:{grootte};font-weight:{gewicht};"
    return (f'<{niveau} style="{teken}{_ruimte(boven, "6pt")}">'
            f'<span style="{teken}">{inhoud}</span></{niveau}>')


def _docs_opmaak(fragment: str) -> str:
    """Zet de doc-opmaak in een CMS-fragment.

    Losse `str.replace` op de kale tags kan hier omdat de fragmenten uit onze eigen renderers
    komen: die schrijven `<p>`, `<li>` en `<h3>` altijd zonder attributen, en alle tekst is
    door `_esc` gegaan, dus een `<p>` uit de brontekst bestaat niet.
    """
    fragment = _H3_RE.sub(lambda m: _docs_kop("h3", m.group(1)), fragment)
    return (fragment
            .replace("<p>", f'<p style="{_ruimte("0", ALINEA_RUIMTE)}">')
            .replace("<li>", f'<li style="{_ruimte("0", BULLET_RUIMTE)}">'))


def render_docs_html(content: dict, titel: str) -> str:
    """De CMS-`content` als één HTML-pagina, voor conversie naar een Google Doc.

    Zelfde structuur als `render_markdown`: titel als kop 1, elk kopje als kop 2, een
    horizontale lijn ertussen. Drive zet `<h1>`/`<h2>` om naar echte Docs-koppen, en daarmee
    krijgt de reviewer een werkende documentoverzicht-zijbalk -- de reden om HTML te uploaden
    in plaats van platte tekst.

    Draait op `content` en niet op het document, want `neem_over` levert geen document: een
    training op modus `overnemen` heeft alleen CMS-content. Een document-gebaseerde renderer
    zou precies de trainingen overslaan die al goede tekst hebben. Bijvangst is dat het doc
    letterlijk toont wat het CMS in gaat, en niet een tweede weergave daarvan.

    Lege kopjes krijgen wél hun kop: een reviewer moet kunnen zien dát er niets staat.
    """
    delen = [_docs_kop("h1", _esc(titel))]
    for kopje in sjabloon.KOPJES:
        waarde = content.get(kopje.cms) or ""
        # `summary` en `summary_edudex` staan als platte tekst in het CMS (Kopje.html is False)
        fragment = str(waarde) if kopje.html else _paragrafen(waarde)
        delen.append(_docs_kop("h2", _esc(kopje.kop)) + _docs_opmaak(fragment))
    return _DOCS_HTML_KOP.format(titel=_esc(titel)) + "<hr>".join(delen) + "</body></html>"


def content_naar_platte_tekst(content: dict, naam: str = "") -> dict:
    """Leesbare weergave per veld -- voor het review-tabblad en voor diffen met de bron."""
    from score_trainings import clean_text
    return {k: clean_text(v, naam) if isinstance(v, str) else v
            for k, v in content.items()}
